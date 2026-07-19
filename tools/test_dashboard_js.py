#!/usr/bin/env python3
"""Tests for callCountry(), the browser-side callsign-prefix -> country
lookup embedded as JS text inside bin/dashboard.py (used for map/cockpit
display only). We extract the *actual* CALL_PREFIXES/callCountry source --
the same bytes served to the browser -- and run it under Node, rather than
reimplementing the prefix-matching logic in Python. A parallel Python port
could silently drift from the real JS and stop catching real bugs; this
doesn't. No radio hardware, no network -- pure local JS evaluation via
subprocess. Requires `node` on PATH (present on GitHub Actions ubuntu-latest
runners by default). Run: python3 tools/test_dashboard_js.py
"""
import importlib.util
import json
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD = os.path.join(ROOT, "bin", "dashboard.py")


def _dashboard_module():
    """Import bin/dashboard.py as a module to get at its fully-templated
    PAGE string (CALL_PREFIXES is templated in from bin/dxcc_prefixes.json
    at import time, not hardcoded in the source text). Safe to import:
    dashboard.py gates its server startup behind `if __name__=="__main__"`,
    so nothing but module-level config loading runs."""
    spec = importlib.util.spec_from_file_location("dashboard", DASHBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract_call_country_js():
    """Slice the CALL_PREFIXES array + callCountry() function verbatim out of
    dashboard.py's rendered PAGE (the same bytes served to the browser),
    between two stable markers: the array's declaration and the following
    US_STATE_BOXES table."""
    page = _dashboard_module().PAGE
    start = page.index("const CALL_PREFIXES=[")
    end = page.index("\nconst US_STATE_BOXES", start)
    snippet = page[start:end]
    assert "function callCountry(call){" in snippet, (
        "callCountry() not found between markers -- dashboard.py layout changed, "
        "update the markers in tools/test_dashboard_js.py")
    return snippet


def run_call_country(calls):
    """Evaluate the real callCountry() JS (via Node) for a list of callsigns.
    Returns {call: country}."""
    js = extract_call_country_js()
    calls_json = json.dumps(list(calls))
    script = js + (
        "\nconst __calls = %s;"
        "\nconst __out = {};"
        "\nfor (const c of __calls) __out[c] = callCountry(c);"
        "\nprocess.stdout.write(JSON.stringify(__out));"
    ) % calls_json
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_secs_to_next_slot_js():
    """Slice secsToNextSlot() verbatim out of dashboard.py's rendered PAGE,
    between its declaration and the updateNextTx() function that uses it."""
    page = _dashboard_module().PAGE
    start = page.index("function secsToNextSlot(")
    end = page.index("\nfunction updateNextTx(", start)
    snippet = page[start:end]
    assert "return" in snippet, (
        "secsToNextSlot() not found between markers -- dashboard.py layout "
        "changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_secs_to_next_slot(now_epoch_sec):
    js = extract_secs_to_next_slot_js()
    script = js + "\nprocess.stdout.write(JSON.stringify(secsToNextSlot(%r)));" % now_epoch_sec
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_pick_new_country_flash_js():
    """Slice the pickNewCountryFlash() edge-trigger/dedup function verbatim
    out of dashboard.py's rendered PAGE, between two stable markers: its
    declaration and the tick() function it feeds."""
    page = _dashboard_module().PAGE
    start = page.index("function pickNewCountryFlash(")
    end = page.index("\nasync function tick(){", start)
    snippet = page[start:end]
    assert "new_country" in snippet, (
        "pickNewCountryFlash() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_pick_new_country_flash(candidates, flashed_calls):
    """Evaluate the real pickNewCountryFlash() JS (via Node). Returns the
    picked candidate dict, or None."""
    js = extract_pick_new_country_flash_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(pickNewCountryFlash(%s, %s)));"
    ) % (json.dumps(candidates), json.dumps(flashed_calls))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


class TestCallCountry(unittest.TestCase):
    def test_regression_pre_existing_prefix(self):
        result = run_call_country(["DL1ABC"])
        self.assertEqual(result["DL1ABC"], "Germany")

    def test_caribbean_and_south_america_prefixes(self):
        result = run_call_country(["HI8ABC", "C6AXY", "YV5ABC", "CE3ABC", "9Y4ABC"])
        self.assertEqual(result["HI8ABC"], "Dominican Republic")
        self.assertEqual(result["C6AXY"], "Bahamas")
        self.assertEqual(result["YV5ABC"], "Venezuela")
        self.assertEqual(result["CE3ABC"], "Chile")
        self.assertEqual(result["9Y4ABC"], "Trinidad and Tobago")

    def test_europe_middle_east_asia_prefixes(self):
        result = run_call_country(["TF3ABC", "4X1ABC", "YB1ABC", "HL5ABC", "9V1ABC"])
        self.assertEqual(result["TF3ABC"], "Iceland")
        self.assertEqual(result["4X1ABC"], "Israel")
        self.assertEqual(result["YB1ABC"], "Indonesia")
        self.assertEqual(result["HL5ABC"], "South Korea")
        self.assertEqual(result["9V1ABC"], "Singapore")

    def test_longest_prefix_wins_kp4_vs_kp(self):
        result = run_call_country(["KP4ABC", "KP2ABC"])
        self.assertEqual(result["KP4ABC"], "Puerto Rico")
        self.assertEqual(result["KP2ABC"], "Caribbean (US)")

    def test_unknown_prefix_returns_empty_string(self):
        result = run_call_country(["QQ9ZZZ"])
        self.assertEqual(result["QQ9ZZZ"], "")


class TestPickNewCountryFlash(unittest.TestCase):
    DL = {"call": "DL2XYZ", "grid": "JN58", "snr": -10, "freq": 1200, "slot": "143000",
          "country": "Germany", "new_country": True}
    W = {"call": "W1ABC", "grid": "FN31", "snr": -5, "freq": 900, "slot": "143000",
         "country": "United States", "new_country": False}

    def test_picks_first_new_country_candidate(self):
        result = run_pick_new_country_flash([self.W, self.DL], [])
        self.assertEqual(result["call"], "DL2XYZ")

    def test_returns_none_when_no_new_country_candidates(self):
        self.assertIsNone(run_pick_new_country_flash([self.W], []))

    def test_skips_already_flashed_call(self):
        self.assertIsNone(run_pick_new_country_flash([self.DL], ["DL2XYZ"]))

    def test_finds_new_country_candidate_beyond_first(self):
        # a rare country buried at candidate #3 by SNR is still worth flashing
        other = {"call": "K5AAA", "grid": "EM10", "snr": -3, "freq": 800,
                  "slot": "143000", "country": "United States", "new_country": False}
        result = run_pick_new_country_flash([self.W, other, self.DL], [])
        self.assertEqual(result["call"], "DL2XYZ")

    def test_empty_candidates_returns_none(self):
        self.assertIsNone(run_pick_new_country_flash([], []))


class TestSecsToNextSlot(unittest.TestCase):
    def test_at_slot_boundary_returns_full_slot(self):
        self.assertEqual(run_secs_to_next_slot(0), 15)

    def test_mid_slot(self):
        self.assertAlmostEqual(run_secs_to_next_slot(14.5), 0.5, places=5)

    def test_exact_boundary_wraps_to_full_slot_not_zero(self):
        self.assertEqual(run_secs_to_next_slot(15), 15)

    def test_second_slot_mid_point(self):
        self.assertAlmostEqual(run_secs_to_next_slot(22.3), 7.7, places=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
