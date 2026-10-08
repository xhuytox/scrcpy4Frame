package io.github.xhuytox.scrcpy4frame.companion

/**
 * Whether the scrcpy4Frame app is running on a Frame nearby. A Frame counts as present for
 * [timeoutMs] after one of its socket clients last sent a line or after it last woke the device
 * over UDP. The Frame pings every 15 s while connected. Times are `SystemClock.elapsedRealtime()`.
 */
class FramePresence(private val timeoutMs: Long = TIMEOUT_MS) {
    private val clients = HashMap<Int, Long>() // client id -> last heard
    private var wokenAt: Long? = null

    /** Returns true if this makes a Frame present when none was. */
    @Synchronized
    fun heard(clientId: Int, now: Long): Boolean {
        val was = isPresent(now)
        clients[clientId] = now
        return !was
    }

    /** Returns true if this makes a Frame present when none was. */
    @Synchronized
    fun woken(now: Long): Boolean {
        val was = isPresent(now)
        wokenAt = now
        return !was
    }

    /** The app closed or forgot this device. Earlier wakes came from it too, so they stop counting. */
    @Synchronized
    fun bye(clientId: Int) {
        clients.remove(clientId)
        wokenAt = null
    }

    @Synchronized
    fun isPresent(now: Long): Boolean = expiresAt(now) != null

    /** When presence ends unless a Frame is heard from again, or null if no Frame is present. */
    @Synchronized
    fun expiresAt(now: Long): Long? {
        clients.values.removeAll { now - it >= timeoutMs }
        if (wokenAt?.let { now - it >= timeoutMs } == true) wokenAt = null
        val last = maxOf(clients.values.maxOrNull() ?: Long.MIN_VALUE, wokenAt ?: Long.MIN_VALUE)
        return if (last == Long.MIN_VALUE) null else last + timeoutMs
    }

    companion object {
        const val TIMEOUT_MS = 60_000L
    }
}
