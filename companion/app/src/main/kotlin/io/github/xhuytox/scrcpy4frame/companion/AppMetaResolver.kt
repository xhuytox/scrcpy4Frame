package io.github.xhuytox.scrcpy4frame.companion

import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.Drawable
import android.os.Build
import android.os.Bundle
import android.util.Base64
import android.util.Log
import android.util.LruCache
import java.io.ByteArrayOutputStream

/**
 * Label and app icon of a notification's source app, cached per package. The icon is the
 * application icon, not the monochrome small icon, as a base64 PNG of at most 96x96.
 */
class AppMetaResolver(context: Context) {

    data class AppMeta(val label: String, val iconBase64: String?)

    private val pm: PackageManager = context.applicationContext.packageManager
    private val cache = LruCache<String, AppMeta>(64)

    /**
     * [extras] is the notification's extras. The ApplicationInfo that `Notification.Builder` stores
     * there is preferred over a PackageManager lookup.
     */
    fun resolve(pkg: String, extras: Bundle?): AppMeta {
        cache.get(pkg)?.let { return it }
        val info = builderApplicationInfo(extras)?.takeIf { it.packageName == pkg }
            ?: try {
                pm.getApplicationInfo(pkg, 0)
            } catch (e: PackageManager.NameNotFoundException) {
                null
            }
        val label = info?.let {
            try {
                pm.getApplicationLabel(it).toString()
            } catch (e: RuntimeException) {
                null
            }
        }
        val icon = info?.let {
            try {
                toPngBase64(pm.getApplicationIcon(it))
            } catch (e: Exception) {
                Log.w(TAG, "icon for $pkg failed", e)
                null
            }
        }
        val meta = AppMeta(label?.takeIf { it.isNotBlank() } ?: pkg, icon)
        // Don't cache a miss: the package may just not be resolvable yet.
        if (info != null) cache.put(pkg, meta)
        return meta
    }

    fun clear() = cache.evictAll()

    private fun builderApplicationInfo(extras: Bundle?): ApplicationInfo? {
        if (extras == null) return null
        return try {
            // The typed getter is buggy on API 33. androidx BundleCompat also waits for 34.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                extras.getParcelable(EXTRA_BUILDER_APPLICATION_INFO, ApplicationInfo::class.java)
            } else {
                @Suppress("DEPRECATION")
                extras.getParcelable(EXTRA_BUILDER_APPLICATION_INFO) as? ApplicationInfo
            }
        } catch (e: Exception) {
            // BadParcelableException / ClassCastException from a malformed bundle.
            null
        }
    }

    private fun toPngBase64(drawable: Drawable): String? {
        val (w, h) = NotificationRules.fitWithin(drawable.intrinsicWidth, drawable.intrinsicHeight)
        val bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        try {
            val oldBounds = drawable.copyBounds()
            drawable.setBounds(0, 0, w, h)
            drawable.draw(Canvas(bitmap))
            drawable.bounds = oldBounds
            val out = ByteArrayOutputStream()
            if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)) return null
            return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        } finally {
            bitmap.recycle()
        }
    }

    private companion object {
        const val TAG = "S4F.AppMeta"

        /** Hidden `Notification.EXTRA_BUILDER_APPLICATION_INFO`. */
        const val EXTRA_BUILDER_APPLICATION_INFO = "android.appInfo"
    }
}
