"""In-memory SPI NOR flash emulator.

Satisfies the same ``spi_transfer(write_bytes, read_len) -> bytes`` interface
that :class:`ch341a.device.CH341ADevice` exposes, so :class:`ch341a.spi_flash.SpiFlash`
can be exercised without any real CH341A hardware attached.
"""

from __future__ import annotations

from ch341a.spi_flash import (
    CMD_BLOCK_ERASE_64K,
    CMD_CHIP_ERASE,
    CMD_PAGE_PROGRAM,
    CMD_READ,
    CMD_READ_ID,
    CMD_READ_STATUS1,
    CMD_SECTOR_ERASE_4K,
    CMD_WRITE_DISABLE,
    CMD_WRITE_ENABLE,
    CMD_WRITE_STATUS1,
    SECTOR_SIZE,
)

BLOCK_SIZE = 64 * 1024


class FakeSpiFlash:
    def __init__(
        self,
        size: int = 1 << 20,
        manufacturer: int = 0xC8,
        memory_type: int = 0x40,
        capacity_code: int = 20,
    ):
        self.mem = bytearray(b"\xff" * size)
        self.manufacturer = manufacturer
        self.memory_type = memory_type
        self.capacity_code = capacity_code
        self._write_enabled = False
        self.status_register = 0x00
        self.erase_calls: list[tuple[str, int]] = []
        self.program_calls: list[tuple[int, bytes]] = []
        self.status_writes: list[int] = []

    def spi_transfer(self, write_bytes: bytes = b"", read_len: int = 0) -> bytes:
        cmd = write_bytes[0] if write_bytes else None

        if cmd == CMD_READ_ID:
            return bytes([self.manufacturer, self.memory_type, self.capacity_code])[:read_len]

        if cmd == CMD_READ_STATUS1:
            return bytes([self.status_register])[:read_len]

        if cmd == CMD_WRITE_STATUS1:
            self._consume_wel()
            self.status_register = write_bytes[1]
            self.status_writes.append(self.status_register)
            return b""

        if cmd == CMD_WRITE_ENABLE:
            self._write_enabled = True
            return b""

        if cmd == CMD_WRITE_DISABLE:
            self._write_enabled = False
            return b""

        if cmd == CMD_SECTOR_ERASE_4K:
            addr = self._addr(write_bytes)
            self._consume_wel()
            self.mem[addr:addr + SECTOR_SIZE] = b"\xff" * SECTOR_SIZE
            self.erase_calls.append(("sector", addr))
            return b""

        if cmd == CMD_BLOCK_ERASE_64K:
            addr = self._addr(write_bytes)
            self._consume_wel()
            self.mem[addr:addr + BLOCK_SIZE] = b"\xff" * BLOCK_SIZE
            self.erase_calls.append(("block", addr))
            return b""

        if cmd == CMD_CHIP_ERASE:
            self._consume_wel()
            self.mem[:] = b"\xff" * len(self.mem)
            self.erase_calls.append(("chip", 0))
            return b""

        if cmd == CMD_PAGE_PROGRAM:
            addr = self._addr(write_bytes)
            data = write_bytes[4:]
            self._consume_wel()
            for i, byte in enumerate(data):
                self.mem[addr + i] &= byte  # NOR program can only clear bits
            self.program_calls.append((addr, bytes(data)))
            return b""

        if cmd == CMD_READ:
            addr = self._addr(write_bytes)
            return bytes(self.mem[addr:addr + read_len])

        raise AssertionError(f"Unexpected SPI command bytes: {write_bytes!r}")

    @staticmethod
    def _addr(write_bytes: bytes) -> int:
        return (write_bytes[1] << 16) | (write_bytes[2] << 8) | write_bytes[3]

    def _consume_wel(self) -> None:
        if not self._write_enabled:
            raise AssertionError("Write/erase attempted without WRITE_ENABLE first")
        self._write_enabled = False
