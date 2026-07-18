#!/usr/bin/env python3
"""Unit tests for the pure RX/reporting pipeline: station.conf parsing,
decode log storage, session reporting, and the GFSK synthesis math.

Everything here is file I/O over tmpdirs/fixtures or pure numpy math — no
subprocess ever touches rigctl, paplay, or curl, and no real hardware or
network is involved. The one subprocess call (TestParseDecodes) runs
bin/parse_decodes.py itself against fixture jt9-style text, isolated to a
tmpdir cwd — parse_decodes.py has no importable functions (everything runs
at module level against stdin/argv), so this is the only way to exercise it
without refactoring production code. Run: python3 tools/test_pipeline.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import station_config
import decode_store
import report
import ft8synth


class TestStationConfig(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(station_config.load("/nonexistent/path.conf"), {})

    def test_parses_keys_strips_comments_and_quotes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write('MYCALL=N0CALL   # inline comment\n'
                    'MYGRID="AA00"\n'
                    '\n# full line comment\n'
                    'TX_PWR=5\n')
            path = f.name
        try:
            cfg = station_config.load(path)
        finally:
            os.unlink(path)
        self.assertEqual(cfg, {"MYCALL": "N0CALL", "MYGRID": "AA00", "TX_PWR": "5"})

    def test_save_keys_updates_in_place_preserving_comments(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write("TX_PWR=5   # watts\nBAND=20m\n")
            path = f.name
        try:
            station_config.save_keys({"TX_PWR": 10}, path)
            cfg = station_config.load(path)
            with open(path) as f:
                text = f.read()
        finally:
            os.unlink(path)
        self.assertEqual(cfg["TX_PWR"], "10")
        self.assertIn("# watts", text)
        self.assertEqual(cfg["BAND"], "20m")

    def test_save_keys_appends_new_key(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write("BAND=20m\n")
            path = f.name
        try:
            station_config.save_keys({"TX_PWR": 10}, path)
            cfg = station_config.load(path)
        finally:
            os.unlink(path)
        self.assertEqual(cfg["TX_PWR"], "10")
        self.assertEqual(cfg["BAND"], "20m")


class TestDecodeStore(unittest.TestCase):
    def test_timestamp_format(self):
        self.assertEqual(decode_store.timestamp("260711", "143015"),
                          "2026-07-11T14:30:15Z")

    def test_hour_file_path(self):
        p = decode_store.hour_file("/data", "260711", "143015")
        self.assertEqual(p, "/data/decodes/2026-07-11/14.jsonl")

    def test_append_and_read_all_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            decode_store.append(d, "260711", "143015", [{"msg": "CQ K1ABC FN42"}])
            decode_store.append(d, "260711", "150000", [{"msg": "CQ W9XYZ EN53"}])
            lines = decode_store.read_all(d)
        msgs = [json.loads(l)["msg"] for l in lines]
        self.assertEqual(msgs, ["CQ K1ABC FN42", "CQ W9XYZ EN53"])

    def test_read_all_since_date_filters_earlier_days(self):
        with tempfile.TemporaryDirectory() as d:
            decode_store.append(d, "260710", "120000", [{"msg": "yesterday"}])
            decode_store.append(d, "260711", "120000", [{"msg": "today"}])
            lines = decode_store.read_all(d, since_date_yymmdd="260711")
        msgs = [json.loads(l)["msg"] for l in lines]
        self.assertEqual(msgs, ["today"])

    def test_tail_uses_todays_files_when_present(self):
        import time
        today = time.strftime("%y%m%d", time.gmtime())
        with tempfile.TemporaryDirectory() as d:
            decode_store.append(d, today, "100000", [{"msg": "m0"}])
            decode_store.append(d, today, "110000", [{"msg": "m1"}])
            decode_store.append(d, today, "120000", [{"msg": "m2"}])
            lines = decode_store.tail(d, 2)
        msgs = [json.loads(l)["msg"] for l in lines]
        self.assertEqual(msgs, ["m1", "m2"])

    def test_tail_falls_back_to_latest_files_when_nothing_today(self):
        with tempfile.TemporaryDirectory() as d:
            decode_store.append(d, "200101", "100000", [{"msg": "old0"}])
            decode_store.append(d, "200101", "110000", [{"msg": "old1"}])
            lines = decode_store.tail(d, 2)
        msgs = [json.loads(l)["msg"] for l in lines]
        self.assertEqual(msgs, ["old0", "old1"])


class TestReport(unittest.TestCase):
    def test_read_jsonl_skips_malformed_lines_and_blanks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write('{"a": 1}\n\nnot json\n{"a": 2}\n')
            path = f.name
        try:
            recs = report.read_jsonl(path)
        finally:
            os.unlink(path)
        self.assertEqual(recs, [{"a": 1}, {"a": 2}])

    def test_read_jsonl_missing_file_returns_empty(self):
        self.assertEqual(report.read_jsonl("/nonexistent/file.jsonl"), [])

    def test_parse_adif_extracts_records(self):
        adif = ("<call:5>K1ABC<band:3>20m<qso_date:8>20260711<eor>"
                "<call:5>W9XYZ<band:3>40m<qso_date:8>20260710<eor>")
        with tempfile.NamedTemporaryFile("w", suffix=".adi", delete=False) as f:
            f.write(adif)
            path = f.name
        try:
            recs = report.parse_adif(path)
        finally:
            os.unlink(path)
        self.assertEqual(recs, [
            {"call": "K1ABC", "band": "20m", "qso_date": "20260711"},
            {"call": "W9XYZ", "band": "40m", "qso_date": "20260710"},
        ])

    def test_parse_adif_missing_file_returns_empty(self):
        self.assertEqual(report.parse_adif("/nonexistent/log.adi"), [])


class TestFT8Synth(unittest.TestCase):
    """gfsk_pulse/synth are pure numpy math (no subprocess) — the one
    external-binary call in this module (symbols_from_ft8code, which shells
    out to WSJT-X's ft8code) isn't exercised here since a bare CI runner
    doesn't have WSJT-X installed; README's on-air etiquette/loopback claims
    are verified manually against jt9, not by this suite."""

    def test_gfsk_pulse_shape(self):
        sps = 1920
        pulse = ft8synth.gfsk_pulse(ft8synth.SYM_BT, sps)
        self.assertEqual(len(pulse), 3 * sps)
        self.assertFalse(np.isnan(pulse).any())
        self.assertAlmostEqual(pulse[0], -pulse[-1], places=6)

    def test_synth_produces_bounded_finite_waveform(self):
        symbols = ([3, 1, 4, 0, 6, 5, 2] * 12)[:79]   # Costas-like, values 0-7
        sig = ft8synth.synth(symbols, 1500.0)
        sps = int(ft8synth.RATE * ft8synth.SYM_T)
        self.assertEqual(len(sig), ft8synth.NSYM * sps)
        self.assertFalse(np.isnan(sig).any())
        self.assertTrue(np.all(np.abs(sig) <= 1.0 + 1e-9))

    def test_synth_is_sensitive_to_symbols(self):
        sig_a = ft8synth.synth([0] * 79, 1500.0)
        sig_b = ft8synth.synth([7] * 79, 1500.0)
        self.assertFalse(np.allclose(sig_a, sig_b))


class TestParseDecodes(unittest.TestCase):
    """Exercises bin/parse_decodes.py itself via subprocess against fixture
    jt9-style stdout, isolated to a tmpdir cwd. Doesn't assert on next-call
    ranking, which depends on the real station's local station.conf/ADIF
    (MYCALL, worked-call history) and would make results depend on
    machine-local QSO history rather than this script's own logic."""

    SCRIPT = os.path.join(ROOT, "bin", "parse_decodes.py")

    def run_parser(self, stdin_text, date="260711", slot="143000"):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(
                [sys.executable, self.SCRIPT, date, slot],
                input=stdin_text, capture_output=True, text=True, cwd=d, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(decode_store.hour_file(d, date, slot)) as f:
                decoded = [json.loads(l) for l in f]
            with open(os.path.join(d, "status.json")) as f:
                status = json.load(f)
        return decoded, status

    def test_parses_jt9_output_and_writes_status(self):
        jt9_stdout = (
            "000000  -12  0.7  860 ~  CQ N5CH EM05\n"
            "000000   -5  0.3  797 ~  CQ VE6SLP D033\n"
            "not a decode line\n"
        )
        decoded, status = self.run_parser(jt9_stdout)
        self.assertEqual(len(decoded), 2)
        self.assertEqual(decoded[0]["msg"], "CQ N5CH EM05")
        self.assertEqual(decoded[0]["snr"], -12)
        self.assertEqual(decoded[1]["msg"], "CQ VE6SLP D033")
        self.assertEqual(decoded[1]["freq"], 797)
        self.assertEqual(status["slot_decodes"], 2)
        self.assertEqual(status["calling_me"], [])

    def test_malformed_lines_are_skipped(self):
        decoded, status = self.run_parser("garbage\n\nalso garbage\n", slot="150000")
        self.assertEqual(decoded, [])
        self.assertEqual(status["slot_decodes"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
