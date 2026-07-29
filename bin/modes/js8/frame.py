#!/usr/bin/env python3
"""JS8 frame codec: 12 payload characters <-> 79 channel symbols.

A direct port of JS8::encode (JS8_Mode/JS8.cpp), specified in
docs/js8/PROTOCOL.md section 7, plus the exact inverse for the receive path.

    12 chars -> 72 bits -+
                         +-> 87-bit block -> CRC-12 -> LDPC -> 79 tones
    i3bit    ->  3 bits -+

This module is deliberately the boundary between "bits" and "tones". It knows
nothing about audio (that is synth.py, P3) and nothing about what the twelve
characters MEAN (that is varicode.py, P2) -- callers at this level hand it raw
alphabet characters.

decode_tones() is hard-decision only: it is the loopback inverse used to prove
the encoder, not the real receiver. The soft-decision demodulator and
belief-propagation decoder that handle actual noisy signals are P4.
"""
from dataclasses import dataclass

try:
    from . import crc12 as _crc12
    from . import ldpc as _ldpc
    from . import spec
except ImportError:  # loaded standalone by the test harness
    import importlib.util
    import os

    _DIR = os.path.dirname(os.path.abspath(__file__))

    def _load(name):
        p = os.path.join(_DIR, name + ".py")
        s = importlib.util.spec_from_file_location("js8_" + name, p)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        return m

    spec = _load("spec")
    _crc12 = _load("crc12")
    _ldpc = _load("ldpc")


@dataclass(frozen=True)
class DecodedFrame:
    """What a successfully CRC-checked frame carried."""

    message: str  # the 12 raw alphabet characters
    i3bit: int  # transmission flags (First/Last/Data), NOT the frame type


def _submode(submode):
    try:
        return spec.SUBMODES[submode]
    except KeyError:
        raise ValueError(
            f"unknown submode {submode!r}; valid ids are {sorted(spec.SUBMODES)}"
        ) from None


def pack_message(message):
    """12 alphabet characters -> the first 9 bytes of the information block."""
    if len(message) != spec.MSG_CHARS:
        raise ValueError(
            f"message must be exactly {spec.MSG_CHARS} characters, got {len(message)}"
        )
    out = bytearray(9)
    for src, dst in ((0, 0), (4, 3), (8, 6)):
        w = 0
        for k in range(4):
            ch = message[src + k]
            try:
                w = (w << 6) | spec.ALPHABET_INDEX[ch]
            except KeyError:
                raise ValueError(
                    f"character {ch!r} is not in the JS8 payload alphabet"
                ) from None
        out[dst] = (w >> 16) & 0xFF
        out[dst + 1] = (w >> 8) & 0xFF
        out[dst + 2] = w & 0xFF
    return bytes(out)


def unpack_message(block9):
    """The inverse of pack_message()."""
    if len(block9) != 9:
        raise ValueError(f"expected 9 payload bytes, got {len(block9)}")
    chars = []
    for src in (0, 3, 6):
        w = (block9[src] << 16) | (block9[src + 1] << 8) | block9[src + 2]
        for shift in (18, 12, 6, 0):
            chars.append(spec.ALPHABET[(w >> shift) & 0x3F])
    return "".join(chars)


def build_block(i3bit, message):
    """The complete, CRC-stamped 11-byte information block."""
    if not isinstance(i3bit, int) or isinstance(i3bit, bool):
        raise ValueError(f"i3bit must be an int, got {i3bit!r}")
    if not 0 <= i3bit <= 7:
        raise ValueError(f"i3bit must be 0..7, got {i3bit}")
    block = bytearray(pack_message(message) + bytes(2))
    block[9] = (i3bit & 0b111) << 5
    return _crc12.insert(bytes(block))


def message_bits(i3bit, message):
    """The 87 information bits: 72 payload, 3 type, 12 CRC."""
    block = build_block(i3bit, message)
    return [(block[k // 8] >> (7 - (k % 8))) & 1 for k in range(spec.INFO_BITS)]


def encode(i3bit, message, submode=spec.SUBMODE_NORMAL):
    """12 characters + transmission flags -> 79 channel symbols (tones 0..7)."""
    sm = _submode(submode)
    codeword = _ldpc.encode(message_bits(i3bit, message))

    tones = [0] * spec.NN
    for block, start in enumerate(spec.COSTAS_POS):
        tones[start : start + 7] = list(sm.costas[block])

    # Parity is transmitted before the message: symbols 7..35, then 43..71.
    parity_bits = codeword[: spec.LDPC_M]
    msg_bits = codeword[spec.LDPC_M :]
    for s in range(29):
        i = 3 * s
        tones[7 + s] = (parity_bits[i] << 2) | (parity_bits[i + 1] << 1) | parity_bits[i + 2]
        tones[43 + s] = (msg_bits[i] << 2) | (msg_bits[i + 1] << 1) | msg_bits[i + 2]
    return tones


def decode_tones(tones, submode=spec.SUBMODE_NORMAL, check_costas=False):
    """79 hard-decision tones -> DecodedFrame, or None if the CRC fails.

    This is the encoder's exact inverse, for loopback proof. It does not
    error-correct: any symbol error propagates to the CRC and the frame is
    rejected. Real reception (soft symbols -> LLRs -> belief propagation) is
    P4's decode.py.
    """
    tones = list(tones)
    if len(tones) != spec.NN:
        raise ValueError(f"expected {spec.NN} tones, got {len(tones)}")
    for t in tones:
        if t not in range(8):
            raise ValueError(f"tone values must be 0..7, found {t!r}")

    sm = _submode(submode)
    if check_costas:
        for block, start in enumerate(spec.COSTAS_POS):
            if tones[start : start + 7] != list(sm.costas[block]):
                return None

    bits = []
    for s in range(29):  # message symbols only -- parity is not needed to read it
        t = tones[43 + s]
        bits.extend(((t >> 2) & 1, (t >> 1) & 1, t & 1))
    bits = bits[: spec.INFO_BITS]

    block = bytearray(11)
    for k, bit in enumerate(bits):
        if bit:
            block[k // 8] |= 1 << (7 - (k % 8))

    if not _crc12.verify(bytes(block)):
        return None
    return DecodedFrame(message=unpack_message(bytes(block[:9])),
                        i3bit=(block[9] >> 5) & 0b111)
