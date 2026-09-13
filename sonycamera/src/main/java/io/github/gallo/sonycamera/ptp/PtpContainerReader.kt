package io.github.gallo.sonycamera.ptp

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** One monotonic budget shared by all reads/drains in a transaction. */
internal class PtpReadDeadline(
    timeoutMs: Int,
    private val nanoTime: () -> Long = System::nanoTime
) {
    private val startedAt = nanoTime()
    private val budgetNanos = timeoutMs.toLong() * 1_000_000L

    init { require(timeoutMs > 0) { "PTP timeout must be positive (USB uses zero for infinity)" } }

    fun remainingMs(): Int {
        val left = budgetNanos - (nanoTime() - startedAt)
        if (left <= 0) return 0
        // Round UP: a sub-millisecond remainder must never become USB timeout=0.
        return ((left + 999_999L) / 1_000_000L).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
    }
}

internal data class PtpContainer(
    val type: Int,
    val code: Int,
    val transactionId: Int,
    val payload: ByteArray
)

/**
 * USB transfers are not PTP container boundaries. Preserve split headers and
 * payloads across reads/timeouts, and retain a response coalesced with data.
 * Only the transport lock may access this reader. No Android dependency, so
 * malformed packets and late responses can be regression-tested on the JVM.
 */
internal class PtpContainerReader(
    private val readTransfer: (ByteArray, Int) -> Int,
    private val maxContainerLength: Int = 128 * 1024 * 1024
) {
    // Works on API 26/27 too; those Android versions truncate transfers >16 KiB.
    private val input = ByteArray(16 * 1024)
    private var position = 0
    private var limit = 0
    private val header = ByteArray(PtpConstants.HEADER_SIZE)
    private var headerUsed = 0
    private var payload: ByteArray? = null
    private var payloadUsed = 0
    private var type = 0
    private var code = 0
    private var transactionId = 0
    private var invalid = false

    val hasPartialContainer: Boolean get() = headerUsed > 0 || payload != null

    fun reset() {
        position = 0
        limit = 0
        headerUsed = 0
        payload = null
        payloadUsed = 0
        invalid = false
    }

    fun read(deadline: PtpReadDeadline): PtpContainer? {
        if (invalid) return null // Require explicit pipe recovery after malformed framing.
        while (headerUsed < header.size) {
            val copied = copyTo(header, headerUsed, header.size - headerUsed, deadline)
            if (copied <= 0) return null
            headerUsed += copied
        }
        if (payload == null) {
            val bb = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
            val length = bb.int
            type = bb.short.toInt() and 0xFFFF
            code = bb.short.toInt() and 0xFFFF
            transactionId = bb.int
            val validTypeAndSize = when (type) {
                PtpConstants.CONTAINER_TYPE_DATA -> length <= maxContainerLength
                PtpConstants.CONTAINER_TYPE_RESPONSE -> length <= header.size + 20 && (length - header.size) % 4 == 0
                PtpConstants.CONTAINER_TYPE_EVENT -> length <= header.size + 12 && (length - header.size) % 4 == 0
                else -> false
            }
            if (length < header.size || !validTypeAndSize) {
                invalid = true
                return null
            }
            payload = ByteArray(length - header.size)
        }
        val body = checkNotNull(payload)
        while (payloadUsed < body.size) {
            val copied = copyTo(body, payloadUsed, body.size - payloadUsed, deadline)
            if (copied <= 0) return null
            payloadUsed += copied
        }
        val result = PtpContainer(type, code, transactionId, body)
        headerUsed = 0
        payload = null
        payloadUsed = 0
        return result
    }

    private fun copyTo(target: ByteArray, offset: Int, length: Int, deadline: PtpReadDeadline): Int {
        var emptyReads = 0
        while (position == limit) {
            val timeout = deadline.remainingMs()
            if (timeout == 0) return 0
            val count = readTransfer(input, timeout)
            if (count < 0) return 0 // A failed USB read is not a reason to busy-spin.
            if (count == 0) {
                // Some bodies terminate exact-packet data with a zero-length packet.
                if (++emptyReads >= 4) return 0
                continue
            }
            if (count > input.size) {
                invalid = true
                return 0
            }
            position = 0
            limit = count
        }
        val count = minOf(length, limit - position)
        input.copyInto(target, offset, position, position + count)
        position += count
        return count
    }
}
