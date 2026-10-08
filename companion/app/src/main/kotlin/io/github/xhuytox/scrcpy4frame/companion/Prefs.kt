package io.github.xhuytox.scrcpy4frame.companion

import android.content.Context
import android.content.SharedPreferences

/**
 * The companion's setting, changed from MainActivity or by the Frame's `config` command, plus the
 * state it needs to manage Wireless debugging for the Frame.
 */
object Prefs {
    private const val FILE = "companion"
    const val KEY_AUTO_WIRELESS_DEBUGGING = "autoWirelessDebugging"
    private const val DEFAULT_AUTO_WIRELESS_DEBUGGING = true
    private const val KEY_WAKE_KEYS = "wakeKeys"
    private const val KEY_FRAME_SESSION = "frameSession"
    private const val MAX_WAKE_KEYS = 4

    fun of(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun autoWirelessDebugging(context: Context): Boolean =
        of(context).getBoolean(KEY_AUTO_WIRELESS_DEBUGGING, DEFAULT_AUTO_WIRELESS_DEBUGGING)

    fun setAutoWirelessDebugging(context: Context, enabled: Boolean) {
        of(context).edit().putBoolean(KEY_AUTO_WIRELESS_DEBUGGING, enabled).apply()
    }

    /** Keys of the Frames that may wake this device over UDP, newest first. */
    fun wakeKeys(context: Context): List<ByteArray> = storedWakeKeys(context).mapNotNull(Protocol::decodeWakeKey)

    /** Remembers a Frame's key (canonical base64). Keeps the [MAX_WAKE_KEYS] newest. */
    @Synchronized
    fun addWakeKey(context: Context, key: String) {
        val current = storedWakeKeys(context)
        if (current.firstOrNull() == key) return
        val keys = (listOf(key) + current.filter { it != key }).take(MAX_WAKE_KEYS)
        of(context).edit().putString(KEY_WAKE_KEYS, keys.joinToString(",")).apply()
    }

    private fun storedWakeKeys(context: Context): List<String> =
        of(context).getString(KEY_WAKE_KEYS, null)?.split(',')?.filter { it.isNotEmpty() }.orEmpty()

    /**
     * True from when a Frame shows up until the companion has turned Wireless debugging off after it
     * left. Stored, so the companion still turns it off if its process restarts in between.
     */
    fun frameSession(context: Context): Boolean = of(context).getBoolean(KEY_FRAME_SESSION, false)

    fun setFrameSession(context: Context, active: Boolean) {
        of(context).edit().putBoolean(KEY_FRAME_SESSION, active).apply()
    }
}
