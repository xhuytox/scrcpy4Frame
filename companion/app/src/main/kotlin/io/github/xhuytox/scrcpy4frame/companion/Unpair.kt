package io.github.xhuytox.scrcpy4frame.companion

import android.os.IBinder

/**
 * Removes an adb key from the device's paired devices (Android 11+), as "Forget" under Wireless
 * debugging does. Not run by the app: the Frame runs it from this APK as the shell user, with
 * `CLASSPATH=<apk> app_process / io.github.xhuytox.scrcpy4frame.companion.Unpair <fingerprint>`,
 * because only shell holds MANAGE_DEBUGGING. The fingerprint is the key's MD5 as colon-separated
 * upper-case hex.
 */
object Unpair {
    @JvmStatic
    fun main(args: Array<String>) {
        val fingerprint = args.singleOrNull() ?: error("usage: Unpair <fingerprint>")
        // Hidden APIs; app_process isn't subject to the hidden API checks apps are.
        val binder = Class.forName("android.os.ServiceManager")
            .getMethod("getService", String::class.java)
            .invoke(null, "adb") as IBinder
        val adbManager = Class.forName("android.debug.IAdbManager\$Stub")
            .getMethod("asInterface", IBinder::class.java)
            .invoke(null, binder)
        // Also drops the adb connections that use the key, maybe this one.
        adbManager.javaClass.getMethod("unpairDevice", String::class.java).invoke(adbManager, fingerprint)
    }
}
