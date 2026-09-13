package io.github.gallo.sonycamera.ptp

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.ArrayDeque
import org.junit.Assert.*
import org.junit.Test

internal fun packet(type: Int, code: Int, tx: Int, body: ByteArray = byteArrayOf()): ByteArray =
    ByteBuffer.allocate(12 + body.size).order(ByteOrder.LITTLE_ENDIAN)
        .putInt(12 + body.size).putShort(type.toShort()).putShort(code.toShort())
        .putInt(tx).put(body).array()

internal class PacketInput(vararg chunks: ByteArray) {
    val chunks = ArrayDeque<ByteArray>().apply { chunks.forEach(::add) }
    val timeouts = mutableListOf<Int>()
    var calls = 0
    fun read(buffer: ByteArray, timeout: Int): Int {
        calls++
        timeouts += timeout
        val bytes = chunks.pollFirst() ?: return -1
        check(bytes.size <= buffer.size)
        bytes.copyInto(buffer)
        return bytes.size
    }
}

class PtpContainerReaderTest {
    @Test fun splitHeaderAndPayloadAreReassembled() {
        val expected = packet(2, 0x1009, 7, byteArrayOf(1, 2, 3, 4))
        val input = PacketInput(*expected.map { byteArrayOf(it) }.toTypedArray())
        val result = PtpContainerReader(input::read).read(PtpReadDeadline(100))!!
        assertEquals(7, result.transactionId)
        assertArrayEquals(byteArrayOf(1, 2, 3, 4), result.payload)
    }

    @Test fun coalescedResponseIsNotConsumedAsJpegData() {
        val body = byteArrayOf(1, 2, 3)
        val input = PacketInput(packet(2, 0x1009, 2, body) + packet(3, 0x2001, 2))
        val reader = PtpContainerReader(input::read)
        assertArrayEquals(body, reader.read(PtpReadDeadline(100))!!.payload)
        val response = reader.read(PtpReadDeadline(100))!!
        assertEquals(3, response.type)
        assertEquals(0x2001, response.code)
        assertEquals(1, input.calls)
    }

    @Test fun partialPayloadSurvivesTimeoutToAllowStaleTransactionDrain() {
        val data = packet(2, 0x1009, 3, byteArrayOf(1, 2, 3, 4))
        val input = PacketInput(data.copyOfRange(0, 14))
        val reader = PtpContainerReader(input::read)
        assertNull(reader.read(PtpReadDeadline(100)))
        assertTrue(reader.hasPartialContainer)
        input.chunks.add(data.copyOfRange(14, data.size))
        assertArrayEquals(byteArrayOf(1, 2, 3, 4), reader.read(PtpReadDeadline(100))!!.payload)
        assertFalse(reader.hasPartialContainer)
    }

    @Test fun partialHeaderSurvivesTimeout() {
        val bytes = packet(3, 0x2001, 8)
        val input = PacketInput(bytes.copyOfRange(0, 5))
        val reader = PtpContainerReader(input::read)
        assertNull(reader.read(PtpReadDeadline(100)))
        input.chunks.add(bytes.copyOfRange(5, bytes.size))
        assertEquals(8, reader.read(PtpReadDeadline(100))!!.transactionId)
    }

    @Test fun malformedLengthsAreRejectedBeforeAllocation() {
        for (length in listOf(-1, 0, 11, Int.MAX_VALUE)) {
            val bytes = packet(2, 0x1009, 1)
            ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).putInt(length)
            assertNull(PtpContainerReader(PacketInput(bytes)::read).read(PtpReadDeadline(100)))
        }
    }

    @Test fun malformedResponseParameterLengthIsRejected() {
        val input = PacketInput(packet(3, 0x2001, 1, byteArrayOf(1)))
        assertNull(PtpContainerReader(input::read).read(PtpReadDeadline(100)))
    }

    @Test fun zeroLengthPacketBeforeResponseIsSkipped() {
        val input = PacketInput(byteArrayOf(), packet(3, 0x2001, 4))
        assertEquals(4, PtpContainerReader(input::read).read(PtpReadDeadline(100))!!.transactionId)
        assertTrue(input.timeouts.all { it > 0 })
    }

    @Test fun failedReadDoesNotSpinUntilTimeout() {
        val input = PacketInput()
        assertNull(PtpContainerReader(input::read).read(PtpReadDeadline(5_000)))
        assertEquals(1, input.calls)
    }

    @Test fun resetDiscardsBufferedOldResponse() {
        val input = PacketInput(packet(3, 0x2001, 1) + packet(3, 0x2001, 2))
        val reader = PtpContainerReader(input::read)
        assertEquals(1, reader.read(PtpReadDeadline(100))!!.transactionId)
        reader.reset()
        input.chunks.add(packet(3, 0x2001, 3))
        assertEquals(3, reader.read(PtpReadDeadline(100))!!.transactionId)
    }

    @Test fun deadlineUsesOneBudgetAndNeverReturnsUsbInfiniteTimeout() {
        var now = 0L
        val deadline = PtpReadDeadline(10) { now }
        assertEquals(10, deadline.remainingMs())
        now = 9_999_999L
        assertEquals(1, deadline.remainingMs())
        now = 10_000_000L
        assertEquals(0, deadline.remainingMs())
    }

    @Test(expected = IllegalArgumentException::class)
    fun zeroTimeoutIsRejected() { PtpReadDeadline(0) }
}
