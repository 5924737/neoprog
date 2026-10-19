import pytest

from ch341a.device import CH341AError
from ch341a.spi_flash import CMD_PAGE_PROGRAM, PAGE_SIZE, SECTOR_SIZE, SpiFlash

from .fake_device import FakeSpiFlash


def test_read_id():
    fake = FakeSpiFlash(size=1 << 24, manufacturer=0xC8, memory_type=0x40, capacity_code=0x18)
    info = SpiFlash(fake).read_id()
    assert info.manufacturer == 0xC8
    assert info.memory_type == 0x40
    assert info.capacity_code == 0x18
    assert info.size_bytes == 1 << 24


def test_read_returns_erased_state():
    fake = FakeSpiFlash(size=SECTOR_SIZE * 2)
    flash = SpiFlash(fake)
    assert flash.read(0, 16) == b"\xff" * 16


def test_write_then_read_back():
    fake = FakeSpiFlash(size=SECTOR_SIZE * 2)
    flash = SpiFlash(fake)
    data = bytes(range(256)) * 4  # 1024 bytes, spans several pages
    flash.write(0, data)
    assert flash.read(0, len(data)) == data
    assert fake.erase_calls == [("sector", 0)]


def test_write_only_erases_covered_sectors():
    fake = FakeSpiFlash(size=SECTOR_SIZE * 4)
    flash = SpiFlash(fake)
    data = b"\x11" * 10
    flash.write(SECTOR_SIZE + 5, data, verify=False)
    assert fake.erase_calls == [("sector", SECTOR_SIZE)]
    assert fake.mem[:SECTOR_SIZE] == b"\xff" * SECTOR_SIZE
    assert fake.mem[3 * SECTOR_SIZE:] == b"\xff" * SECTOR_SIZE


def test_write_spanning_sector_boundary_erases_both_sectors():
    fake = FakeSpiFlash(size=SECTOR_SIZE * 3)
    flash = SpiFlash(fake)
    addr = SECTOR_SIZE - 10
    data = b"\xaa" * 20
    flash.write(addr, data, verify=False)
    assert fake.erase_calls == [("sector", 0), ("sector", SECTOR_SIZE)]
    assert flash.read(addr, len(data)) == data


def test_page_program_never_crosses_page_boundary():
    fake = FakeSpiFlash(size=SECTOR_SIZE)
    flash = SpiFlash(fake)
    addr = 250  # 6 bytes before the next 256-byte page boundary
    data = bytes(range(20))
    flash.write(addr, data, verify=False)
    for prog_addr, chunk in fake.program_calls:
        assert prog_addr // PAGE_SIZE == (prog_addr + len(chunk) - 1) // PAGE_SIZE
    assert flash.read(addr, len(data)) == data


def test_write_verify_failure_raises():
    class FlakyFakeSpiFlash(FakeSpiFlash):
        def spi_transfer(self, write_bytes: bytes = b"", read_len: int = 0) -> bytes:
            result = super().spi_transfer(write_bytes, read_len)
            if write_bytes and write_bytes[0] == CMD_PAGE_PROGRAM:
                addr = self._addr(write_bytes)
                self.mem[addr] ^= 0x01  # corrupt one bit after programming
            return result

    fake = FlakyFakeSpiFlash(size=SECTOR_SIZE)
    flash = SpiFlash(fake)
    with pytest.raises(CH341AError):
        flash.write(0, b"\x42" * 10, verify=True)


def test_erase_chip():
    fake = FakeSpiFlash(size=SECTOR_SIZE * 2)
    fake.mem[:] = b"\x00" * len(fake.mem)
    SpiFlash(fake).erase_chip()
    assert fake.mem == b"\xff" * len(fake.mem)


def test_write_and_erase_chip_clear_protection_bits_first():
    fake = FakeSpiFlash(size=SECTOR_SIZE)
    fake.status_register = 0x3C  # BP0-BP4 set, as a chip might ship configured
    SpiFlash(fake).write(0, b"\x11" * 4, verify=False)
    assert fake.status_writes == [0x00]

    fake2 = FakeSpiFlash(size=SECTOR_SIZE)
    fake2.status_register = 0x3C
    SpiFlash(fake2).erase_chip()
    assert fake2.status_writes == [0x00]
