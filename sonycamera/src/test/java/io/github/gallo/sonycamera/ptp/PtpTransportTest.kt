package io.github.gallo.sonycamera.ptp

import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import org.junit.Assert.*
import org.junit.Test
import org.mockito.ArgumentMatchers.*
import org.mockito.Mockito.*

class PtpTransportTest {
    private val connection = mock(UsbDeviceConnection::class.java)
    private val bulkIn = mock(UsbEndpoint::class.java)
    private val bulkOut = mock(UsbEndpoint::class.java)
    private val input = PacketInput()
    private var outputResult: Int? = null
    private var writes = 0

    private fun transport(): PtpTransport {
        `when`(connection.bulkTransfer(same(bulkIn), any(ByteArray::class.java), anyInt(), anyInt()))
            .thenAnswer { input.read(it.getArgument(1), it.getArgument(3)) }
        `when`(connection.bulkTransfer(same(bulkOut), any(ByteArray::class.java), anyInt(), anyInt()))
            .thenAnswer { writes++; outputResult ?: it.getArgument<Int>(2) }
        return PtpTransport(connection, bulkOut, bulkIn)
    }

    @Test fun shortPathSkipsStaleResponses() {
        input.chunks.add(packet(3, 0x2001, 99))
        input.chunks.add(packet(2, 0x1009, 1, byteArrayOf(7, 8)))
        input.chunks.add(packet(3, 0x2001, 1))
        val response = transport().sendCommandWithDataShortTimeout(0x1009, 100)
        assertTrue(response.isSuccess)
        assertEquals(1, response.transactionId)
        assertArrayEquals(byteArrayOf(7, 8), response.data)
    }

    @Test fun shortPathDrainsStaleDataAndHandlesCoalescedAck() {
        input.chunks.add(packet(2, 0x1009, 42, byteArrayOf(0)) + packet(3, 0x2001, 42))
        input.chunks.add(packet(2, 0x1009, 1, byteArrayOf(9)) + packet(3, 0x2001, 1))
        val response = transport().sendCommandWithDataShortTimeout(0x1009, 100)
        assertTrue(response.isSuccess)
        assertArrayEquals(byteArrayOf(9), response.data)
        assertEquals(2, input.calls)
    }

    @Test fun truncatedDataCannotBeReportedAsSuccess() {
        input.chunks.add(packet(2, 0x1009, 1, byteArrayOf(1, 2, 3)).dropLast(1).toByteArray())
        val response = transport().sendCommandWithDataShortTimeout(0x1009, 100)
        assertEquals(PtpConstants.RESP_INCOMPLETE_TRANSFER, response.responseCode)
        assertFalse(response.isSuccess)
        assertTrue(response.data.isEmpty())
    }

    @Test fun lateRemainderIsNotMisreadAsNextTransactionsHeader() {
        val first = packet(2, 0x1009, 1, byteArrayOf(1, 2, 3))
        input.chunks.add(first.copyOfRange(0, 14))
        val transport = transport()
        assertFalse(transport.sendCommandWithDataShortTimeout(0x1009, 100).isSuccess)
        input.chunks.add(first.copyOfRange(14, first.size) + packet(3, 0x2001, 1))
        input.chunks.add(packet(2, 0x1009, 2, byteArrayOf(6)) + packet(3, 0x2001, 2))
        val second = transport.sendCommandWithDataShortTimeout(0x1009, 100)
        assertTrue(second.isSuccess)
        assertArrayEquals(byteArrayOf(6), second.data)
    }

    @Test fun positiveShortWriteFailsWithoutReadingAResponse() {
        outputResult = 1
        val response = transport().sendCommandWithDataShortTimeout(0x1009, 100)
        assertFalse(response.isSuccess)
        assertEquals(0, input.calls)
    }

    @Test fun shortControlWriteDoesNotSendDataPhase() {
        outputResult = 0
        val response = transport().sendHighPriorityCommandWithDataOut(0x9207, byteArrayOf(1), 0xD2E4)
        assertFalse(response.isSuccess)
        assertEquals(1, writes)
        assertEquals(0, input.calls)
    }

    @Test fun commandResponseMatchesTransaction() {
        input.chunks.add(packet(3, 0x2001, 9) + packet(3, 0x2001, 1))
        val response = transport().sendCommand(0x1002, responseTimeoutMs = 100, params = intArrayOf(1))
        assertTrue(response.isSuccess)
        assertEquals(1, response.transactionId)
    }

    @Test fun flushStopsOnFirstEmptyTransfer() {
        transport().flushPipe()
        assertEquals(1, input.calls)
    }
}
