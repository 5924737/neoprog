from ch341a import protocol as p
from ch341a.device import CH341ADevice


def test_reverse_byte_is_involution():
    for byte in range(256):
        assert p.reverse_byte(p.reverse_byte(byte)) == byte


def test_reverse_byte_known_values():
    assert p.reverse_byte(0x01) == 0x80
    assert p.reverse_byte(0x80) == 0x01
    assert p.reverse_byte(0xF0) == 0x0F
    assert p.reverse_byte(0x00) == 0x00
    assert p.reverse_byte(0xFF) == 0xFF


class RecordingLink(CH341ADevice):
    """A CH341ADevice whose USB I/O is stubbed out for framing tests."""

    def __init__(self, read_data: bytes):
        super().__init__()
        self.written = bytearray()
        self._read_data = bytearray(read_data)

    def _bulk_write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def _bulk_read(self, length: int) -> bytes:
        chunk = bytes(self._read_data[:length])
        del self._read_data[:length]
        return chunk


def test_spi_transfer_cs_packet_and_single_packet_framing():
    write_bytes = bytes([0x9F])  # JEDEC READ_ID
    read_len = 3
    # Device echoes back reverse_byte(0) for the command byte and the JEDEC
    # ID bytes (pre-reversed, since spi_transfer reverses them back).
    raw_in = bytes([0x00]) + bytes(p.reverse_byte(b) for b in (0xC8, 0x40, 0x18))
    link = RecordingLink(raw_in)

    result = link.spi_transfer(write_bytes, read_len)

    cs_packet = bytes(link.written[:p.PACKET_LENGTH])
    assert cs_packet[0] == p.CMD_UIO_STREAM
    assert cs_packet[1] == (p.CMD_UIO_STM_OUT | p.UIO_PINS_IDLE)
    assert cs_packet[2] == (p.CMD_UIO_STM_OUT | p.UIO_PINS_CS_ASSERTED)
    assert cs_packet[3] == p.CMD_UIO_STM_END
    assert cs_packet[4:] == bytes(p.PACKET_LENGTH - 4)

    spi_packet = bytes(link.written[p.PACKET_LENGTH:])
    assert spi_packet[0] == p.CMD_SPI_STREAM
    assert spi_packet[1] == p.reverse_byte(0x9F)
    assert spi_packet[2:2 + read_len] == b"\xff" * read_len

    assert result == bytes([0xC8, 0x40, 0x18])


def test_spi_transfer_splits_across_multiple_packets():
    write_bytes = bytes(range(35))
    read_len = 5
    total = len(write_bytes) + read_len
    link = RecordingLink(bytes(total))  # all-zero echo

    result = link.spi_transfer(write_bytes, read_len)

    assert len(link.written) == p.PACKET_LENGTH + 32 + 10

    packet1 = link.written[p.PACKET_LENGTH:p.PACKET_LENGTH + 32]
    assert packet1[0] == p.CMD_SPI_STREAM
    assert bytes(packet1[1:32]) == bytes(p.reverse_byte(b) for b in write_bytes[:31])

    packet2 = link.written[p.PACKET_LENGTH + 32:]
    assert packet2[0] == p.CMD_SPI_STREAM
    assert bytes(packet2[1:5]) == bytes(p.reverse_byte(b) for b in write_bytes[31:35])
    assert bytes(packet2[5:10]) == b"\xff" * 5

    assert result == b"\x00" * read_len


def test_spi_transfer_no_data_only_pulses_cs():
    link = RecordingLink(b"")
    result = link.spi_transfer(b"", 0)
    assert len(link.written) == p.PACKET_LENGTH
    assert link.written[0] == p.CMD_UIO_STREAM
    assert link.written[3] == p.CMD_UIO_STM_END
    assert bytes(link.written[4:]) == bytes(p.PACKET_LENGTH - 4)
    assert result == b""


def test_spi_transfer_write_only_multi_packet_burst():
    """A page-program-shaped transfer: CMD + 3 address bytes + 256 data
    bytes, no read phase - exactly what SpiFlash._page_program() sends.
    Even with read_len=0, the device still echoes one byte per byte
    clocked during the write phase, so every packet must still be drained
    by a matching read (this is what previously hung real hardware when
    the whole burst was queued before any of it was read back)."""
    write_bytes = bytes([0x02, 0x00, 0x10, 0x00]) + bytes(range(256))
    total = len(write_bytes)
    link = RecordingLink(bytes(total))  # all-zero echo, discarded anyway

    result = link.spi_transfer(write_bytes, 0)

    # CS packet + ceil(260/31) = 9 SPI_STREAM packets, none carrying any
    # 0xFF read filler.
    packet_count = -(-total // p.PACKET_PAYLOAD)
    assert packet_count == 9
    body = link.written[p.PACKET_LENGTH:]
    offset = 0
    write_offset = 0
    for i in range(packet_count):
        expected_len = min(p.PACKET_PAYLOAD, total - write_offset)
        packet = body[offset:offset + 1 + expected_len]
        assert packet[0] == p.CMD_SPI_STREAM
        assert bytes(packet[1:]) == bytes(
            p.reverse_byte(b) for b in write_bytes[write_offset:write_offset + expected_len]
        )
        offset += 1 + expected_len
        write_offset += expected_len
    assert offset == len(body)
    assert result == b""


def test_spi_transfer_interleaves_write_and_read_per_packet():
    """Regression test for the real-hardware hang this fixed: writes and
    reads for a multi-packet burst must alternate per packet, never queue
    every write packet before draining any of the reads."""

    class OrderTrackingLink(RecordingLink):
        def __init__(self, read_data: bytes):
            super().__init__(read_data)
            self.calls: list[tuple[str, int]] = []

        def _bulk_write(self, data: bytes) -> int:
            self.calls.append(("write", len(data)))
            return super()._bulk_write(data)

        def _bulk_read(self, length: int) -> bytes:
            self.calls.append(("read", length))
            return super()._bulk_read(length)

    write_bytes = bytes(range(35))
    read_len = 5
    total = len(write_bytes) + read_len
    link = OrderTrackingLink(bytes(total))

    link.spi_transfer(write_bytes, read_len)

    # CS pulse write, then per SPI packet: one write immediately followed
    # by its read - never two writes (or two reads) in a row.
    assert link.calls[0] == ("write", p.PACKET_LENGTH)
    kinds = [kind for kind, _ in link.calls[1:]]
    assert kinds == ["write", "read", "write", "read"]
