package io.github.xhuytox.scrcpy4frame.companion

import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class WakeServerTest {

    private val key = ByteArray(32) { it.toByte() }

    private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }

    /**
     * Sends [line] until a reply of type [expect] comes back. The server binds on its own thread,
     * so the first datagrams can be lost, and a late answer to an earlier one is skipped.
     */
    private fun exchange(frame: DatagramSocket, port: Int, line: String, expect: String): JSONObject {
        val bytes = line.toByteArray(Charsets.UTF_8)
        val buffer = ByteArray(Protocol.MAX_WAKE_BYTES)
        repeat(40) {
            frame.send(DatagramPacket(bytes, bytes.size, InetAddress.getLoopbackAddress(), port))
            try {
                while (true) {
                    val reply = DatagramPacket(buffer, buffer.size)
                    frame.receive(reply)
                    val message = JSONObject(String(buffer, 0, reply.length, Charsets.UTF_8))
                    if (message.getString("type") == expect) return message
                }
            } catch (_: SocketTimeoutException) {
                // Not listening yet.
            }
        }
        throw AssertionError("no $expect on udp:$port")
    }

    @Test(timeout = 20_000)
    fun aFrameWithAKnownKeyWakesTheDeviceOverUdp() {
        val port = DatagramSocket(0).use { it.localPort }
        val woken = CountDownLatch(1)
        val server = WakeServer(keys = { listOf(key) }, onWake = { woken.countDown() }, port = port)
        server.start()
        try {
            DatagramSocket().use { frame ->
                frame.soTimeout = 250
                val nonce = exchange(frame, port, """{"type":"wake"}""", "challenge").getString("nonce")
                val mac = hex(WakeHandshake.mac(key, nonce))
                exchange(frame, port, """{"type":"wake","nonce":"$nonce","mac":"$mac"}""", "awake")
            }
            assertTrue(woken.await(1, TimeUnit.SECONDS))
        } finally {
            server.stop()
        }
    }
}
