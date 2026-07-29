#!/usr/bin/env python3
"""LDPC(174, 87) -- the FEC layer of JS8. Encode side (P1).

See docs/js8/PROTOCOL.md section 4. Rate 1/2, regular degree 3 on the variable
side. Parity is generated from the dense generator G:

    p_i = XOR_j ( G[i][j] AND m_j )     for i = 0..86

and the transmitted codeword is [parity | message] -- parity FIRST. That
ordering matters twice over: it decides the symbol layout in frame.py, and it
decides that LLR indices 0..86 are parity and 87..173 are message on the RX
side (P4).

Belief-propagation decoding lands in P4; this module carries the syndrome
helper it will need, because syndrome() is also what proves the tables right
today (see tools/test_js8_frame.py).
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

_GENERATOR_WORDS = None


def _generator_words():
    """G rows packed as ints, so parity is 87 popcounts instead of 87*87 ANDs."""
    global _GENERATOR_WORDS
    if _GENERATOR_WORDS is None:
        words = []
        for row in spec.generator_rows():
            v = 0
            for bit in row:
                v = (v << 1) | bit
            words.append(v)
        _GENERATOR_WORDS = words
    return _GENERATOR_WORDS


def _check_message(message):
    if len(message) != spec.LDPC_K:
        raise ValueError(f"message must be {spec.LDPC_K} bits, got {len(message)}")
    for b in message:
        if b not in (0, 1):
            raise ValueError(f"message bits must be 0 or 1, found {b!r}")


def parity(message):
    """The 87 parity bits for an 87-bit message."""
    _check_message(message)
    m = 0
    for bit in message:
        m = (m << 1) | bit
    return [bin(row & m).count("1") & 1 for row in _generator_words()]


def encode(message):
    """The full 174-bit codeword, [parity | message]."""
    return parity(message) + list(message)


def syndrome(codeword):
    """Per-check parity of a 174-bit codeword. All zeros iff it is a codeword."""
    if len(codeword) != spec.LDPC_N:
        raise ValueError(f"codeword must be {spec.LDPC_N} bits, got {len(codeword)}")
    out = []
    for neighbors in spec.NM:
        s = 0
        for bit in neighbors:
            s ^= codeword[bit]
        out.append(s)
    return out


def is_codeword(codeword):
    return not any(syndrome(codeword))


def message_of(codeword):
    """Strip the parity prefix off a codeword."""
    if len(codeword) != spec.LDPC_N:
        raise ValueError(f"codeword must be {spec.LDPC_N} bits, got {len(codeword)}")
    return list(codeword[spec.LDPC_M :])
