#!/usr/bin/env python3
"""JS8's CRC-12 -- augmented, poly 0xC06, init 0, final XOR 42.

See docs/js8/PROTOCOL.md section 3. This is an *augmented* CRC (the message is
shifted through the register with no explicit zero-tail append, matching
boost::crc_basic's behaviour as JS8.cpp uses it), computed over all 11 bytes of
the information block WITH THE CRC FIELD ZEROED, then XORed with 42.

Field placement inside the 11-byte block:
    byte 9  bits 4..0  <- crc bits 11..7
    byte 10 bits 7..1  <- crc bits 6..0
    byte 9  bits 7..5  are the i3bit transmission flags and must be preserved.

The lone unused bit is byte 10 bit 0 (information bit 87), always 0.
"""
try:
    from . import spec
except ImportError:  # loaded standalone by the test harness
    import importlib.util
    import os

    _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec.py")
    _s = importlib.util.spec_from_file_location("js8_spec", _p)
    spec = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(spec)

BLOCK_BYTES = 11
_MASK = (1 << 12) - 1


def crc12(data11):
    """Bit-by-bit reference implementation. Returns the 12-bit CRC."""
    data11 = bytes(data11)
    if len(data11) != BLOCK_BYTES:
        raise ValueError(f"CRC-12 covers exactly {BLOCK_BYTES} bytes, got {len(data11)}")
    rem = spec.CRC_INIT
    for byte in data11:
        for i in range(7, -1, -1):
            top = (rem >> 11) & 1
            rem = ((rem << 1) | ((byte >> i) & 1)) & _MASK
            if top:
                rem ^= spec.CRC_POLY
    return rem ^ spec.CRC_FINAL_XOR


def _build_table():
    """T[h] = (h * x^12) mod G(x), where G(x) = x^12 + CRC_POLY.

    Derived from polynomial algebra rather than by replaying the bit loop, so
    that crc12_table_driven() is a genuinely independent second opinion.
    """
    table = []
    for h in range(256):
        v = h
        for _ in range(12):
            v <<= 1
            if v & (1 << 12):  # x^12 is congruent to CRC_POLY mod G
                v = (v ^ (1 << 12)) ^ spec.CRC_POLY
        table.append(v & _MASK)
    return tuple(table)


_TABLE = _build_table()


def crc12_table_driven(data11):
    """Byte-at-a-time equivalent of crc12().

    One step of crc12() is r <- r*x + b (mod G), so eight steps are
    r <- r*x^8 + B (mod G). Splitting r into its top 8 bits r_hi and low 4
    bits r_lo gives r*x^8 = r_hi*x^12 + r_lo*x^8, hence

        r' = T[r_hi] XOR (r_lo << 8) XOR byte

    Note the message byte does NOT fold into the table index: that shortcut
    only applies when message bits enter at the TOP of the register, and JS8's
    augmented CRC feeds them in at the bottom.
    """
    data11 = bytes(data11)
    if len(data11) != BLOCK_BYTES:
        raise ValueError(f"CRC-12 covers exactly {BLOCK_BYTES} bytes, got {len(data11)}")
    rem = spec.CRC_INIT
    for byte in data11:
        rem = (_TABLE[rem >> 4] ^ ((rem & 0xF) << 8) ^ byte) & _MASK
    return rem ^ spec.CRC_FINAL_XOR


def insert(block):
    """Return a copy of an 11-byte block with its CRC field filled in.

    The CRC field must already be zero on input -- that is what gets checksummed.
    """
    b = bytearray(block)
    if len(b) != BLOCK_BYTES:
        raise ValueError(f"block must be {BLOCK_BYTES} bytes, got {len(b)}")
    if (b[9] & 0x1F) or b[10]:
        raise ValueError("CRC field must be zero before insertion")
    crc = crc12(bytes(b))
    b[9] |= (crc >> 7) & 0x1F
    b[10] = (crc & 0x7F) << 1
    return bytes(b)


def extract(block):
    """Recover the 12-bit CRC carried in an 11-byte block."""
    b = bytes(block)
    if len(b) != BLOCK_BYTES:
        raise ValueError(f"block must be {BLOCK_BYTES} bytes, got {len(b)}")
    return ((b[9] & 0x1F) << 7) | (b[10] >> 1)


def verify(block):
    """True if the block's CRC field matches a recomputation over the block."""
    b = bytearray(block)
    if len(b) != BLOCK_BYTES:
        raise ValueError(f"block must be {BLOCK_BYTES} bytes, got {len(b)}")
    carried = extract(bytes(b))
    b[9] &= 0xE0  # clear the CRC field, keep the i3bit flags
    b[10] = 0
    return crc12(bytes(b)) == carried
