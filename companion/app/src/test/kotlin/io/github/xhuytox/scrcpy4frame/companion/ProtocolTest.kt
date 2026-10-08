package io.github.xhuytox.scrcpy4frame.companion

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class ProtocolTest {

    // keys() works with both Android's org.json and the Maven artifact. keySet() doesn't.
    private fun JSONObject.keyNames(): Set<String> = keys().asSequence().toSet()

    private fun parse(line: String): JSONObject {
        assertFalse("a line must not contain a newline", line.contains('\n'))
        return JSONObject(line)
    }

    @Test
    fun helloHasAllContractFieldsWithTheRightTypes() {
        val o = parse(
            Protocol.hello("1.0.0", 1, 34, listenerConnected = true, autoWirelessDebugging = true, canWriteSecureSettings = false, wakePort = 38490),
        )
        assertEquals("hello", o.getString("type"))
        assertEquals(2, o.getInt("protocol"))
        assertEquals("1.0.0", o.getString("versionName"))
        assertEquals(1, o.getInt("versionCode"))
        assertEquals(34, o.getInt("sdk"))
        assertEquals(true, o.get("listenerConnected"))
        assertEquals(true, o.get("autoWirelessDebugging"))
        assertEquals(false, o.get("canWriteSecureSettings"))
        assertEquals(38490, o.getInt("wakePort"))
        assertEquals(
            setOf(
                "type", "protocol", "versionName", "versionCode", "sdk", "listenerConnected", "autoWirelessDebugging",
                "canWriteSecureSettings", "wakePort",
            ),
            o.keyNames(),
        )
    }

    @Test
    fun postedHasAllContractFields() {
        val n = ForwardedNotification(
            key = "0|com.whatsapp|1|null|10123",
            pkg = "com.whatsapp",
            appLabel = "WhatsApp",
            title = "Alice",
            text = "Are you coming?",
            whenMillis = 1_767_225_600_000L,
            iconBase64 = "iVBORw0KGgo=",
        )
        val o = parse(Protocol.posted(n))
        assertEquals("posted", o.getString("type"))
        assertEquals("0|com.whatsapp|1|null|10123", o.getString("key"))
        assertEquals("com.whatsapp", o.getString("pkg"))
        assertEquals("WhatsApp", o.getString("appLabel"))
        assertEquals("Alice", o.getString("title"))
        assertEquals("Are you coming?", o.getString("text"))
        assertEquals(1_767_225_600_000L, o.getLong("when"))
        assertEquals("iVBORw0KGgo=", o.getString("icon"))
        assertEquals(setOf("type", "key", "pkg", "appLabel", "title", "text", "when", "icon"), o.keyNames())
    }

    @Test
    fun postedWithoutIconHasExplicitNull() {
        val n = ForwardedNotification("k", "p", "P", "t", "x", 1L, null)
        val o = parse(Protocol.posted(n))
        assertTrue("icon key must be present", o.has("icon"))
        assertTrue(o.isNull("icon"))
        assertTrue(Protocol.posted(n).contains("\"icon\":null"))
    }

    @Test
    fun postedEscapesNewlinesSoItStaysOneLine() {
        val n = ForwardedNotification("k", "p", "P", "multi\nline\r\ntitle", "a b c\u0085d", 1L, null)
        val line = Protocol.posted(n)
        assertFalse(line.contains('\n'))
        assertFalse(line.contains('\r'))
        assertFalse(line.contains(' '))
        assertFalse(line.contains(' '))
        assertFalse(line.contains('\u0085'))
        assertEquals(1, line.lines().size)
        val o = JSONObject(line)
        assertEquals("multi\nline\r\ntitle", o.getString("title"))
        assertEquals("a b c\u0085d", o.getString("text"))
    }

    @Test
    fun encodeEscapesLineSeparatorsEvenIfTheJsonLibraryDoesNot() {
        // Android's org.json leaves U+2028 raw. Simulate that with a custom toString().
        val raw = object : JSONObject() {
            override fun toString(): String = "{\"text\":\"a b\"}"
        }
        val line = Protocol.encode(raw)
        assertEquals("{\"text\":\"a\\u2028b\"}", line)
        assertEquals("a b", JSONObject(line).getString("text"))
    }

    @Test
    fun removedPongAndError() {
        val removed = parse(Protocol.removed("0|a|1|null|1"))
        assertEquals("removed", removed.getString("type"))
        assertEquals("0|a|1|null|1", removed.getString("key"))
        assertEquals(setOf("type", "key"), removed.keyNames())

        assertEquals(setOf("type"), parse(Protocol.pong()).keyNames())
        assertEquals("pong", parse(Protocol.pong()).getString("type"))

        val error = parse(Protocol.error("boom \"quoted\""))
        assertEquals("error", error.getString("type"))
        assertEquals("boom \"quoted\"", error.getString("message"))
    }

    @Test
    fun parsesPing() {
        assertEquals(Protocol.Command.Ping, Protocol.parseCommand("""{"type":"ping"}"""))
        assertEquals(Protocol.Command.Ping, Protocol.parseCommand(""" { "type" : "ping" , "extra": 1 } """))
    }

    @Test
    fun parsesDismiss() {
        assertEquals(
            Protocol.Command.Dismiss("0|com.whatsapp|1|null|10123"),
            Protocol.parseCommand("""{"type":"dismiss","key":"0|com.whatsapp|1|null|10123"}"""),
        )
    }

    @Test
    fun dismissNeedsAStringKey() {
        assertTrue(Protocol.parseCommand("""{"type":"dismiss"}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"dismiss","key":""}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"dismiss","key":42}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"dismiss","key":null}""") is Protocol.Command.Invalid)
    }

    @Test
    fun parsesConfig() {
        assertEquals(Protocol.Command.Config(true), Protocol.parseCommand("""{"type":"config","autoWirelessDebugging":true}"""))
        assertEquals(Protocol.Command.Config(false), Protocol.parseCommand("""{"type":"config","autoWirelessDebugging":false}"""))
    }

    @Test
    fun configNeedsABoolean() {
        assertTrue(Protocol.parseCommand("""{"type":"config"}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"config","autoWirelessDebugging":"true"}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"config","autoWirelessDebugging":1}""") is Protocol.Command.Invalid)
    }

    @Test
    fun parsesWakeKeyAsCanonicalBase64() {
        val key = Base64.getEncoder().encodeToString(ByteArray(32) { it.toByte() })
        assertEquals(Protocol.Command.WakeKey(key), Protocol.parseCommand("""{"type":"wakeKey","key":"$key"}"""))
        // Not the MIME decoder, so line breaks don't sneak in.
        val broken = key.substring(0, 8) + "\\n" + key.substring(8)
        assertTrue(Protocol.parseCommand("""{"type":"wakeKey","key":"$broken"}""") is Protocol.Command.Invalid)
    }

    @Test
    fun wakeKeyNeedsBase64OfAnAllowedLength() {
        val short = Base64.getEncoder().encodeToString(ByteArray(15))
        val long = Base64.getEncoder().encodeToString(ByteArray(65))
        assertTrue(Protocol.parseCommand("""{"type":"wakeKey"}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"wakeKey","key":"$short"}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"wakeKey","key":"$long"}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"wakeKey","key":"!!!"}""") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":"wakeKey","key":42}""") is Protocol.Command.Invalid)
    }

    @Test
    fun parsesBye() {
        assertEquals(Protocol.Command.Bye, Protocol.parseCommand("""{"type":"bye"}"""))
    }

    @Test
    fun parsesWakeDatagrams() {
        assertEquals(Protocol.WakeRequest(null, null), Protocol.parseWake("""{"type":"wake"}"""))
        assertEquals(Protocol.WakeRequest("ab", "cd"), Protocol.parseWake("""{"type":"wake","nonce":"ab","mac":"cd"}"""))
        assertNull(Protocol.parseWake("""{"type":"wake","nonce":1}"""))
        assertNull(Protocol.parseWake("""{"type":"wake","mac":null}"""))
        assertNull(Protocol.parseWake("""{"type":"ping"}"""))
        assertNull(Protocol.parseWake("garbage"))
    }

    @Test
    fun challengeAndAwake() {
        val challenge = parse(Protocol.challenge("00ff"))
        assertEquals(setOf("type", "nonce"), challenge.keyNames())
        assertEquals("challenge", challenge.getString("type"))
        assertEquals("00ff", challenge.getString("nonce"))
        assertEquals(setOf("type"), parse(Protocol.awake()).keyNames())
        assertEquals("awake", parse(Protocol.awake()).getString("type"))
    }

    @Test
    fun rejectsGarbage() {
        val invalidJson = Protocol.parseCommand("not json")
        assertEquals(Protocol.Command.Invalid("invalid JSON"), invalidJson)
        assertTrue(Protocol.parseCommand("[1,2]") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("{}") is Protocol.Command.Invalid)
        assertTrue(Protocol.parseCommand("""{"type":7}""") is Protocol.Command.Invalid)
        val unknown = Protocol.parseCommand("""{"type":"reboot"}""")
        assertEquals(Protocol.Command.Invalid("unknown type: reboot"), unknown)
    }

    @Test
    fun invalidCommandsBecomeErrorMessages() {
        val cmd = Protocol.parseCommand("""{"type":"reboot"}""") as Protocol.Command.Invalid
        val o = parse(Protocol.error(cmd.message))
        assertEquals("error", o.getString("type"))
        assertEquals("unknown type: reboot", o.getString("message"))
    }

    @Test
    fun onlyShellAndRootPeersAreAllowed() {
        assertTrue(Protocol.isAllowedPeer(2000))
        assertTrue(Protocol.isAllowedPeer(0))
        assertFalse(Protocol.isAllowedPeer(1000)) // system
        assertFalse(Protocol.isAllowedPeer(10123)) // an app
        assertFalse(Protocol.isAllowedPeer(-1)) // unknown
    }

    @Test
    fun frameAppendsNewlineAndEnforcesTheOneMebibyteLimit() {
        val small = Protocol.frame("""{"type":"pong"}""")!!
        assertEquals('\n'.code.toByte(), small.last())
        assertEquals("""{"type":"pong"}""" + "\n", String(small, Charsets.UTF_8))

        val exactly = "x".repeat(Protocol.MAX_LINE_BYTES - 1) // + '\n' = 1 MiB
        assertEquals(Protocol.MAX_LINE_BYTES, Protocol.frame(exactly)!!.size)
        assertNull(Protocol.frame(exactly + "x"))
        // Multi-byte characters count as bytes, not chars.
        assertNull(Protocol.frame("é".repeat(Protocol.MAX_LINE_BYTES / 2)))
    }

    @Test
    fun worstCasePostedLineFitsInOneMebibyte() {
        val s = NotificationSnapshot(
            key = "k".repeat(200),
            packageName = "p".repeat(200),
            flags = 0,
            importance = 4,
            title = "中".repeat(100_000),
            bigText = "中".repeat(1_000_000),
        )
        // A 96x96 RGBA PNG is at most ~37 KB, so 64 KiB of base64 is generous.
        val f = NotificationRules.toForwarded(s, "L".repeat(10_000), "A".repeat(64 * 1024))
        val bytes = Protocol.frame(Protocol.posted(f))
        assertTrue("posted line must fit the protocol limit", bytes != null)
    }
}
