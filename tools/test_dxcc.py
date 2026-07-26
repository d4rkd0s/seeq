#!/usr/bin/env python3
"""Tests for bin/dxcc.py — the shared callsign-prefix -> country/DXCC-entity
lookup used by both qso.py's DX Mode filter and dashboard.py's cockpit/map
display (templated from the same bin/dxcc_prefixes.json). Pure logic, no
radio hardware, no network. Run: python3 tools/test_dxcc.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dxcc
from test_dashboard_js import run_call_country


class TestCountryForCall(unittest.TestCase):
    def test_known_prefix_returns_country(self):
        self.assertEqual(dxcc.country_for_call("DL1ABC"), "Germany")

    def test_unmapped_prefix_returns_empty(self):
        self.assertEqual(dxcc.country_for_call("QQ9ZZZ"), "")

    def test_longest_prefix_wins(self):
        self.assertEqual(dxcc.country_for_call("KP4ABC"), "Puerto Rico")
        self.assertEqual(dxcc.country_for_call("KP2ABC"), "Caribbean (US)")

    def test_falsy_call_returns_empty(self):
        self.assertEqual(dxcc.country_for_call(""), "")
        self.assertEqual(dxcc.country_for_call(None), "")


class TestIsDxCall(unittest.TestCase):
    def test_true_when_different_known_countries(self):
        self.assertTrue(dxcc.is_dx_call("DL1ABC", "W1AW"))

    def test_false_when_same_country(self):
        self.assertFalse(dxcc.is_dx_call("K5XYZ", "W1AW"))

    def test_false_when_candidate_country_unknown(self):
        self.assertFalse(dxcc.is_dx_call("QQ9ZZZ", "W1AW"))

    def test_false_when_home_country_unknown(self):
        self.assertFalse(dxcc.is_dx_call("DL1ABC", "QQ9ZZZ"))


class TestIsNeighborCall(unittest.TestCase):
    """is_neighbor_call(call, home_call): True when `call`'s DXCC country
    shares a land border with `home_call`'s (bin/country_adjacency.json) --
    used by qso.py's candidate_tier to rank genuine long-distance DX (EU,
    Africa, Asia...) above "easy" neighboring-country DX (Canada/Mexico for
    a US station), without ever outranking an unworked/new country (that
    stays candidate_tier's tier 0 regardless of neighbor status)."""

    def test_canada_is_neighbor_of_united_states(self):
        self.assertTrue(dxcc.is_neighbor_call("VE3ABC", "W1AW"))

    def test_mexico_is_neighbor_of_united_states(self):
        self.assertTrue(dxcc.is_neighbor_call("XE1ABC", "W1AW"))

    def test_germany_is_not_neighbor_of_united_states(self):
        self.assertFalse(dxcc.is_neighbor_call("DL1ABC", "W1AW"))

    def test_same_country_is_not_a_neighbor(self):
        self.assertFalse(dxcc.is_neighbor_call("K5XYZ", "W1AW"))

    def test_unmapped_call_fails_closed(self):
        self.assertFalse(dxcc.is_neighbor_call("QQ9ZZZ", "W1AW"))

    def test_unmapped_home_call_fails_closed(self):
        self.assertFalse(dxcc.is_neighbor_call("DL1ABC", "QQ9ZZZ"))

    def test_dxcc_entity_with_no_border_country_fails_closed(self):
        # Alaska is its own DXCC entity but not a distinct Natural Earth
        # political country -- can't resolve an ISO2, must never guess.
        self.assertFalse(dxcc.is_neighbor_call("KL7ABC", "W1AW"))


class TestLoggedCountries(unittest.TestCase):
    def test_resolves_known_calls_to_country_set(self):
        self.assertEqual(dxcc.logged_countries(["DL1ABC", "DL9XYZ", "W1AW"]),
                          {"Germany", "United States"})

    def test_drops_unmapped_calls(self):
        self.assertEqual(dxcc.logged_countries(["QQ9ZZZ"]), set())

    def test_empty_input_returns_empty_set(self):
        self.assertEqual(dxcc.logged_countries([]), set())


class TestIsNewCountry(unittest.TestCase):
    def test_false_when_country_already_logged(self):
        logged = dxcc.logged_countries(["DL1ABC"])
        self.assertFalse(dxcc.is_new_country("DL9XYZ", logged))  # same country, different call

    def test_true_when_country_not_logged(self):
        logged = dxcc.logged_countries(["W1AW"])
        self.assertTrue(dxcc.is_new_country("DL1ABC", logged))

    def test_false_when_call_unmapped(self):
        logged = dxcc.logged_countries(["W1AW"])
        self.assertFalse(dxcc.is_new_country("QQ9ZZZ", logged))  # fails closed

    def test_false_when_logged_empty_and_call_unmapped(self):
        self.assertFalse(dxcc.is_new_country("QQ9ZZZ", set()))


class TestDxSkipReason(unittest.TestCase):
    """dx_filter_ok()'s single 'not confirmed DX (same/unknown country)'
    skip message conflated two very different situations: a genuinely
    same-country station (correctly filtered, nothing to fix) vs. an
    unresolved prefix (a real gap in dxcc_prefixes.json worth closing).
    dx_skip_reason() classifies which one it was, for logging only --
    is_dx_call() remains the actual gate, untouched."""

    def test_same_country_when_both_resolve_and_match(self):
        self.assertEqual(dxcc.dx_skip_reason("K5XYZ", "W1AW"), "same")

    def test_unknown_when_candidate_country_unresolved(self):
        self.assertEqual(dxcc.dx_skip_reason("QQ9ZZZ", "W1AW"), "unknown")

    def test_unknown_when_home_country_unresolved(self):
        self.assertEqual(dxcc.dx_skip_reason("DL1ABC", "QQ9ZZZ"), "unknown")

    def test_unknown_when_both_unresolved(self):
        self.assertEqual(dxcc.dx_skip_reason("QQ9ZZZ", "ZZ9ZZZ"), "unknown")


class TestPythonJsParity(unittest.TestCase):
    """dxcc.py and dashboard.py's callCountry() JS must agree, since both are
    templated from the same bin/dxcc_prefixes.json -- reuses
    tools/test_dashboard_js.py's Node harness rather than a second technique."""

    def test_agrees_with_js_for_sample_calls(self):
        calls = ["DL1ABC", "KP4ABC", "KP2ABC", "QQ9ZZZ", "HI8ABC"]
        js_result = run_call_country(calls)
        for call in calls:
            self.assertEqual(dxcc.country_for_call(call), js_result[call], call)


if __name__ == "__main__":
    unittest.main(verbosity=2)
