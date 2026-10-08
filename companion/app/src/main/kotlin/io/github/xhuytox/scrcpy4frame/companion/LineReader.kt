package io.github.xhuytox.scrcpy4frame.companion

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream

/**
 * Reads '\n'-terminated UTF-8 lines from [input], refusing lines longer than [maxLineBytes]
 * (excluding the terminator). A trailing '\r' is stripped. Not thread-safe.
 */
class LineReader(private val input: InputStream, private val maxLineBytes: Int = Protocol.MAX_LINE_BYTES) {

    class LineTooLongException(limit: Int) : IOException("line exceeds $limit bytes")

    private val buf = ByteArray(8 * 1024)
    private var pos = 0
    private var len = 0
    private val line = ByteArrayOutputStream()

    /** Next line without its terminator, or null at end of stream. */
    fun readLine(): String? {
        line.reset()
        while (true) {
            if (pos == len) {
                val n = input.read(buf)
                if (n < 0) {
                    pos = 0
                    len = 0
                    // A final line without '\n' still counts.
                    return if (line.size() > 0) decode() else null
                }
                pos = 0
                len = n
                continue
            }
            var i = pos
            while (i < len && buf[i] != NEWLINE) i++
            val chunk = i - pos
            if (line.size() + chunk > maxLineBytes) throw LineTooLongException(maxLineBytes)
            line.write(buf, pos, chunk)
            if (i < len) {
                pos = i + 1
                return decode()
            }
            pos = len
        }
    }

    private fun decode(): String {
        val bytes = line.toByteArray()
        val end = if (bytes.isNotEmpty() && bytes[bytes.size - 1] == CR) bytes.size - 1 else bytes.size
        return String(bytes, 0, end, Charsets.UTF_8)
    }

    private companion object {
        const val NEWLINE = '\n'.code.toByte()
        const val CR = '\r'.code.toByte()
    }
}
