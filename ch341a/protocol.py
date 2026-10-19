"""CH341A USB-to-SPI protocol constants.

Reverse-engineered protocol as implemented by the flashrom project's
``ch341a_spi.c`` programmer driver.
"""

USB_VENDOR_ID = 0x1A86
USB_PRODUCT_ID = 0x5512

WRITE_EP = 0x02
READ_EP = 0x82

# flashrom's ch341a_spi.c uses a 1000ms timeout too, but that covers each
# individual 32-byte USB packet on a *fast* SPI clock. At the STM_I2C_100K
# stream speed this driver configures (see device.py's open()), clocking a
# full 4 KiB read chunk can run close to that budget even in the best case,
# and cheap CH341A clones/hubs add enough jitter on top that reads started
# timing out on real hardware (see ch341a/device.py's USBError -> CH341AError
# wrapping). Give individual transfers more headroom.
USB_TIMEOUT_MS = 5000

PACKET_LENGTH = 0x20  # 32 bytes: 1 command byte + up to 31 payload bytes
PACKET_PAYLOAD = PACKET_LENGTH - 1

CMD_SET_OUTPUT = 0xA1
CMD_IO_ADDR = 0xA2
CMD_PRINT_OUT = 0xA3
CMD_SPI_STREAM = 0xA8
CMD_SIO_STREAM = 0xA9
CMD_I2C_STREAM = 0xAA
CMD_UIO_STREAM = 0xAB

CMD_I2C_STM_START = 0x74
CMD_I2C_STM_STOP = 0x75
CMD_I2C_STM_OUT = 0x80
CMD_I2C_STM_IN = 0xC0
CMD_I2C_STM_SET = 0x60
CMD_I2C_STM_US = 0x40
CMD_I2C_STM_MS = 0x50
CMD_I2C_STM_DLY = 0x0F
CMD_I2C_STM_END = 0x00

CMD_UIO_STM_IN = 0x00
CMD_UIO_STM_DIR = 0x40
CMD_UIO_STM_OUT = 0x80
CMD_UIO_STM_US = 0xC0
CMD_UIO_STM_END = 0x20

STM_I2C_20K = 0x00
STM_I2C_100K = 0x01
STM_I2C_400K = 0x02
STM_I2C_750K = 0x03
STM_SPI_DBL = 0x04

# UIO pin mask used by enable_pins()/pluck_cs(): bit0 = CS0 (the only CS we
# drive), bit3 = SCK, bit5 = DOUT, all default to "idle" (high) except CS
# which toggles low while asserted.
UIO_PINS_IDLE = 0x37  # CS deasserted (active-low), SCK=0, DOUT=1
UIO_PINS_CS_ASSERTED = 0x36
UIO_PINS_OUTPUT_ENABLE_MASK = 0x3F


def _build_reverse_table():
    table = []
    for byte in range(256):
        reversed_byte = 0
        for bit in range(8):
            if byte & (1 << bit):
                reversed_byte |= 1 << (7 - bit)
        table.append(reversed_byte)
    return tuple(table)


REVERSE_BYTE_TABLE = _build_reverse_table()


def reverse_byte(byte: int) -> int:
    """Bit-reverse a single byte (CH341A shifts SPI data LSB-first on the wire)."""
    return REVERSE_BYTE_TABLE[byte]


def reverse_bytes(data: bytes) -> bytes:
    return bytes(REVERSE_BYTE_TABLE[b] for b in data)
