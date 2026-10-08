package io.github.xhuytox.scrcpy4frame.companion

import android.os.SystemClock
import android.util.Log
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress

/**
 * UDP listener on [Protocol.WAKE_PORT]. The Frame app sends `wake` here while it wants the device
 * and adb can't reach it, because Wireless debugging is off. A sender that proves it holds one of
 * [keys] (see [WakeHandshake]) gets [onWake] called on this server's thread.
 */
class WakeServer(
    private val keys: () -> List<ByteArray>,
    private val onWake: () -> Unit,
    private val port: Int = Protocol.WAKE_PORT,
) {
    private val lock = Any()
    private var receiver: Receiver? = null

    fun start() {
        synchronized(lock) {
            if (receiver != null) return
            receiver = Receiver().also { it.start() }
        }
    }

    fun stop() {
        val r = synchronized(lock) {
            receiver.also { receiver = null }
        } ?: return
        r.shutdown()
    }

    private inner class Receiver : Thread("s4f-wake") {
        @Volatile private var stopped = false
        @Volatile private var socket: DatagramSocket? = null

        init {
            isDaemon = true
        }

        fun shutdown() {
            stopped = true
            interrupt()
            socket?.close() // wakes receive()
        }

        override fun run() {
            val handshake = WakeHandshake()
            var backoffMs = INITIAL_BACKOFF_MS
            while (!stopped) {
                val s = try {
                    // Not `apply`: in there `port` would be DatagramSocket.getPort(), -1 while unconnected.
                    DatagramSocket(null).also {
                        it.reuseAddress = true
                        it.bind(InetSocketAddress(port))
                    }
                } catch (e: IOException) {
                    Log.w(TAG, "bind udp:$port failed (${e.message}); retrying in $backoffMs ms")
                    if (!sleepQuietly(backoffMs)) break
                    backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
                    continue
                }
                socket = s
                if (stopped) { // shutdown() may have run before `socket` was visible to it.
                    s.close()
                    break
                }
                backoffMs = INITIAL_BACKOFF_MS
                Log.i(TAG, "listening on udp:$port")
                receiveLoop(s, handshake)
                socket = null
                s.close()
                if (!stopped && !sleepQuietly(INITIAL_BACKOFF_MS)) break
            }
            Log.i(TAG, "wake thread finished")
        }

        private fun receiveLoop(s: DatagramSocket, handshake: WakeHandshake) {
            // One byte more than allowed, so an oversized datagram shows up as too long.
            val buffer = ByteArray(Protocol.MAX_WAKE_BYTES + 1)
            while (!stopped) {
                val packet = DatagramPacket(buffer, buffer.size)
                try {
                    s.receive(packet)
                } catch (e: IOException) {
                    if (!stopped) Log.w(TAG, "receive failed; rebinding", e)
                    return
                }
                if (packet.length > Protocol.MAX_WAKE_BYTES) continue
                val request = Protocol.parseWake(String(buffer, 0, packet.length, Charsets.UTF_8)) ?: continue
                val reply = when (val outcome = handshake.handle(request, keys(), SystemClock.elapsedRealtime())) {
                    is WakeHandshake.Outcome.Reply -> outcome.line
                    WakeHandshake.Outcome.Woken -> {
                        Log.i(TAG, "woken by ${packet.address.hostAddress}")
                        onWake()
                        Protocol.awake()
                    }
                    WakeHandshake.Outcome.Ignored -> {
                        Log.d(TAG, "ignored a wake from ${packet.address.hostAddress}")
                        null
                    }
                } ?: continue
                val bytes = reply.toByteArray(Charsets.UTF_8)
                try {
                    s.send(DatagramPacket(bytes, bytes.size, packet.socketAddress))
                } catch (e: IOException) {
                    Log.d(TAG, "reply to ${packet.address.hostAddress} failed: ${e.message}")
                }
            }
        }

        private fun sleepQuietly(ms: Long): Boolean = try {
            sleep(ms)
            !stopped
        } catch (_: InterruptedException) {
            false
        }
    }

    private companion object {
        const val TAG = "S4F.WakeServer"
        const val INITIAL_BACKOFF_MS = 250L
        const val MAX_BACKOFF_MS = 10_000L
    }
}
