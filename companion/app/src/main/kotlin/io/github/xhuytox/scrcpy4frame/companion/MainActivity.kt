package io.github.xhuytox.scrcpy4frame.companion

import android.app.Activity
import android.app.NotificationManager
import android.content.ComponentName
import android.content.SharedPreferences
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import io.github.xhuytox.scrcpy4frame.companion.databinding.ActivityMainBinding

/** Status screen plus the Wireless debugging switch. The Frame does all setup over adb. */
class MainActivity : Activity() {

    private lateinit var binding: ActivityMainBinding

    // Picks up changes made by the Frame's `config` command.
    private val prefListener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == Prefs.KEY_AUTO_WIRELESS_DEBUGGING) refresh()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.switchAutoWirelessDebugging.setOnCheckedChangeListener { _, checked ->
            if (checked != Prefs.autoWirelessDebugging(this)) Prefs.setAutoWirelessDebugging(this, checked)
        }
    }

    override fun onResume() {
        super.onResume()
        Prefs.of(this).registerOnSharedPreferenceChangeListener(prefListener)
        refresh()
    }

    override fun onPause() {
        Prefs.of(this).unregisterOnSharedPreferenceChangeListener(prefListener)
        super.onPause()
    }

    private fun refresh() {
        binding.statusNotificationAccess.text = row(
            R.string.status_notification_access,
            when {
                !isNotificationAccessGranted() -> getString(R.string.not_granted)
                NotifListener.isConnected -> getString(R.string.granted)
                else -> getString(R.string.granted_not_running)
            },
        )
        binding.statusWriteSecureSettings.text =
            row(R.string.status_write_secure_settings, grantedText(WirelessDebugging.canWriteSecureSettings(this)))
        binding.statusWirelessDebugging.text = row(
            R.string.status_wireless_debugging,
            when {
                !WirelessDebugging.isSupported -> getString(R.string.unavailable_before_android_11)
                else -> when (WirelessDebugging.isEnabled(this)) {
                    true -> getString(R.string.on)
                    false -> getString(R.string.off)
                    null -> getString(R.string.unknown)
                }
            },
        )
        binding.statusAndroidVersion.text = row(
            R.string.status_android_version,
            getString(R.string.android_version_value, Build.VERSION.RELEASE, Build.VERSION.SDK_INT),
        )
        binding.statusCompanionVersion.text = row(
            R.string.status_companion_version,
            getString(R.string.companion_version_value, BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE),
        )
        binding.switchAutoWirelessDebugging.isChecked = Prefs.autoWirelessDebugging(this)
        binding.switchAutoWirelessDebugging.isEnabled = WirelessDebugging.isSupported
    }

    private fun row(labelRes: Int, value: String): String = "${getString(labelRes)}: $value"

    private fun grantedText(granted: Boolean): String =
        getString(if (granted) R.string.granted else R.string.not_granted)

    private fun isNotificationAccessGranted(): Boolean {
        val component = ComponentName(this, NotifListener::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            return getSystemService(NotificationManager::class.java)
                ?.isNotificationListenerAccessGranted(component) == true
        }
        // Android 8.0 has no public API for this, so read the hidden secure setting.
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners") ?: return false
        return enabled.split(':').any { ComponentName.unflattenFromString(it) == component }
    }
}
