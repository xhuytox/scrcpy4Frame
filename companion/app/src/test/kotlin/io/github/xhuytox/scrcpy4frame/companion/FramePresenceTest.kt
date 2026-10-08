package io.github.xhuytox.scrcpy4frame.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FramePresenceTest {

    @Test
    fun nobodyIsPresentAtFirst() {
        val p = FramePresence(timeoutMs = 60_000)
        assertFalse(p.isPresent(1_000))
        assertNull(p.expiresAt(1_000))
    }

    @Test
    fun aClientIsPresentUntilItGoesQuietForTheTimeout() {
        val p = FramePresence(timeoutMs = 60_000)
        assertTrue("first contact starts presence", p.heard(1, 1_000))
        assertFalse("later contact doesn't start it again", p.heard(1, 16_000))
        assertEquals(76_000L, p.expiresAt(16_000))
        assertTrue(p.isPresent(75_999))
        assertFalse(p.isPresent(76_000))
        assertTrue("contact after a timeout starts presence again", p.heard(1, 80_000))
    }

    @Test
    fun aWakeCountsLikeContact() {
        val p = FramePresence(timeoutMs = 60_000)
        assertTrue(p.woken(1_000))
        assertFalse(p.woken(5_000))
        assertEquals(65_000L, p.expiresAt(10_000))
        assertFalse(p.isPresent(65_000))
    }

    @Test
    fun theLatestContactOfAnySourceWins() {
        val p = FramePresence(timeoutMs = 60_000)
        p.heard(1, 1_000)
        p.woken(20_000)
        p.heard(2, 10_000)
        assertEquals(80_000L, p.expiresAt(30_000))
        assertTrue(p.isPresent(79_000))
    }

    @Test
    fun byeEndsPresenceAtOnceAlsoForEarlierWakes() {
        val p = FramePresence(timeoutMs = 60_000)
        p.woken(1_000)
        p.heard(1, 2_000)
        p.bye(1)
        assertFalse(p.isPresent(2_001))
    }

    @Test
    fun byeLeavesOtherClientsPresent() {
        val p = FramePresence(timeoutMs = 60_000)
        p.heard(1, 1_000)
        p.heard(2, 2_000)
        p.bye(1)
        assertEquals(62_000L, p.expiresAt(3_000))
    }
}
