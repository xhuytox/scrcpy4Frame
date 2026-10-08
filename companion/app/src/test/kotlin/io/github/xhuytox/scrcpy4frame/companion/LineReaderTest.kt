package io.github.xhuytox.scrcpy4frame.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.InputStream

class LineReaderTest {

    /** Returns at most [chunk] bytes per read() to split lines and UTF-8 sequences across reads. */
    private class ChunkedStream(private val data: ByteArray, private val chunk: Int) : InputStream() {
        private var pos = 0
        override fun read(): Int = if (pos < data.size) data[pos++].toInt() and 0xff else -1
        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (pos >= data.size) return -1
            val n = minOf(len, chunk, data.size - pos)
            System.arraycopy(data, pos, b, off, n)
            pos += n
            return n
        }
    }

    private fun readAll(reader: LineReader): List<String> {
        val out = mutableListOf<String>()
        while (true) out += reader.readLine() ?: return out
    }

    @Test
    fun readsLinesAndStripsCr() {
        val r = LineReader(ByteArrayInputStream("{\"type\":\"ping\"}\n\r\nsecond\r\n".toByteArray()))
        assertEquals(listOf("{\"type\":\"ping\"}", "", "second"), readAll(r))
    }

    @Test
    fun returnsUnterminatedLastLineThenNull() {
        val r = LineReader(ByteArrayInputStream("a\nb".toByteArray()))
        assertEquals("a", r.readLine())
        assertEquals("b", r.readLine())
        assertNull(r.readLine())
        assertNull(r.readLine())
    }

    @Test
    fun emptyStreamIsEof() {
        assertNull(LineReader(ByteArrayInputStream(ByteArray(0))).readLine())
    }

    @Test
    fun handlesMultiByteUtf8SplitAcrossReads() {
        val text = "héllo 世界 😀\nsecond line\n"
        for (chunk in 1..7) {
            val r = LineReader(ChunkedStream(text.toByteArray(Charsets.UTF_8), chunk))
            assertEquals("chunk=$chunk", listOf("héllo 世界 😀", "second line"), readAll(r))
        }
    }

    @Test
    fun handlesLinesLongerThanTheInternalBuffer() {
        val long = "x".repeat(50_000)
        val r = LineReader(ByteArrayInputStream("$long\nshort\n".toByteArray()))
        assertEquals(listOf(long, "short"), readAll(r))
    }

    @Test
    fun acceptsALineOfExactlyTheLimit() {
        val r = LineReader(ByteArrayInputStream(("y".repeat(16) + "\n").toByteArray()), maxLineBytes = 16)
        assertEquals("y".repeat(16), r.readLine())
    }

    @Test
    fun rejectsLinesOverTheLimit() {
        val r = LineReader(ChunkedStream(("z".repeat(17) + "\n").toByteArray(), 5), maxLineBytes = 16)
        try {
            r.readLine()
            fail("expected LineTooLongException")
        } catch (e: LineReader.LineTooLongException) {
            // expected
        }
    }

    @Test
    fun defaultLimitIsOneMebibyte() {
        val ok = "a".repeat(Protocol.MAX_LINE_BYTES)
        assertEquals(ok.length, LineReader(ByteArrayInputStream("$ok\n".toByteArray())).readLine()!!.length)
        try {
            LineReader(ByteArrayInputStream("${ok}a\n".toByteArray())).readLine()
            fail("expected LineTooLongException")
        } catch (e: LineReader.LineTooLongException) {
            // expected
        }
    }
}
