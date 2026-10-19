"""Low-level CH341A USB-to-SPI transport.

Talks to the CH341A chip directly over USB bulk transfers, replicating the
packet framing used by flashrom's ``ch341a_spi.c`` programmer driver (see
that file for the original, GPLv2-licensed reference implementation this
was reverse-engineered from).
"""

from __future__ import annotations

import usb.core
import usb.util

from . import protocol as p


class CH341AError(Exception):
    """Raised for CH341A/USB communication failures."""


class CH341ANotFoundError(CH341AError):
    """Raised when no CH341A device is plugged in."""


class CH341ADevice:
    def __init__(self, vendor_id: int = p.USB_VENDOR_ID, product_id: int = p.USB_PRODUCT_ID):
        self._vendor_id = vendor_id
        self._product_id = product_id
        self._dev = None
        self._detached_kernel_driver = False

    def open(self) -> None:
        dev = usb.core.find(idVendor=self._vendor_id, idProduct=self._product_id)
        if dev is None:
            raise CH341ANotFoundError(
                f"CH341A device {self._vendor_id:04x}:{self._product_id:04x} not found. "
                "Is the programmer plugged in?"
            )
        self._dev = dev

        if hasattr(dev, "is_kernel_driver_active"):
            try:
                if dev.is_kernel_driver_active(0):
                    dev.detach_kernel_driver(0)
                    self._detached_kernel_driver = True
            except (usb.core.USBError, NotImplementedError):
                pass

        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # already configured

        usb.util.claim_interface(dev, 0)

        self.config_stream(p.STM_I2C_100K)
        self.enable_pins(True)

    def close(self) -> None:
        if self._dev is None:
            return
        try:
            self.enable_pins(False)
        except CH341AError:
            pass
        try:
            usb.util.release_interface(self._dev, 0)
        except usb.core.USBError:
            pass
        if self._detached_kernel_driver and hasattr(self._dev, "attach_kernel_driver"):
            try:
                self._dev.attach_kernel_driver(0)
            except usb.core.USBError:
                pass
        usb.util.dispose_resources(self._dev)
        self._dev = None

    def __enter__(self) -> "CH341ADevice":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -- raw USB I/O, isolated so tests can stub them out ------------------

    def _bulk_write(self, data: bytes) -> int:
        try:
            return self._dev.write(p.WRITE_EP, data, timeout=p.USB_TIMEOUT_MS)
        except usb.core.USBError as exc:
            # usb.core.USBError subclasses OSError/IOError, so if this were
            # left to propagate as-is it would get silently caught by
            # unrelated `except OSError` handlers upstream (e.g. app.py's
            # file-save error handler) and misreported as a file I/O
            # failure instead of a USB communication failure.
            raise CH341AError(f"USB write failed: {exc}") from exc

    def _bulk_read(self, length: int) -> bytes:
        try:
            return bytes(self._dev.read(p.READ_EP, length, timeout=p.USB_TIMEOUT_MS))
        except usb.core.USBError as exc:
            raise CH341AError(f"USB read failed: {exc}") from exc

    # -- CH341A control commands --------------------------------------------

    def config_stream(self, speed: int = p.STM_I2C_100K) -> None:
        buf = bytes([p.CMD_I2C_STREAM, p.CMD_I2C_STM_SET | (speed & 0x7), p.CMD_I2C_STM_END])
        self._bulk_write(buf)

    def enable_pins(self, enable: bool) -> None:
        buf = bytes([
            p.CMD_UIO_STREAM,
            p.CMD_UIO_STM_OUT | p.UIO_PINS_IDLE,
            p.CMD_UIO_STM_DIR | (p.UIO_PINS_OUTPUT_ENABLE_MASK if enable else 0x00),
            p.CMD_UIO_STM_END,
        ])
        self._bulk_write(buf)

    @staticmethod
    def _pluck_cs_packet() -> bytes:
        """One CS deassert->assert pulse, padded to a full 32-byte packet."""
        packet = bytearray(p.PACKET_LENGTH)
        packet[0] = p.CMD_UIO_STREAM
        packet[1] = p.CMD_UIO_STM_OUT | p.UIO_PINS_IDLE
        packet[2] = p.CMD_UIO_STM_OUT | p.UIO_PINS_CS_ASSERTED
        packet[3] = p.CMD_UIO_STM_END
        return bytes(packet)

    def spi_transfer(self, write_bytes: bytes = b"", read_len: int = 0) -> bytes:
        """Perform one CS-bracketed SPI transaction.

        Clocks out ``write_bytes`` then ``read_len`` filler bytes, returning
        the ``read_len`` bytes clocked in during the read phase. The device
        always echoes back one IN byte per OUT byte (full-duplex SPI), so
        the bytes captured during the write phase are discarded here.

        Each 32-byte SPI_STREAM packet is written and its echo read back
        immediately, one packet at a time, rather than queueing the whole
        multi-packet burst as one big bulk write before draining any of the
        IN side. flashrom's ch341a_spi.c gets the same effect by submitting
        the OUT and IN libusb transfers concurrently (async); pyusb only
        exposes blocking I/O, so interleaving per packet is how this reaches
        the same effect. Queuing everything up front (the previous approach
        here) let the device's small internal FIFO fill up faster than a
        multi-packet read could drain it, and it would stop responding
        entirely partway through a large read/write (observed as an IN
        bulk-transfer timeout on real hardware, even with a generous
        timeout) - while single-packet transactions like read_id() (which
        never queue more than one packet) always worked fine.
        """
        writecnt = len(write_bytes)

        self._bulk_write(bytes(self._pluck_cs_packet()))

        raw_in = bytearray()
        write_left = writecnt
        read_left = read_len
        offset = 0
        while write_left > 0 or read_left > 0:
            write_now = min(p.PACKET_PAYLOAD, write_left)
            read_now = min(p.PACKET_PAYLOAD - write_now, read_left)
            packet = bytearray()
            packet.append(p.CMD_SPI_STREAM)
            for i in range(write_now):
                packet.append(p.reverse_byte(write_bytes[offset + i]))
            offset += write_now
            write_left -= write_now
            if read_now:
                packet += bytes([0xFF]) * read_now
                read_left -= read_now

            self._bulk_write(bytes(packet))

            remaining = write_now + read_now
            while remaining > 0:
                chunk = min(p.PACKET_PAYLOAD, remaining)
                data = self._bulk_read(chunk)
                if not data:
                    raise CH341AError("USB read returned no data (timeout?)")
                raw_in += data
                remaining -= len(data)

        reversed_in = p.reverse_bytes(bytes(raw_in))
        return bytes(reversed_in[writecnt:])
