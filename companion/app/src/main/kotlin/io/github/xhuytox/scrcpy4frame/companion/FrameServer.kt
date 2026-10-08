package io.github.xhuytox.scrcpy4frame.companion

import android.net.LocalServerSocket
import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.system.Os
import android.system.OsConstants
import android.util.Log
import java.io.IOException
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * JSON-lines server on the abstract socket `@scrcpy4frame`, which the Frame reaches through
 * `adb forward tcp:0 localabstract:scrcpy4frame`. Only shell (adbd) and root peers are accepted.
 *
 * Each client gets a reader thread and a writer thread draining a bounded queue, so a stalled
 * client can't block [broadcast]. A client whose queue overflows is dropped.
 * [onCommand] runs on the client's reader thread with the client's id and returns an optional reply
 * line. After `bye` the server sends what is queued and hangs up, so the Frame knows it arrived.
 */
class FrameServer(
    private val helloProvider: () -> String,
    private val onCommand: (clientId: Int, command: Protocol.Command) -> String?,
    private val socketName: String = Protocol.SOCKET_NAME,
) {
    private val clients = CopyOnWriteArrayList<Client>()
    private val clientIds = AtomicInteger()
    private val lock = Any()
    private var acceptor: Acceptor? = null

    val hasClients: Boolean
        get() = clients.isNotEmpty()

    fun start() {
        synchronized(lock) {
            if (acceptor != null) return
            acceptor = Acceptor().also { it.start() }
        }
    }

    fun stop() {
        val a = synchronized(lock) {
            acceptor.also { acceptor = null }
        } ?: return
        a.shutdown()
        for (c in clients) c.close()
        clients.clear()
    }

    /** Lines over 1 MiB are dropped. */
    fun broadcast(line: String) {
        if (clients.isEmpty()) return
        val bytes = Protocol.frame(line) ?: run {
            Log.w(TAG, "dropping a line over ${Protocol.MAX_LINE_BYTES} bytes")
            return
        }
        for (c in clients) c.enqueue(bytes)
    }

    private inner class Acceptor : Thread("s4f-accept") {
        @Volatile private var stopped = false
        @Volatile private var server: LocalServerSocket? = null

        init {
            isDaemon = true
        }

        fun shutdown() {
            stopped = true
            interrupt()
            server?.let(::closeServer)
            // Some Android versions don't wake accept() on close. Poke it with a connection if needed.
            Thread({
                try {
                    join(500)
                } catch (_: InterruptedException) {
                }
                if (isAlive) {
                    try {
                        LocalSocket().use { it.connect(LocalSocketAddress(socketName)) }
                    } catch (_: IOException) {
                    }
                }
            }, "s4f-accept-wake").apply { isDaemon = true }.start()
        }

        override fun run() {
            var backoffMs = INITIAL_BACKOFF_MS
            while (!stopped) {
                val srv = try {
                    LocalServerSocket(socketName)
                } catch (e: IOException) {
                    // Usually EADDRINUSE while a previous instance still holds the name.
                    Log.w(TAG, "bind @$socketName failed (${e.message}); retrying in $backoffMs ms")
                    if (!sleepQuietly(backoffMs)) break
                    backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
                    continue
                }
                server = srv
                if (stopped) { // stop() may have run before `server` was visible to it.
                    closeServer(srv)
                    break
                }
                backoffMs = INITIAL_BACKOFF_MS
                Log.i(TAG, "listening on @$socketName")
                acceptLoop(srv)
                closeServer(srv)
                server = null
                if (!stopped && !sleepQuietly(INITIAL_BACKOFF_MS)) break
            }
            Log.i(TAG, "accept thread finished")
        }

        private fun acceptLoop(srv: LocalServerSocket) {
            while (!stopped) {
                val socket = try {
                    srv.accept()
                } catch (e: IOException) {
                    if (!stopped) Log.w(TAG, "accept failed; rebinding", e)
                    return
                }
                if (stopped) {
                    closeQuietly(socket)
                    return
                }
                handleNewConnection(socket)
            }
        }

        private fun handleNewConnection(socket: LocalSocket) {
            val uid = try {
                socket.peerCredentials.uid
            } catch (e: IOException) {
                -1
            }
            if (!Protocol.isAllowedPeer(uid)) {
                Log.w(TAG, "rejecting connection from uid $uid")
                closeQuietly(socket)
                return
            }
            val client = try {
                Client(socket, clientIds.incrementAndGet())
            } catch (e: IOException) {
                closeQuietly(socket)
                return
            }
            // hello goes into the queue before the client becomes visible to broadcast().
            val hello = try {
                helloProvider()
            } catch (e: RuntimeException) {
                Log.e(TAG, "hello failed", e)
                Protocol.error("internal error")
            }
            val helloBytes = Protocol.frame(hello)
            if (helloBytes == null) {
                closeQuietly(socket)
                return
            }
            client.enqueue(helloBytes)
            clients.add(client)
            if (stopped) {
                client.close()
                return
            }
            Log.i(TAG, "client ${client.id} connected (uid $uid)")
            client.start()
        }

        private fun sleepQuietly(ms: Long): Boolean = try {
            sleep(ms)
            !stopped
        } catch (_: InterruptedException) {
            false
        }
    }

    private inner class Client(private val socket: LocalSocket, val id: Int) {
        private val queue = LinkedBlockingQueue<ByteArray>(QUEUE_CAPACITY)
        private val closed = AtomicBoolean(false)
        private val output = socket.outputStream
        private val input = socket.inputStream
        private val writer = Thread(::writeLoop, "s4f-client-$id-w").apply { isDaemon = true }
        private val reader = Thread(::readLoop, "s4f-client-$id-r").apply { isDaemon = true }

        fun start() {
            writer.start()
            reader.start()
        }

        fun enqueue(bytes: ByteArray) {
            if (closed.get()) return
            if (!queue.offer(bytes)) {
                Log.w(TAG, "client $id is not reading; disconnecting it")
                close()
            }
        }

        /** Sends whatever is queued, then closes. */
        private fun closeAfterFlush() {
            if (!queue.offer(CLOSE_MARKER)) close()
        }

        private fun writeLoop() {
            try {
                while (!closed.get()) {
                    val bytes = queue.take()
                    if (bytes === CLOSE_MARKER) {
                        output.flush()
                        break
                    }
                    output.write(bytes)
                    if (queue.isEmpty()) output.flush()
                }
            } catch (_: InterruptedException) {
            } catch (e: IOException) {
                if (!closed.get()) Log.d(TAG, "client $id write failed: ${e.message}")
            } finally {
                close()
            }
        }

        private fun readLoop() {
            try {
                val lines = LineReader(input, Protocol.MAX_LINE_BYTES)
                while (!closed.get()) {
                    val line = lines.readLine() ?: break
                    if (line.isBlank()) continue
                    val command = Protocol.parseCommand(line)
                    val reply = try {
                        onCommand(id, command)
                    } catch (e: RuntimeException) {
                        Log.e(TAG, "command failed", e)
                        Protocol.error("internal error: ${e.javaClass.simpleName}")
                    }
                    reply?.let { Protocol.frame(it) }?.let(::enqueue)
                    if (command == Protocol.Command.Bye) {
                        closeAfterFlush()
                        return
                    }
                }
                close()
            } catch (e: LineReader.LineTooLongException) {
                Protocol.frame(Protocol.error("line exceeds ${Protocol.MAX_LINE_BYTES} bytes"))?.let(::enqueue)
                closeAfterFlush()
            } catch (e: IOException) {
                close()
            }
        }

        fun close() {
            if (!closed.compareAndSet(false, true)) return
            clients.remove(this)
            queue.clear()
            queue.offer(CLOSE_MARKER) // wakes the writer
            // close() alone may not unblock threads stuck in read() or write().
            try {
                socket.shutdownInput()
            } catch (_: IOException) {
            }
            try {
                socket.shutdownOutput()
            } catch (_: IOException) {
            }
            closeQuietly(socket)
            writer.interrupt()
            Log.i(TAG, "client $id disconnected")
        }
    }

    private companion object {
        const val TAG = "S4F.FrameServer"
        const val INITIAL_BACKOFF_MS = 250L
        const val MAX_BACKOFF_MS = 10_000L
        const val QUEUE_CAPACITY = 512
        val CLOSE_MARKER = ByteArray(0)

        fun closeServer(srv: LocalServerSocket) {
            try {
                Os.shutdown(srv.fileDescriptor, OsConstants.SHUT_RDWR)
            } catch (_: Exception) {
            }
            try {
                srv.close()
            } catch (_: IOException) {
            }
        }

        fun closeQuietly(socket: LocalSocket) {
            try {
                socket.close()
            } catch (_: IOException) {
            }
        }
    }
}
