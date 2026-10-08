package io.github.xhuytox.scrcpy4frame.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random

class WakeHandshakeTest {

    private val key = ByteArray(32) { it.toByte() }
    private val otherKey = ByteArray(32) { 7 }

    private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }

    /** Asks for a challenge and returns its nonce. */
    private fun challenge(h: WakeHandshake, now: Long, keys: List<ByteArray> = listOf(key)): String {
        val outcome = h.handle(Protocol.WakeRequest(null, null), keys, now)
        assertTrue(outcome is WakeHandshake.Outcome.Reply)
        val o = JSONObject((outcome as WakeHandshake.Outcome.Reply).line)
        assertEquals("challenge", o.getString("type"))
        return o.getString("nonce")
    }

    private fun answer(h: WakeHandshake, nonce: String, mac: String, now: Long, keys: List<ByteArray> = listOf(key)) =
        h.handle(Protocol.WakeRequest(nonce, mac), keys, now)

    @Test
    fun macMatchesTheFrameSide() {
        // Same vector as tests/test_wake.py.
        assertEquals(
            "2912d1a5bebfed62d2685a0b1c529aabdc7e3394494eb02ca48b47dd1281e6b4",
            hex(WakeHandshake.mac(key, "000102030405060708090a0b0c0d0e0f")),
        )
    }

    @Test
    fun aFrameWithAKnownKeyWakesTheDevice() {
        val h = WakeHandshake(Random(1))
        val nonce = challenge(h, 1_000)
        assertEquals(32, nonce.length)
        val mac = hex(WakeHandshake.mac(key, nonce))
        assertEquals(WakeHandshake.Outcome.Woken, answer(h, nonce, mac, 2_000, listOf(otherKey, key)))
    }

    @Test
    fun aNonceWorksOnce() {
        val h = WakeHandshake(Random(1))
        val nonce = challenge(h, 1_000)
        val mac = hex(WakeHandshake.mac(key, nonce))
        assertEquals(WakeHandshake.Outcome.Woken, answer(h, nonce, mac, 2_000))
        assertEquals(WakeHandshake.Outcome.Ignored, answer(h, nonce, mac, 3_000))
    }

    @Test
    fun wrongKeysUnknownOrExpiredNoncesAndBadMacsAreIgnored() {
        val h = WakeHandshake(Random(1))
        val nonce = challenge(h, 1_000)
        assertEquals(WakeHandshake.Outcome.Ignored, answer(h, nonce, hex(WakeHandshake.mac(otherKey, nonce)), 2_000))
        // A wrong answer used the nonce up.
        assertEquals(WakeHandshake.Outcome.Ignored, answer(h, nonce, hex(WakeHandshake.mac(key, nonce)), 2_000))

        val unknown = "00".repeat(16)
        assertEquals(WakeHandshake.Outcome.Ignored, answer(h, unknown, hex(WakeHandshake.mac(key, unknown)), 2_000))

        val late = challenge(h, 10_000)
        val lateMac = hex(WakeHandshake.mac(key, late))
        assertEquals(WakeHandshake.Outcome.Ignored, answer(h, late, lateMac, 10_000 + WakeHandshake.NONCE_TTL_MS))

        val n = challenge(h, 50_000)
        assertEquals(WakeHandshake.Outcome.Ignored, answer(h, n, "not hex", 50_001))
        assertEquals(WakeHandshake.Outcome.Ignored, h.handle(Protocol.WakeRequest(n, null), listOf(key), 50_001))
    }

    @Test
    fun withoutKeysNothingIsAnswered() {
        val h = WakeHandshake(Random(1))
        assertEquals(WakeHandshake.Outcome.Ignored, h.handle(Protocol.WakeRequest(null, null), emptyList(), 1_000))
    }

    @Test
    fun onlyTheNewestChallengesStayValid() {
        val h = WakeHandshake(Random(1))
        val first = challenge(h, 1_000)
        repeat(WakeHandshake.MAX_PENDING) { challenge(h, 1_000) }
        assertEquals(WakeHandshake.Outcome.Ignored, answer(h, first, hex(WakeHandshake.mac(key, first)), 2_000))
        val last = challenge(h, 1_000)
        assertEquals(WakeHandshake.Outcome.Woken, answer(h, last, hex(WakeHandshake.mac(key, last)), 2_000))
    }
}
