#!/usr/bin/env python3
"""Unit tests for bin/band_plan.py -- the strict FCC Part 97 compliance checker
(callsign format, band/frequency privileges, power caps) that gates seeq
start/chase and seeq doctor. Pure/stdlib-only, no network, no radio.
Run: python3 tools/test_band_plan.py
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import band_plan


class TestCheckCallsign(unittest.TestCase):
    def test_valid_well_known_callsign(self):
        ok, detail = band_plan.check_callsign("W1AW")
        self.assertTrue(ok, detail)

    def test_valid_2x3_format(self):
        ok, _ = band_plan.check_callsign("AB1CDE")
        self.assertTrue(ok)

    def test_valid_1x3_format(self):
        ok, _ = band_plan.check_callsign("K5ABC")
        self.assertTrue(ok)

    def test_lowercase_is_accepted(self):
        ok, _ = band_plan.check_callsign("k5abc")
        self.assertTrue(ok)

    def test_placeholder_rejected(self):
        ok, detail = band_plan.check_callsign("N0CALL")
        self.assertFalse(ok)
        self.assertIn("placeholder", detail.lower())

    def test_empty_rejected(self):
        ok, detail = band_plan.check_callsign("")
        self.assertFalse(ok)

    def test_none_rejected(self):
        ok, detail = band_plan.check_callsign(None)
        self.assertFalse(ok)

    def test_starts_with_digit_rejected(self):
        ok, _ = band_plan.check_callsign("5ABCDE")
        self.assertFalse(ok)

    def test_a_prefix_second_letter_out_of_range_rejected(self):
        # US 'A' block is AAA-ALZ only -- second letter must be A-L
        ok, detail = band_plan.check_callsign("AZ1ABC")
        self.assertFalse(ok, detail)

    def test_a_prefix_second_letter_in_range_accepted(self):
        ok, detail = band_plan.check_callsign("AL1ABC")
        self.assertTrue(ok, detail)

    def test_non_us_first_letter_rejected(self):
        ok, _ = band_plan.check_callsign("G0ABC")
        self.assertFalse(ok)


class TestCheckFrequency(unittest.TestCase):
    def test_inside_20m_data_privileges(self):
        ok, _ = band_plan.check_frequency("general", "20m", 14074000, "data")
        self.assertTrue(ok)

    def test_outside_20m_data_privileges(self):
        ok, _ = band_plan.check_frequency("general", "20m", 14200000, "data")
        self.assertFalse(ok)

    def test_inside_40m_data_privileges(self):
        ok, _ = band_plan.check_frequency("general", "40m", 7074000, "data")
        self.assertTrue(ok)

    def test_below_band_edge_rejected(self):
        ok, _ = band_plan.check_frequency("general", "30m", 10099000, "data")
        self.assertFalse(ok)

    def test_unsupported_license_class_hard_fails(self):
        ok, detail = band_plan.check_frequency("extra", "20m", 14074000, "data")
        self.assertFalse(ok)
        self.assertIn("not supported", detail.lower())

    def test_unknown_band_rejected(self):
        ok, _ = band_plan.check_frequency("general", "6m", 50313000, "data")
        self.assertFalse(ok)

    def test_60m_on_channel_accepted(self):
        ok, detail = band_plan.check_frequency("general", "60m", 5332000, "data")
        self.assertTrue(ok, detail)

    def test_60m_on_channel_within_half_width_accepted(self):
        ok, _ = band_plan.check_frequency("general", "60m", 5348000 + 1000, "data")
        self.assertTrue(ok)

    def test_60m_in_new_segment_accepted(self):
        ok, detail = band_plan.check_frequency("general", "60m", 5357000, "data")
        self.assertTrue(ok, detail)

    def test_60m_segment_edges_accepted(self):
        ok_lo, _ = band_plan.check_frequency("general", "60m", 5351500, "data")
        ok_hi, _ = band_plan.check_frequency("general", "60m", 5366500, "data")
        self.assertTrue(ok_lo)
        self.assertTrue(ok_hi)

    def test_60m_dead_zone_between_channel_and_segment_rejected(self):
        # 5340000 is not near any of the 4 channels and not in the 5351.5-5366.5 segment
        ok, detail = band_plan.check_frequency("general", "60m", 5340000, "data")
        self.assertFalse(ok, detail)


class TestCheckPower(unittest.TestCase):
    def test_30m_at_cap_ok(self):
        ok, _ = band_plan.check_power("30m", 10136000, 200)
        self.assertTrue(ok)

    def test_30m_over_cap_rejected(self):
        ok, detail = band_plan.check_power("30m", 10136000, 201)
        self.assertFalse(ok, detail)

    def test_60m_channel_at_cap_ok(self):
        ok, _ = band_plan.check_power("60m", 5332000, 100)
        self.assertTrue(ok)

    def test_60m_channel_over_cap_rejected(self):
        ok, detail = band_plan.check_power("60m", 5332000, 101)
        self.assertFalse(ok, detail)

    def test_60m_segment_at_cap_ok(self):
        ok, _ = band_plan.check_power("60m", 5357000, 9.15)
        self.assertTrue(ok)

    def test_60m_segment_over_cap_rejected(self):
        ok, detail = band_plan.check_power("60m", 5357000, 10)
        self.assertFalse(ok, detail)

    def test_general_ceiling_ok(self):
        ok, _ = band_plan.check_power("20m", 14074000, 1500)
        self.assertTrue(ok)

    def test_general_ceiling_exceeded_rejected(self):
        ok, detail = band_plan.check_power("20m", 14074000, 1501)
        self.assertFalse(ok, detail)

    def test_normal_band_low_power_ok(self):
        ok, _ = band_plan.check_power("20m", 14074000, 10)
        self.assertTrue(ok)

    def test_zero_power_rejected(self):
        ok, _ = band_plan.check_power("20m", 14074000, 0)
        self.assertFalse(ok)

    def test_non_numeric_power_rejected(self):
        ok, detail = band_plan.check_power("20m", 14074000, "lots")
        self.assertFalse(ok, detail)


class TestVerify(unittest.TestCase):
    def test_fully_compliant_config_passes(self):
        cfg = {"MYCALL": "AB1CDE", "LICENSE_CLASS": "general", "BAND": "20m",
               "DIAL_HZ": "14074000", "TX_PWR": "10"}
        ok, results = band_plan.verify(cfg)
        self.assertTrue(ok, results)
        self.assertEqual(len(results), 3)

    def test_bad_callsign_fails_aggregate(self):
        cfg = {"MYCALL": "N0CALL", "LICENSE_CLASS": "general", "BAND": "20m",
               "DIAL_HZ": "14074000", "TX_PWR": "10"}
        ok, results = band_plan.verify(cfg)
        self.assertFalse(ok)

    def test_out_of_band_freq_fails_aggregate(self):
        cfg = {"MYCALL": "AB1CDE", "LICENSE_CLASS": "general", "BAND": "20m",
               "DIAL_HZ": "14500000", "TX_PWR": "10"}
        ok, results = band_plan.verify(cfg)
        self.assertFalse(ok)

    def test_over_power_fails_aggregate(self):
        cfg = {"MYCALL": "AB1CDE", "LICENSE_CLASS": "general", "BAND": "30m",
               "DIAL_HZ": "10136000", "TX_PWR": "500"}
        ok, results = band_plan.verify(cfg)
        self.assertFalse(ok)

    def test_missing_dial_hz_fails_cleanly(self):
        cfg = {"MYCALL": "AB1CDE", "LICENSE_CLASS": "general", "BAND": "20m",
               "TX_PWR": "10"}
        ok, results = band_plan.verify(cfg)
        self.assertFalse(ok)

    def test_missing_tx_pwr_fails_cleanly(self):
        cfg = {"MYCALL": "AB1CDE", "LICENSE_CLASS": "general", "BAND": "20m",
               "DIAL_HZ": "14074000"}
        ok, results = band_plan.verify(cfg)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
