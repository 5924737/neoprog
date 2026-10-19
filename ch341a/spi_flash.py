"""JEDEC-compatible SPI NOR flash operations built on top of a CH341A SPI link."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from .device import CH341AError

CMD_READ_ID = 0x9F
CMD_READ_STATUS1 = 0x05
CMD_WRITE_STATUS1 = 0x01
CMD_WRITE_ENABLE = 0x06
CMD_WRITE_DISABLE = 0x04
CMD_PAGE_PROGRAM = 0x02
CMD_SECTOR_ERASE_4K = 0x20
CMD_BLOCK_ERASE_64K = 0xD8
CMD_CHIP_ERASE = 0xC7
CMD_READ = 0x03

PAGE_SIZE = 256
SECTOR_SIZE = 4096
READ_CHUNK = 4096

ProgressCallback = Optional[Callable[[int, int], None]]


@dataclass(frozen=True)
class ChipInfo:
    manufacturer: int
    memory_type: int
    capacity_code: int

    @property
    def size_bytes(self) -> int:
        # JEDEC convention: third ID byte is log2 of the chip size in bytes.
        if 10 <= self.capacity_code <= 34:
            return 1 << self.capacity_code
        return 0

    def __str__(self) -> str:
        size = self.size_bytes
        size_desc = f"{size // (1024 * 1024)} MiB" if size else "unknown size"
        return (
            f"manufacturer=0x{self.manufacturer:02X} "
            f"memory_type=0x{self.memory_type:02X} "
            f"capacity=0x{self.capacity_code:02X} ({size_desc})"
        )


def _addr_bytes(addr: int) -> bytes:
    return bytes([(addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF])


class SpiFlash:
    def __init__(self, device):
        self._device = device

    def read_id(self) -> ChipInfo:
        resp = self._device.spi_transfer(bytes([CMD_READ_ID]), 3)
        return ChipInfo(resp[0], resp[1], resp[2])

    def read_status(self) -> int:
        resp = self._device.spi_transfer(bytes([CMD_READ_STATUS1]), 1)
        return resp[0]

    def wait_busy(self, timeout: float = 30.0, poll_interval: float = 0.001) -> None:
        start = time.monotonic()
        while self.read_status() & 0x01:  # WIP bit
            if time.monotonic() - start > timeout:
                raise CH341AError("Timed out waiting for flash WIP bit to clear")
            time.sleep(poll_interval)

    def write_enable(self) -> None:
        self._device.spi_transfer(bytes([CMD_WRITE_ENABLE]), 0)

    def read(self, addr: int, length: int, progress_cb: ProgressCallback = None) -> bytes:
        out = bytearray()
        remaining = length
        cur = addr
        while remaining > 0:
            chunk_len = min(READ_CHUNK, remaining)
            cmd = bytes([CMD_READ]) + _addr_bytes(cur)
            out += self._device.spi_transfer(cmd, chunk_len)
            cur += chunk_len
            remaining -= chunk_len
            if progress_cb:
                progress_cb(length - remaining, length)
        return bytes(out)

    def read_all(self, progress_cb: ProgressCallback = None) -> bytes:
        info = self.read_id()
        if not info.size_bytes:
            raise CH341AError(
                f"Cannot determine chip size from JEDEC ID ({info}); "
                "use read(addr, length) with an explicit length instead."
            )
        return self.read(0, info.size_bytes, progress_cb=progress_cb)

    def unprotect(self) -> None:
        """Clears the status register (block-protect bits BP0-BP4 and SRP0).

        Chips commonly ship - or end up left - with these bits set, which
        silently blocks every erase/program command even after
        WRITE_ENABLE (the command completes as a no-op, no error is
        reported). Programmer tools like NeoProgrammer call this step
        "Off-Protect" and always run it before erasing/writing.
        """
        self.write_enable()
        self._device.spi_transfer(bytes([CMD_WRITE_STATUS1, 0x00]), 0)
        self.wait_busy()

    def erase_sector(self, addr: int) -> None:
        self.write_enable()
        self._device.spi_transfer(bytes([CMD_SECTOR_ERASE_4K]) + _addr_bytes(addr), 0)
        self.wait_busy()

    def erase_block(self, addr: int) -> None:
        self.write_enable()
        self._device.spi_transfer(bytes([CMD_BLOCK_ERASE_64K]) + _addr_bytes(addr), 0)
        self.wait_busy()

    def erase_chip(self, progress_cb: ProgressCallback = None) -> None:
        self.unprotect()
        self.write_enable()
        self._device.spi_transfer(bytes([CMD_CHIP_ERASE]), 0)
        self.wait_busy(timeout=200.0, poll_interval=0.05)
        if progress_cb:
            progress_cb(1, 1)

    def _page_program(self, addr: int, data: bytes) -> None:
        self.write_enable()
        self._device.spi_transfer(bytes([CMD_PAGE_PROGRAM]) + _addr_bytes(addr) + data, 0)
        self.wait_busy()

    def write(
        self,
        addr: int,
        data: bytes,
        verify: bool = True,
        progress_cb: ProgressCallback = None,
        verify_progress_cb: ProgressCallback = None,
    ) -> None:
        """Erase the sectors covered by [addr, addr+len(data)) and program data.

        Note: NOR flash erases in whole 4 KiB sectors, so if ``addr`` or the
        end of the range is not sector-aligned, existing bytes elsewhere in
        the boundary sectors are wiped to 0xFF too - this is a hardware
        limitation, not something the driver can avoid.

        ``progress_cb`` tracks the erase+program phase; ``verify_progress_cb``
        (only used when ``verify`` is true) tracks the separate read-back
        phase, since it can take about as long as the write itself and
        callers may want to show it as a distinct step.
        """
        if not data:
            return

        self.unprotect()

        end = addr + len(data)
        first_sector = addr - (addr % SECTOR_SIZE)
        last_sector = (end - 1) - ((end - 1) % SECTOR_SIZE)
        num_sectors = (last_sector - first_sector) // SECTOR_SIZE + 1

        # Erasing is weighted into the same progress scale as programming
        # (one sector-erase counts as much progress as writing SECTOR_SIZE
        # bytes) so callers see steady feedback through the whole call -
        # erasing every covered sector always happens first and can easily
        # take minutes on a full-chip write, and previously reported no
        # progress at all until it finished, which looked like a hang.
        total = num_sectors * SECTOR_SIZE + len(data)
        done = 0

        sector = first_sector
        while sector <= last_sector:
            self.erase_sector(sector)
            sector += SECTOR_SIZE
            done += SECTOR_SIZE
            if progress_cb:
                progress_cb(done, total)

        offset = 0
        cur_addr = addr
        while offset < len(data):
            page_len = min(PAGE_SIZE, len(data) - offset, PAGE_SIZE - (cur_addr % PAGE_SIZE))
            chunk = data[offset:offset + page_len]
            self._page_program(cur_addr, chunk)
            cur_addr += page_len
            offset += page_len
            done += page_len
            if progress_cb:
                progress_cb(done, total)

        if verify:
            readback = self.read(addr, len(data), progress_cb=verify_progress_cb)
            if readback != data:
                raise CH341AError("Verification failed: readback does not match written data")
