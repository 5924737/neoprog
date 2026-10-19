#!/usr/bin/env python3
"""Manual hardware test: verify write/erase on a confirmed-blank flash
region before trusting write() on real firmware.

Targets the first sector of the empty run at 0x091A000-0x0A4FFFF
(sectors 2330-2639), confirmed all-0xFF across three independent
full-chip dumps (dump.bin/dump1.bin/dump2.bin/dump3.bin) taken
2026-09-14. Only touches ONE sector (4096 bytes) - writing to a real
chip is destructive, so this tests the write path (interleaved SPI
packet transport, page-program page-boundary handling, sector erase,
read-back verify) with minimum blast radius, not the whole empty region.

Before writing anything it re-reads that sector and aborts if it isn't
still all-0xFF, in case the chip/wiring isn't the one the dumps came
from. After the write/verify round-trip it erases the sector back to
0xFF, leaving the chip exactly as it found it.

Usage: python scripts/hw_write_test.py
Requires the CH341A programmer connected via USB with the target chip
wired (JEDEC ID should read manufacturer=0xC8 memory_type=0x40
capacity=0x18, 16 MiB).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ch341a.device import CH341ADevice, CH341AError, CH341ANotFoundError
from ch341a.spi_flash import SECTOR_SIZE, SpiFlash

TEST_ADDR = 0x091A000  # first sector of the confirmed-empty run
EXPECTED_MANUFACTURER = 0xC8
EXPECTED_MEMORY_TYPE = 0x40
EXPECTED_CAPACITY_CODE = 0x18


def build_pattern(size: int) -> bytes:
    """A deterministic, non-trivial pattern (not just 0x00 or 0xFF) so a
    stuck-bit or off-by-one bug in the write path shows up as a mismatch
    instead of accidentally matching the erased/blank state."""
    header = b"CH341A-WRITE-TEST"
    body = bytes((i * 37 + 11) & 0xFF for i in range(size - len(header)))
    return header + body


def fail(msg: str) -> None:
    print(f"ОШИБКА: {msg}")
    sys.exit(1)


def main() -> None:
    print(f"Целевой сектор: 0x{TEST_ADDR:07X}-0x{TEST_ADDR + SECTOR_SIZE - 1:07X} ({SECTOR_SIZE} байт)")
    print()

    try:
        with CH341ADevice() as dev:
            flash = SpiFlash(dev)

            info = flash.read_id()
            print(f"Чип: {info}")
            if (info.manufacturer, info.memory_type, info.capacity_code) != (
                EXPECTED_MANUFACTURER,
                EXPECTED_MEMORY_TYPE,
                EXPECTED_CAPACITY_CODE,
            ):
                fail(
                    f"Неожиданный JEDEC ID (ожидался manufacturer=0x{EXPECTED_MANUFACTURER:02X} "
                    f"memory_type=0x{EXPECTED_MEMORY_TYPE:02X} capacity=0x{EXPECTED_CAPACITY_CODE:02X}). "
                    "Это не тот чип, для которого проверялась пустая область - прерываю, чтобы не затереть данные."
                )

            print("Проверка, что сектор действительно пуст (0xFF) перед записью...")
            before = flash.read(TEST_ADDR, SECTOR_SIZE)
            if before != b"\xff" * SECTOR_SIZE:
                nonempty = sum(1 for b in before if b != 0xFF)
                fail(
                    f"Сектор не пуст ({nonempty} байт из {SECTOR_SIZE} не 0xFF) - похоже, дамп устарел "
                    "или адрес неверный. Прерываю, не рискую перезаписать реальные данные."
                )
            print("  OK: сектор пуст.")

            pattern = build_pattern(SECTOR_SIZE)

            print(f"Запись {len(pattern)} байт тестового паттерна (write() со встроенной верификацией)...")
            flash.write(TEST_ADDR, pattern, verify=True)
            print("  OK: встроенная верификация в write() прошла.")

            print("Независимое повторное чтение и побайтовое сравнение...")
            readback = flash.read(TEST_ADDR, SECTOR_SIZE)
            if readback != pattern:
                mismatches = [i for i in range(SECTOR_SIZE) if readback[i] != pattern[i]]
                first = mismatches[0]
                fail(
                    f"Независимая проверка не прошла: {len(mismatches)} байт из {SECTOR_SIZE} не совпадают "
                    f"(первое расхождение на смещении 0x{first:X}: записали 0x{pattern[first]:02X}, "
                    f"прочитали 0x{readback[first]:02X})."
                )
            print("  OK: прочитанные данные полностью совпадают с записанными.")

            print("Стирание сектора обратно (возврат в исходное пустое состояние)...")
            flash.unprotect()
            flash.erase_sector(TEST_ADDR)
            after_erase = flash.read(TEST_ADDR, SECTOR_SIZE)
            if after_erase != b"\xff" * SECTOR_SIZE:
                nonempty = sum(1 for b in after_erase if b != 0xFF)
                fail(
                    f"После стирания сектор не пуст ({nonempty} байт из {SECTOR_SIZE} не 0xFF) - "
                    "стирание не сработало как ожидалось."
                )
            print("  OK: сектор снова полностью стёрт (0xFF), чип оставлен как был.")

    except CH341ANotFoundError:
        fail("Программатор не найден. Подключите CH341A и повторите.")
    except CH341AError as exc:
        fail(f"Ошибка связи с устройством: {exc}")

    print()
    print("ВСЁ ПРОШЛО УСПЕШНО: запись, чтение и стирание работают корректно.")


if __name__ == "__main__":
    main()
