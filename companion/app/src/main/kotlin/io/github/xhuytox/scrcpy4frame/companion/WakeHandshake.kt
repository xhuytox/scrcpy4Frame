package io.github.xhuytox.scrcpy4frame.companion

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Random
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The device side of the UDP wake handshake (docs/CONTRACTS.md). A Frame asks with `wake`, gets a
 * single-use nonce, and answers with HMAC-SHA256 over it, using a key it gave the companion over
 * adb. Times are `SystemClock.elapsedRealtime()`. Not thread-safe: [WakeServer]'s thread owns it.
 */
class WakeHandshake(private val random: Random = SecureRandom()) {

    sealed interface Outcome {
        /** Send [line] back to the sender. */
        data class Reply(val line: String) : Outcome
        /** The sender proved it holds a key. */
        data object Woken : Outcome
        data object Ignored : Outcome
    }

    private val nonces = LinkedHashMap<String, Long>() // nonce -> issued at, oldest first

    fun handle(request: Protocol.WakeRequest, keys: List<ByteArray>, now: Long): Outcome {
        if (keys.isEmpty()) return Outcome.Ignored
        nonces.values.removeAll { now - it >= NONCE_TTL_MS }
        val nonce = request.nonce
        if (nonce == null) {
            if (nonces.size >= MAX_PENDING) nonces.remove(nonces.keys.first())
            val fresh = newNonce()
            nonces[fresh] = now
            return Outcome.Reply(Protocol.challenge(fresh))
        }
        val given = request.mac?.let(::hexToBytes) ?: return Outcome.Ignored
        if (nonces.remove(nonce) == null) return Outcome.Ignored // unknown, expired or used
        return if (keys.any { MessageDigest.isEqual(mac(it, nonce), given) }) Outcome.Woken else Outcome.Ignored
    }

    private fun newNonce(): String {
        val bytes = ByteArray(NONCE_BYTES)
        random.nextBytes(bytes)
        return bytes.joinToString("") { "%02x".format(it) }
    }

    companion object {
        const val NONCE_TTL_MS = 30_000L
        const val MAX_PENDING = 8
        private const val NONCE_BYTES = 16
        private const val MAC_PREFIX = "scrcpy4frame-wake:"

        fun mac(key: ByteArray, nonce: String): ByteArray {
            val hmac = Mac.getInstance("HmacSHA256")
            hmac.init(SecretKeySpec(key, "HmacSHA256"))
            return hmac.doFinal((MAC_PREFIX + nonce).toByteArray(Charsets.UTF_8))
        }

        private fun hexToBytes(hex: String): ByteArray? {
            if (hex.length % 2 != 0 || hex.length > 128) return null
            return ByteArray(hex.length / 2) { i ->
                val hi = Character.digit(hex[2 * i], 16)
                val lo = Character.digit(hex[2 * i + 1], 16)
                if (hi < 0 || lo < 0) return null
                ((hi shl 4) or lo).toByte()
            }
        }
    }
}
