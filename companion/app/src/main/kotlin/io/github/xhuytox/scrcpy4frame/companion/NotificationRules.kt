package io.github.xhuytox.scrcpy4frame.companion

// No Android classes in this file, so it can be unit-tested on the JVM.

/** What the rules need from one StatusBarNotification. */
data class NotificationSnapshot(
    val key: String,
    val packageName: String,
    val flags: Int,
    /** Null when the ranking could not be looked up. */
    val importance: Int?,
    val title: CharSequence? = null,
    val conversationTitle: CharSequence? = null,
    val bigText: CharSequence? = null,
    val text: CharSequence? = null,
    val textLines: List<CharSequence?>? = null,
    /** `Notification.when` */
    val whenMillis: Long = 0L,
    /** `StatusBarNotification.postTime` */
    val postTimeMillis: Long = 0L,
)

/** The payload of a `posted` message. */
data class ForwardedNotification(
    val key: String,
    val pkg: String,
    val appLabel: String,
    val title: String,
    val text: String,
    val whenMillis: Long,
    /** Base64 (NO_WRAP) PNG of the app icon, at most 96x96, or null. */
    val iconBase64: String?,
)

enum class SkipReason {
    OWN_PACKAGE,
    ONGOING,
    FOREGROUND_SERVICE,
    GROUP_SUMMARY,
    LOW_IMPORTANCE,
    EMPTY,
}

object NotificationRules {
    // Copies of SDK constants. NotificationRulesTest checks they match.
    const val FLAG_ONGOING_EVENT = 0x00000002
    const val FLAG_FOREGROUND_SERVICE = 0x00000040
    const val FLAG_GROUP_SUMMARY = 0x00000200
    const val IMPORTANCE_MIN = 1

    /** Longest icon edge in pixels. */
    const val MAX_ICON_PX = 96

    // Keeps a worst-case `posted` line (3 bytes per UTF-16 unit, plus the icon) well under 1 MiB.
    const val MAX_TITLE_CHARS = 1_024
    const val MAX_TEXT_CHARS = 16_384
    const val MAX_LABEL_CHARS = 256

    /** `android.title`, then `android.conversationTitle`. Blank values fall through. */
    fun title(s: NotificationSnapshot): String = firstNonBlank(s.title, s.conversationTitle)

    /** `android.bigText`, then `android.text`, then the last non-blank `android.textLines` entry. */
    fun text(s: NotificationSnapshot): String =
        firstNonBlank(s.bigText, s.text, s.textLines?.lastOrNull { !it.isNullOrBlank() })

    /** Null if the notification should be forwarded. */
    fun skipReason(s: NotificationSnapshot, ownPackage: String): SkipReason? = when {
        s.packageName == ownPackage -> SkipReason.OWN_PACKAGE
        s.flags and FLAG_ONGOING_EVENT != 0 -> SkipReason.ONGOING
        s.flags and FLAG_FOREGROUND_SERVICE != 0 -> SkipReason.FOREGROUND_SERVICE
        s.flags and FLAG_GROUP_SUMMARY != 0 -> SkipReason.GROUP_SUMMARY
        s.importance != null && s.importance <= IMPORTANCE_MIN -> SkipReason.LOW_IMPORTANCE
        title(s).isEmpty() && text(s).isEmpty() -> SkipReason.EMPTY
        else -> null
    }

    fun toForwarded(s: NotificationSnapshot, appLabel: String?, iconBase64: String?): ForwardedNotification =
        ForwardedNotification(
            key = s.key,
            pkg = s.packageName,
            appLabel = truncate(appLabel?.trim().takeUnless { it.isNullOrEmpty() } ?: s.packageName, MAX_LABEL_CHARS),
            title = truncate(title(s), MAX_TITLE_CHARS),
            text = truncate(text(s), MAX_TEXT_CHARS),
            whenMillis = if (s.whenMillis > 0) s.whenMillis else s.postTimeMillis,
            iconBase64 = iconBase64,
        )

    /** Cuts [value] to at most [maxChars] UTF-16 units, ending in "…" and never splitting a surrogate pair. */
    fun truncate(value: String, maxChars: Int): String {
        require(maxChars >= 1)
        if (value.length <= maxChars) return value
        var end = maxChars - 1
        if (end > 0 && Character.isHighSurrogate(value[end - 1])) end--
        return value.substring(0, end) + "…"
    }

    /** Downscales to fit in [max]x[max], keeping the aspect ratio. Never upscales. */
    fun fitWithin(intrinsicWidth: Int, intrinsicHeight: Int, max: Int = MAX_ICON_PX): Pair<Int, Int> {
        if (intrinsicWidth <= 0 || intrinsicHeight <= 0) return max to max
        val longest = maxOf(intrinsicWidth, intrinsicHeight)
        if (longest <= max) return intrinsicWidth to intrinsicHeight
        val scale = max.toDouble() / longest
        val w = (intrinsicWidth * scale).toInt().coerceIn(1, max)
        val h = (intrinsicHeight * scale).toInt().coerceIn(1, max)
        return w to h
    }

    private fun firstNonBlank(vararg values: CharSequence?): String {
        for (v in values) {
            val s = v?.toString()?.trim()
            if (!s.isNullOrEmpty()) return s
        }
        return ""
    }
}
