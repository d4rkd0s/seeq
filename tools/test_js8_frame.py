#!/usr/bin/env python3
"""Tests for SeeQ's native JS8 encoder core (P1).

Covers bin/modes/js8/{spec,crc12,ldpc,frame}.py -- the path from 12 payload
characters to 79 channel symbols. Pure arithmetic: no radio, no audio, no
JS8Call, no network. Everything here runs while the FT8 chaser is on the air.

The strongest offline check in this file is
test_generator_output_satisfies_every_parity_check: JS8.cpp carries the dense
generator G *and* the sparse parity-check tables Mn/Nm as two independent
literals. A codeword built from G that satisfies all 87 checks derived from Nm
proves both transcriptions correct without any reference implementation. That
is a genuine specification test, not a self-consistency tautology.

What this file CANNOT prove is that our reading of the spec matches JS8Call's
actual output. That gate is P1b (tools/js8_reference.py -> TX.FRAME oracle).

Run: python3 tools/test_js8_frame.py
"""
import importlib.util
import os
import random
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS8_DIR = os.path.join(ROOT, "bin", "modes", "js8")


def _load(name):
    path = os.path.join(JS8_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location("js8_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


spec_mod = _load("spec")
crc12 = _load("crc12")
ldpc = _load("ldpc")
frame = _load("frame")


class TestSpecConstants(unittest.TestCase):
    """Frame geometry and tables, per docs/js8/PROTOCOL.md sections 1-4."""

    def test_frame_geometry(self):
        self.assertEqual(spec_mod.NN, 79)
        self.assertEqual(spec_mod.ND, 58)
        self.assertEqual(spec_mod.NS, 21)
        self.assertEqual(spec_mod.ND + spec_mod.NS, spec_mod.NN)

    def test_costas_original_is_the_ft8_array(self):
        # PROTOCOL.md section 1: "JS8 originally used the same Costas arrays as FT8"
        for block in spec_mod.COSTAS_ORIGINAL:
            self.assertEqual(list(block), [4, 2, 5, 6, 1, 3, 0])
        self.assertEqual(len(spec_mod.COSTAS_ORIGINAL), 3)

    def test_costas_modified_blocks_all_differ(self):
        blocks = [list(b) for b in spec_mod.COSTAS_MODIFIED]
        self.assertEqual(blocks[0], [0, 6, 2, 3, 5, 4, 1])
        self.assertEqual(blocks[1], [1, 5, 0, 2, 3, 6, 4])
        self.assertEqual(blocks[2], [2, 5, 0, 6, 4, 1, 3])

    def test_every_costas_block_is_a_permutation_of_0_to_6(self):
        for name in ("COSTAS_ORIGINAL", "COSTAS_MODIFIED"):
            for block in getattr(spec_mod, name):
                self.assertEqual(sorted(block), list(range(7)), name)

    def test_payload_alphabet(self):
        self.assertEqual(len(spec_mod.ALPHABET), 64)
        self.assertEqual(spec_mod.ALPHABET[0], "0")
        self.assertEqual(spec_mod.ALPHABET[10], "A")
        self.assertEqual(spec_mod.ALPHABET[36], "a")
        self.assertEqual(spec_mod.ALPHABET[62], "-")
        self.assertEqual(spec_mod.ALPHABET[63], "+")
        self.assertEqual(len(set(spec_mod.ALPHABET)), 64, "alphabet has duplicates")

    def test_submode_normal_uses_the_original_costas_set(self):
        a = spec_mod.SUBMODES[spec_mod.SUBMODE_NORMAL]
        self.assertEqual(a.costas, spec_mod.COSTAS_ORIGINAL)
        self.assertEqual(a.symbol_samples, 1920)
        self.assertAlmostEqual(a.baud, 6.25, places=6)
        self.assertAlmostEqual(a.tone_spacing, 6.25, places=6)
        self.assertEqual(a.period_s, 15)

    def test_every_other_submode_uses_modified_costas(self):
        for sid, sm in spec_mod.SUBMODES.items():
            if sid == spec_mod.SUBMODE_NORMAL:
                continue
            self.assertEqual(sm.costas, spec_mod.COSTAS_MODIFIED, sm.name)

    def test_submode_ids_are_the_documented_bitset(self):
        # PROTOCOL.md section 2: ids 0/1/2/4/8 -- there is deliberately no 3
        self.assertEqual(sorted(spec_mod.SUBMODES), [0, 1, 2, 4, 8])

    def test_submode_derived_timings_match_the_spec_table(self):
        expect = {  # id: (baud, bandwidth, data_duration, period)
            0: (6.25, 50.0, 12.64, 15),
            1: (10.0, 80.0, 7.90, 10),
            2: (20.0, 160.0, 3.95, 6),
            4: (3.125, 25.0, 25.28, 30),
            8: (31.25, 250.0, 2.528, 4),
        }
        for sid, (baud, bw, dur, period) in expect.items():
            sm = spec_mod.SUBMODES[sid]
            self.assertAlmostEqual(sm.baud, baud, places=6, msg=sm.name)
            self.assertAlmostEqual(sm.bandwidth, bw, places=6, msg=sm.name)
            self.assertAlmostEqual(sm.data_duration, dur, places=6, msg=sm.name)
            self.assertEqual(sm.period_s, period, sm.name)

    def test_generator_matrix_shape(self):
        self.assertEqual(len(spec_mod.G_ROWS_HEX), 87)
        for row in spec_mod.G_ROWS_HEX:
            self.assertEqual(len(row), 22, row)
            int(row, 16)  # raises if a transcription typo crept in

    def test_generator_bits_are_87_wide_and_the_88th_is_discarded(self):
        rows = spec_mod.generator_rows()
        self.assertEqual(len(rows), 87)
        for r in rows:
            self.assertEqual(len(r), 87)
            self.assertTrue(all(b in (0, 1) for b in r))

    def test_sparse_tables_shape(self):
        self.assertEqual(len(spec_mod.MN), 174)
        for checks in spec_mod.MN:
            self.assertEqual(len(checks), 3)
        self.assertEqual(len(spec_mod.NM), 87)
        for nb in spec_mod.NM:
            self.assertIn(len(nb), (5, 6, 7))

    def test_mn_and_nm_describe_the_same_graph(self):
        """Mn[i] lists the checks bit i is in; Nm[c] lists the bits in check c."""
        from_nm = {i: set() for i in range(174)}
        for c, nb in enumerate(spec_mod.NM):
            for bit in nb:
                from_nm[bit].add(c)
        for i, checks in enumerate(spec_mod.MN):
            self.assertEqual(set(checks), from_nm[i], f"bit {i} disagrees")


class TestCRC12(unittest.TestCase):
    """PROTOCOL.md section 3: augmented CRC-12, poly 0xC06, final XOR 42."""

    def test_all_zero_block_returns_the_final_xor_constant(self):
        # remainder of an all-zero message is 0, so the result is purely the XOR
        self.assertEqual(crc12.crc12(bytes(11)), 42)

    def test_output_always_fits_in_12_bits(self):
        rng = random.Random(20260729)
        for _ in range(500):
            data = bytes(rng.randrange(256) for _ in range(11))
            self.assertLess(crc12.crc12(data), 1 << 12)

    def test_rejects_wrong_length_input(self):
        with self.assertRaises(ValueError):
            crc12.crc12(bytes(10))
        with self.assertRaises(ValueError):
            crc12.crc12(bytes(12))

    def test_a_bitwise_and_a_table_driven_implementation_agree(self):
        """Two independent implementations catch transcription slips."""
        rng = random.Random(4242)
        for _ in range(300):
            data = bytes(rng.randrange(256) for _ in range(11))
            self.assertEqual(crc12.crc12(data), crc12.crc12_table_driven(data))

    def test_single_bit_flips_always_change_the_crc(self):
        rng = random.Random(7)
        base = bytearray(rng.randrange(256) for _ in range(9)) + bytearray(2)
        ref = crc12.crc12(bytes(base))
        for byte_i in range(9):
            for bit in range(8):
                mutated = bytearray(base)
                mutated[byte_i] ^= 1 << bit
                self.assertNotEqual(crc12.crc12(bytes(mutated)), ref)

    def test_insert_then_extract_round_trips(self):
        rng = random.Random(99)
        for _ in range(200):
            b = bytearray(rng.randrange(256) for _ in range(9))
            b += bytearray(2)
            b[9] = (rng.randrange(8) & 7) << 5  # type bits, CRC field zero
            stamped = crc12.insert(bytes(b))
            self.assertEqual(len(stamped), 11)
            self.assertEqual(crc12.extract(stamped), crc12.crc12(bytes(b)))

    def test_stamped_block_verifies_and_a_corrupted_one_does_not(self):
        rng = random.Random(1234)
        b = bytearray(rng.randrange(256) for _ in range(9)) + bytearray(2)
        stamped = crc12.insert(bytes(b))
        self.assertTrue(crc12.verify(stamped))
        # Every bit except the one padding bit (byte 10 bit 0) is protected.
        for byte_i in range(11):
            for bit in range(8):
                if (byte_i, bit) == (10, 0):
                    continue  # information bit 87 -- unused, see below
                broken = bytearray(stamped)
                broken[byte_i] ^= 1 << bit
                self.assertFalse(crc12.verify(bytes(broken)), f"byte {byte_i} bit {bit}")

    def test_the_padding_bit_is_deliberately_not_covered(self):
        """Information bit 87 is unused padding; the CRC spans bits 0..86 only.

        Documented so a future reader does not "fix" this into a real failure.
        """
        b = bytearray(random.Random(5).randrange(256) for _ in range(9)) + bytearray(2)
        stamped = bytearray(crc12.insert(bytes(b)))
        self.assertEqual(stamped[10] & 0x01, 0, "padding bit should encode as 0")
        stamped[10] ^= 0x01
        self.assertTrue(crc12.verify(bytes(stamped)))

    def test_insert_preserves_the_type_bits(self):
        for i3 in range(8):
            b = bytearray(11)
            b[9] = i3 << 5
            stamped = crc12.insert(bytes(b))
            self.assertEqual((stamped[9] >> 5) & 7, i3)


class TestLDPC(unittest.TestCase):
    """PROTOCOL.md section 4: LDPC(174,87), codeword = [parity | message]."""

    def _random_message(self, rng):
        return [rng.randrange(2) for _ in range(87)]

    def test_parity_length(self):
        rng = random.Random(1)
        self.assertEqual(len(ldpc.parity(self._random_message(rng))), 87)

    def test_codeword_is_parity_then_message(self):
        rng = random.Random(2)
        m = self._random_message(rng)
        cw = ldpc.encode(m)
        self.assertEqual(len(cw), 174)
        self.assertEqual(cw[87:], m, "message must occupy bits 87..173")
        self.assertEqual(cw[:87], ldpc.parity(m), "parity must occupy bits 0..86")

    def test_generator_output_satisfies_every_parity_check(self):
        """G and Nm are independent literals in JS8.cpp -- this proves both.

        If either table were mis-transcribed, syndromes would be non-zero.
        """
        rng = random.Random(20260729)
        for trial in range(200):
            cw = ldpc.encode(self._random_message(rng))
            for c, neighbors in enumerate(spec_mod.NM):
                syndrome = 0
                for bit in neighbors:
                    syndrome ^= cw[bit]
                self.assertEqual(syndrome, 0, f"trial {trial}, check {c}")

    def test_syndrome_helper_agrees_with_the_manual_computation(self):
        rng = random.Random(3)
        cw = ldpc.encode(self._random_message(rng))
        self.assertEqual(ldpc.syndrome(cw), [0] * 87)

    def test_a_flipped_codeword_bit_breaks_exactly_three_checks(self):
        """Regular degree-3 on the variable side: each bit is in 3 checks."""
        rng = random.Random(4)
        cw = ldpc.encode(self._random_message(rng))
        for bit in range(0, 174, 7):  # sample, not all 174, to stay quick
            broken = list(cw)
            broken[bit] ^= 1
            self.assertEqual(sum(ldpc.syndrome(broken)), 3, f"bit {bit}")

    def test_encode_is_linear(self):
        """LDPC is a linear code: enc(a XOR b) == enc(a) XOR enc(b)."""
        rng = random.Random(5)
        a = self._random_message(rng)
        b = self._random_message(rng)
        ab = [x ^ y for x, y in zip(a, b)]
        lhs = ldpc.encode(ab)
        rhs = [x ^ y for x, y in zip(ldpc.encode(a), ldpc.encode(b))]
        self.assertEqual(lhs, rhs)

    def test_all_zero_message_encodes_to_all_zero_codeword(self):
        self.assertEqual(ldpc.encode([0] * 87), [0] * 174)

    def test_rejects_wrong_length_message(self):
        with self.assertRaises(ValueError):
            ldpc.encode([0] * 86)
        with self.assertRaises(ValueError):
            ldpc.encode([0] * 88)

    def test_rejects_non_binary_message(self):
        with self.assertRaises(ValueError):
            ldpc.encode([2] * 87)


class TestFrameEncode(unittest.TestCase):
    """PROTOCOL.md section 7: the JS8::encode algorithm."""

    MSG = "HELLOWORLD12"  # exactly 12 chars from the alphabet

    def test_returns_79_tones_in_range(self):
        tones = frame.encode(0, self.MSG)
        self.assertEqual(len(tones), 79)
        for t in tones:
            self.assertIn(t, range(8))

    def test_costas_blocks_land_at_0_36_and_72(self):
        tones = frame.encode(0, self.MSG)
        costas = spec_mod.COSTAS_ORIGINAL
        self.assertEqual(tones[0:7], list(costas[0]))
        self.assertEqual(tones[36:43], list(costas[1]))
        self.assertEqual(tones[72:79], list(costas[2]))

    def test_submode_selects_the_costas_set(self):
        fast = frame.encode(0, self.MSG, submode=spec_mod.SUBMODE_FAST)
        self.assertEqual(fast[0:7], list(spec_mod.COSTAS_MODIFIED[0]))
        self.assertEqual(fast[36:43], list(spec_mod.COSTAS_MODIFIED[1]))
        self.assertEqual(fast[72:79], list(spec_mod.COSTAS_MODIFIED[2]))

    def test_parity_precedes_message_in_the_symbol_stream(self):
        """PROTOCOL.md section 1: 'Parity is transmitted before the message.'"""
        tones = frame.encode(0, self.MSG)
        bits = frame.message_bits(0, self.MSG)
        cw = ldpc.encode(bits)
        for s in range(29):
            expect_parity = (cw[3 * s] << 2) | (cw[3 * s + 1] << 1) | cw[3 * s + 2]
            self.assertEqual(tones[7 + s], expect_parity, f"parity sym {s}")
            m = cw[87:]
            expect_msg = (m[3 * s] << 2) | (m[3 * s + 1] << 1) | m[3 * s + 2]
            self.assertEqual(tones[43 + s], expect_msg, f"message sym {s}")

    def test_message_bits_layout(self):
        bits = frame.message_bits(5, self.MSG)
        self.assertEqual(len(bits), 87)
        # bits 72..74 are the i3bit transmission flags
        self.assertEqual((bits[72] << 2) | (bits[73] << 1) | bits[74], 5)

    def test_every_i3bit_value_round_trips(self):
        for i3 in range(8):
            tones = frame.encode(i3, self.MSG)
            got = frame.decode_tones(tones)
            self.assertIsNotNone(got, f"i3bit {i3} failed to decode")
            self.assertEqual(got.i3bit, i3)
            self.assertEqual(got.message, self.MSG)

    def test_decode_tones_recovers_arbitrary_payloads(self):
        rng = random.Random(2026)
        for _ in range(200):
            msg = "".join(rng.choice(spec_mod.ALPHABET) for _ in range(12))
            i3 = rng.randrange(8)
            got = frame.decode_tones(frame.encode(i3, msg))
            self.assertIsNotNone(got, msg)
            self.assertEqual(got.message, msg)
            self.assertEqual(got.i3bit, i3)

    def test_decode_rejects_a_corrupted_frame(self):
        """A damaged frame must fail CRC rather than return garbage text."""
        tones = frame.encode(0, self.MSG)
        broken = list(tones)
        broken[50] = (broken[50] + 1) % 8  # corrupt a message symbol
        self.assertIsNone(frame.decode_tones(broken))

    def test_decode_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            frame.decode_tones([0] * 78)

    def test_encode_rejects_bad_message_length(self):
        with self.assertRaises(ValueError):
            frame.encode(0, "SHORT")
        with self.assertRaises(ValueError):
            frame.encode(0, "WAYTOOLONGMESSAGE")

    def test_encode_rejects_characters_outside_the_alphabet(self):
        with self.assertRaises(ValueError):
            frame.encode(0, "HELLO WORLD!")  # space and '!' are not in the alphabet

    def test_encode_rejects_out_of_range_i3bit(self):
        with self.assertRaises(ValueError):
            frame.encode(8, self.MSG)
        with self.assertRaises(ValueError):
            frame.encode(-1, self.MSG)

    def test_encoding_is_deterministic(self):
        self.assertEqual(frame.encode(3, self.MSG), frame.encode(3, self.MSG))

    def test_different_messages_give_different_tones(self):
        a = frame.encode(0, "AAAAAAAAAAAA")
        b = frame.encode(0, "AAAAAAAAAAAB")
        self.assertNotEqual(a, b)

    def test_data_symbols_carry_information_not_a_constant(self):
        """Guard against an encoder that silently emits all zeros."""
        tones = frame.encode(0, self.MSG)
        data = tones[7:36] + tones[43:72]
        self.assertGreater(len(set(data)), 1, "data symbols are constant")


if __name__ == "__main__":
    unittest.main(verbosity=2)
