package io.github.xhuytox.scrcpy4frame.companion

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import android.util.Log

/** Turns Wireless debugging (Android 11+) on or off by writing `Settings.Global adb_wifi_enabled`. */
object WirelessDebugging {
    private const val TAG = "S4F.WirelessDebugging"

    /** Hidden `Settings.Global.ADB_WIFI_ENABLED`. */
    const val SETTING_ADB_WIFI_ENABLED = "adb_wifi_enabled"

    val isSupported: Boolean
        get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R

    fun canWriteSecureSettings(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) ==
            PackageManager.PERMISSION_GRANTED

    /** Null before Android 11 or if the setting can't be read. */
    fun isEnabled(context: Context): Boolean? {
        if (!isSupported) return null
        return try {
            Settings.Global.getInt(context.contentResolver, SETTING_ADB_WIFI_ENABLED, 0) == 1
        } catch (e: SecurityException) {
            null
        }
    }

    /** Returns true if it wrote the setting. */
    fun maybeEnable(context: Context, trigger: String): Boolean = maybeWrite(context, trigger, 1)

    /** Returns true if it wrote the setting. */
    fun maybeDisable(context: Context, trigger: String): Boolean = maybeWrite(context, trigger, 0)

    private fun maybeWrite(context: Context, trigger: String, value: Int): Boolean {
        if (!isSupported) return false
        if (!Prefs.autoWirelessDebugging(context)) return false
        if (!canWriteSecureSettings(context)) {
            Log.d(TAG, "$trigger: WRITE_SECURE_SETTINGS not granted")
            return false
        }
        val resolver = context.contentResolver
        // Avoid redundant writes. If a build refuses the read, write anyway.
        val current = try {
            Settings.Global.getInt(resolver, SETTING_ADB_WIFI_ENABLED, 0)
        } catch (e: SecurityException) {
            -1
        }
        if (current == value) return false
        return try {
            val ok = Settings.Global.putInt(resolver, SETTING_ADB_WIFI_ENABLED, value)
            Log.i(TAG, "$trigger: wrote $SETTING_ADB_WIFI_ENABLED=$value (ok=$ok)")
            ok
        } catch (e: SecurityException) {
            Log.w(TAG, "$trigger: could not write $SETTING_ADB_WIFI_ENABLED", e)
            false
        }
    }
}
