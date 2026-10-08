package io.github.xhuytox.scrcpy4frame.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationRulesTest {

    private val own = "io.github.xhuytox.scrcpy4frame.companion"

    private fun snap(
        pkg: String = "com.whatsapp",
        flags: Int = 0,
        importance: Int? = 3,
        title: CharSequence? = "Alice",
        conversationTitle: CharSequence? = null,
        bigText: CharSequence? = null,
        text: CharSequence? = "Are you coming?",
        textLines: List<CharSequence?>? = null,
        whenMillis: Long = 1_767_225_600_000L,
        postTimeMillis: Long = 1_767_225_699_000L,
    ) = NotificationSnapshot(
        key = "0|$pkg|1|null|10123",
        packageName = pkg,
        flags = flags,
        importance = importance,
        title = title,
        conversationTitle = conversationTitle,
        bigText = bigText,
        text = text,
        textLines = textLines,
        whenMillis = whenMillis,
        postTimeMillis = postTimeMillis,
    )

    @Test
    fun constantsMatchTheAndroidSdk() {
        assertEquals(android.app.Notification.FLAG_ONGOING_EVENT, NotificationRules.FLAG_ONGOING_EVENT)
        assertEquals(android.app.Notification.FLAG_FOREGROUND_SERVICE, NotificationRules.FLAG_FOREGROUND_SERVICE)
        assertEquals(android.app.Notification.FLAG_GROUP_SUMMARY, NotificationRules.FLAG_GROUP_SUMMARY)
        assertEquals(android.app.NotificationManager.IMPORTANCE_MIN, NotificationRules.IMPORTANCE_MIN)
    }

    @Test
    fun ordinaryNotificationIsForwarded() {
        assertNull(NotificationRules.skipReason(snap(), own))
    }

    @Test
    fun skipsOwnPackage() {
        assertEquals(SkipReason.OWN_PACKAGE, NotificationRules.skipReason(snap(pkg = own), own))
    }

    @Test
    fun skipsOngoing() {
        assertEquals(SkipReason.ONGOING, NotificationRules.skipReason(snap(flags = NotificationRules.FLAG_ONGOING_EVENT), own))
    }

    @Test
    fun skipsForegroundService() {
        assertEquals(
            SkipReason.FOREGROUND_SERVICE,
            NotificationRules.skipReason(snap(flags = NotificationRules.FLAG_FOREGROUND_SERVICE), own),
        )
    }

    @Test
    fun skipsGroupSummary() {
        assertEquals(
            SkipReason.GROUP_SUMMARY,
            NotificationRules.skipReason(snap(flags = NotificationRules.FLAG_GROUP_SUMMARY or 0x10), own),
        )
    }

    @Test
    fun unrelatedFlagsDoNotSkip() {
        // FLAG_AUTO_CANCEL (0x10), FLAG_ONLY_ALERT_ONCE (0x8), FLAG_NO_CLEAR (0x20)
        assertNull(NotificationRules.skipReason(snap(flags = 0x10 or 0x8 or 0x20), own))
    }

    @Test
    fun skipsImportanceMinAndBelow() {
        assertEquals(SkipReason.LOW_IMPORTANCE, NotificationRules.skipReason(snap(importance = 1), own))
        assertEquals(SkipReason.LOW_IMPORTANCE, NotificationRules.skipReason(snap(importance = 0), own))
        assertEquals(SkipReason.LOW_IMPORTANCE, NotificationRules.skipReason(snap(importance = -1000), own))
    }

    @Test
    fun forwardsImportanceLowAndAbove() {
        for (importance in 2..5) assertNull(NotificationRules.skipReason(snap(importance = importance), own))
    }

    @Test
    fun unknownRankingDoesNotSkip() {
        assertNull(NotificationRules.skipReason(snap(importance = null), own))
    }

    @Test
    fun skipsEmptyTitleAndText() {
        assertEquals(SkipReason.EMPTY, NotificationRules.skipReason(snap(title = null, text = null), own))
        assertEquals(SkipReason.EMPTY, NotificationRules.skipReason(snap(title = "  ", text = "\n"), own))
        assertEquals(
            SkipReason.EMPTY,
            NotificationRules.skipReason(snap(title = "", text = "", textLines = listOf("", null)), own),
        )
    }

    @Test
    fun titleOnlyOrTextOnlyIsForwarded() {
        assertNull(NotificationRules.skipReason(snap(title = "Only a title", text = null), own))
        assertNull(NotificationRules.skipReason(snap(title = null, text = "Only text"), own))
        assertNull(NotificationRules.skipReason(snap(title = null, text = null, conversationTitle = "Group"), own))
        assertNull(NotificationRules.skipReason(snap(title = null, text = null, textLines = listOf("line")), own))
    }

    @Test
    fun ownPackageWinsOverOtherReasons() {
        val s = snap(pkg = own, flags = NotificationRules.FLAG_ONGOING_EVENT, importance = 0, title = null, text = null)
        assertEquals(SkipReason.OWN_PACKAGE, NotificationRules.skipReason(s, own))
    }

    @Test
    fun titlePrefersAndroidTitle() {
        assertEquals("Alice", NotificationRules.title(snap(title = "Alice", conversationTitle = "Family")))
    }

    @Test
    fun titleFallsBackToConversationTitle() {
        assertEquals("Family", NotificationRules.title(snap(title = null, conversationTitle = "Family")))
        assertEquals("Family", NotificationRules.title(snap(title = " ", conversationTitle = "Family")))
    }

    @Test
    fun titleIsEmptyWhenNothingIsSet() {
        assertEquals("", NotificationRules.title(snap(title = null, conversationTitle = null)))
    }

    @Test
    fun textPrefersBigText() {
        val s = snap(bigText = "The whole message", text = "The whole…", textLines = listOf("a", "b"))
        assertEquals("The whole message", NotificationRules.text(s))
    }

    @Test
    fun textFallsBackToText() {
        assertEquals("short", NotificationRules.text(snap(bigText = null, text = "short", textLines = listOf("a"))))
        assertEquals("short", NotificationRules.text(snap(bigText = "", text = "short")))
    }

    @Test
    fun textFallsBackToLastTextLine() {
        val s = snap(bigText = null, text = null, textLines = listOf("first", "second", "last"))
        assertEquals("last", NotificationRules.text(s))
    }

    @Test
    fun textIgnoresTrailingBlankTextLines() {
        val s = snap(bigText = null, text = null, textLines = listOf("first", "last", "", null))
        assertEquals("last", NotificationRules.text(s))
    }

    @Test
    fun spannedValuesAreFlattenedAndTrimmed() {
        val spanned: CharSequence = StringBuilder("  Bold title \n")
        assertEquals("Bold title", NotificationRules.title(snap(title = spanned)))
    }

    @Test
    fun toForwardedCopiesFields() {
        val f = NotificationRules.toForwarded(snap(), "WhatsApp", "aWNvbg==")
        assertEquals("0|com.whatsapp|1|null|10123", f.key)
        assertEquals("com.whatsapp", f.pkg)
        assertEquals("WhatsApp", f.appLabel)
        assertEquals("Alice", f.title)
        assertEquals("Are you coming?", f.text)
        assertEquals(1_767_225_600_000L, f.whenMillis)
        assertEquals("aWNvbg==", f.iconBase64)
    }

    @Test
    fun toForwardedFallsBackToPackageNameForLabel() {
        assertEquals("com.whatsapp", NotificationRules.toForwarded(snap(), null, null).appLabel)
        assertEquals("com.whatsapp", NotificationRules.toForwarded(snap(), "  ", null).appLabel)
    }

    @Test
    fun toForwardedUsesPostTimeWhenWhenIsUnset() {
        assertEquals(1_767_225_699_000L, NotificationRules.toForwarded(snap(whenMillis = 0), "x", null).whenMillis)
    }

    @Test
    fun toForwardedTruncatesHugeText() {
        val f = NotificationRules.toForwarded(snap(bigText = "x".repeat(100_000)), "x", null)
        assertEquals(NotificationRules.MAX_TEXT_CHARS, f.text.length)
        assertTrue(f.text.endsWith("…"))
    }

    @Test
    fun truncateKeepsShortStrings() {
        assertEquals("hello", NotificationRules.truncate("hello", 5))
    }

    @Test
    fun truncateNeverSplitsSurrogatePairs() {
        val emoji = "😀" // one code point, two UTF-16 units
        // A cut at 4 would keep "ab" plus a lone high surrogate.
        val out = NotificationRules.truncate("ab$emoji$emoji", 4)
        assertEquals("ab…", out)
        assertTrue(out.none { Character.isSurrogate(it) })
    }

    @Test
    fun fitWithinScalesDownLargeIcons() {
        assertEquals(96 to 96, NotificationRules.fitWithin(324, 324))
        assertEquals(96 to 48, NotificationRules.fitWithin(200, 100))
    }

    @Test
    fun fitWithinKeepsSmallIcons() {
        assertEquals(48 to 48, NotificationRules.fitWithin(48, 48))
        assertEquals(96 to 96, NotificationRules.fitWithin(96, 96))
    }

    @Test
    fun fitWithinHandlesDrawablesWithoutIntrinsicSize() {
        assertEquals(96 to 96, NotificationRules.fitWithin(-1, -1))
        assertEquals(96 to 96, NotificationRules.fitWithin(0, 50))
    }
}
