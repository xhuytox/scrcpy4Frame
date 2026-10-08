package io.github.xhuytox.scrcpy4frame.companion

import org.json.JSONObject
import java.util.Base64

/**
 * Companion <-> Frame wire protocol, specified in docs/CONTRACTS.md. UTF-8 JSON, one object per line.
 * Builders return the line without its trailing '\n'.
 */
object Protocol {
    const val VERSION = 2
    const val SOCKET_NAME = "scrcpy4frame"
    const val MAX_LINE_BYTES = 1 shl 20

    /** UDP port of [WakeServer]. */
    const val WAKE_PORT = 38490
    const val MAX_WAKE_BYTES = 512
    const val MIN_WAKE_KEY_BYTES = 16
    const val MAX_WAKE_KEY_BYTES = 64

    const val UID_ROOT = 0
    const val UID_SHELL = 2000

    /** Only adbd (shell) or root may talk to the companion. */
    fun isAllowedPeer(uid: Int): Boolean = uid == UID_SHELL || uid == UID_ROOT

    fun hello(
        versionName: String,
        versionCode: Int,
        sdk: Int,
        listenerConnected: Boolean,
        autoWirelessDebugging: Boolean,
        canWriteSecureSettings: Boolean,
        wakePort: Int,
    ): String = encode(
        JSONObject()
            .put("type", "hello")
            .put("protocol", VERSION)
            .put("versionName", versionName)
            .put("versionCode", versionCode)
            .put("sdk", sdk)
            .put("listenerConnected", listenerConnected)
            .put("autoWirelessDebugging", autoWirelessDebugging)
            .put("canWriteSecureSettings", canWriteSecureSettings)
            .put("wakePort", wakePort),
    )

    fun posted(n: ForwardedNotification): String = encode(
        JSONObject()
            .put("type", "posted")
            .put("key", n.key)
            .put("pkg", n.pkg)
            .put("appLabel", n.appLabel)
            .put("title", n.title)
            .put("text", n.text)
            .put("when", n.whenMillis)
            // put(key, null) removes the key. The contract wants an explicit null.
            .put("icon", n.iconBase64 ?: JSONObject.NULL),
    )

    fun removed(key: String): String = encode(JSONObject().put("type", "removed").put("key", key))

    fun pong(): String = encode(JSONObject().put("type", "pong"))

    fun error(message: String): String = encode(JSONObject().put("type", "error").put("message", message))

    /** A parsed Frame -> companion line. */
    sealed interface Command {
        data object Ping : Command
        data class Dismiss(val key: String) : Command
        data class Config(val autoWirelessDebugging: Boolean) : Command
        /** A Frame's key for waking the device over UDP, as canonical base64. */
        data class WakeKey(val key: String) : Command
        /** The Frame app is closing, or forgot this device. */
        data object Bye : Command
        /** Answered with an `error` message. */
        data class Invalid(val message: String) : Command
    }

    fun parseCommand(line: String): Command {
        val obj = try {
            JSONObject(line)
        } catch (e: Exception) {
            return Command.Invalid("invalid JSON")
        }
        return when (val type = obj.opt("type")) {
            "ping" -> Command.Ping
            "dismiss" -> (obj.opt("key") as? String)?.takeIf { it.isNotEmpty() }
                ?.let { Command.Dismiss(it) }
                ?: Command.Invalid("dismiss: \"key\" must be a non-empty string")
            "config" -> (obj.opt("autoWirelessDebugging") as? Boolean)
                ?.let { Command.Config(it) }
                ?: Command.Invalid("config: \"autoWirelessDebugging\" must be a boolean")
            "wakeKey" -> (obj.opt("key") as? String)?.let(::decodeWakeKey)
                ?.let { Command.WakeKey(Base64.getEncoder().encodeToString(it)) }
                ?: Command.Invalid(
                    "wakeKey: \"key\" must be base64 of $MIN_WAKE_KEY_BYTES to $MAX_WAKE_KEY_BYTES bytes",
                )
            "bye" -> Command.Bye
            is String -> Command.Invalid("unknown type: $type")
            else -> Command.Invalid("missing \"type\"")
        }
    }

    /** The bytes of a base64 wake key, or null if it isn't valid base64 of an allowed length. */
    fun decodeWakeKey(base64: String): ByteArray? {
        val bytes = try {
            Base64.getDecoder().decode(base64)
        } catch (e: IllegalArgumentException) {
            return null
        }
        return bytes.takeIf { it.size in MIN_WAKE_KEY_BYTES..MAX_WAKE_KEY_BYTES }
    }

    /** A UDP wake datagram from a Frame: a request ([nonce] null) or the answer to a challenge. */
    data class WakeRequest(val nonce: String?, val mac: String?)

    /** Null unless [datagram] is a `wake` object whose `nonce` and `mac` are strings when present. */
    fun parseWake(datagram: String): WakeRequest? {
        val obj = try {
            JSONObject(datagram)
        } catch (e: Exception) {
            return null
        }
        if (obj.opt("type") != "wake") return null
        val nonce = obj.opt("nonce")
        val mac = obj.opt("mac")
        if (nonce != null && nonce !is String) return null
        if (mac != null && mac !is String) return null
        return WakeRequest(nonce as String?, mac as String?)
    }

    fun challenge(nonce: String): String = encode(JSONObject().put("type", "challenge").put("nonce", nonce))

    fun awake(): String = encode(JSONObject().put("type", "awake"))

    /**
     * Serializes [obj] to one line, also escaping U+0085, U+2028 and U+2029. org.json can leave
     * those raw, but line splitters like Python's str.splitlines() treat them as line breaks.
     */
    fun encode(obj: JSONObject): String {
        val s = obj.toString()
        if (s.none { it == '\u0085' || it == ' ' || it == ' ' }) return s
        val sb = StringBuilder(s.length + 16)
        for (c in s) {
            when (c) {
                '\u0085' -> sb.append("\\u0085")
                ' ' -> sb.append("\\u2028")
                ' ' -> sb.append("\\u2029")
                else -> sb.append(c)
            }
        }
        return sb.toString()
    }

    /** UTF-8 bytes of [line] plus '\n', or null if that exceeds [MAX_LINE_BYTES]. */
    fun frame(line: String): ByteArray? {
        val bytes = (line + "\n").toByteArray(Charsets.UTF_8)
        return if (bytes.size > MAX_LINE_BYTES) null else bytes
    }
}
