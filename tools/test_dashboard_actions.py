#!/usr/bin/env python3
"""Tests for bin/dashboard.py's pure /action/chase/start validation
(_build_chase_args), extracted out of the H._action_chase_start HTTP handler
so it's unit-testable without spinning up an HTTP server or touching a
subprocess. No radio hardware, no network — pure logic on a plain dict.
Run: python3 tools/test_dashboard_actions.py
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD = os.path.join(ROOT, "bin", "dashboard.py")


def _dashboard_module():
    """Import bin/dashboard.py as a module. Safe: server startup is gated
    behind `if __name__=="__main__"`, so only module-level config loading
    runs. Same technique as tools/test_dashboard_js.py — don't invent a
    second one."""
    spec = importlib.util.spec_from_file_location("dashboard", DASHBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dashboard = _dashboard_module()


class TestBuildChaseArgs(unittest.TestCase):
    def test_dx_only_appends_flag(self):
        args, desc, err = dashboard._build_chase_args(
            {"confirm": True, "mode": "qsos", "n": 1, "dx_only": True})
        self.assertIsNone(err)
        self.assertIn("--dx-only", args)
        self.assertIn("DX Mode", desc)

    def test_dx_only_absent_key_defaults_false(self):
        args, desc, err = dashboard._build_chase_args(
            {"confirm": True, "mode": "qsos", "n": 1})
        self.assertIsNone(err)
        self.assertNotIn("--dx-only", args)

    def test_dx_only_false_explicit(self):
        args, desc, err = dashboard._build_chase_args(
            {"confirm": True, "mode": "qsos", "n": 1, "dx_only": False})
        self.assertIsNone(err)
        self.assertNotIn("--dx-only", args)

    def test_missing_confirm_still_errors(self):
        args, desc, err = dashboard._build_chase_args({"mode": "qsos", "n": 1})
        self.assertEqual(err, "confirm required")

    def test_bad_mode_still_errors(self):
        args, desc, err = dashboard._build_chase_args(
            {"confirm": True, "mode": "bogus", "n": 1})
        self.assertEqual(err, "mode must be 'qsos' or 'minutes'")

    def test_qsos_out_of_range_still_errors(self):
        args, desc, err = dashboard._build_chase_args(
            {"confirm": True, "mode": "qsos", "n": 21})
        self.assertEqual(err, "n out of range (1-20 QSOs)")

    def test_minutes_out_of_range_still_errors(self):
        args, desc, err = dashboard._build_chase_args(
            {"confirm": True, "mode": "minutes", "n": 181})
        self.assertEqual(err, "n out of range (1-180 minutes)")


class TestValidateSnrFloor(unittest.TestCase):
    """_validate_snr_floor(): pure validation for /action/snr_floor/set's
    POST body, mirroring _validate_max_watts's (ok, value_or_errmsg) shape."""

    def test_valid_value_ok(self):
        ok, val = dashboard._validate_snr_floor(-20)
        self.assertTrue(ok)
        self.assertEqual(val, -20)

    def test_string_number_is_coerced(self):
        ok, val = dashboard._validate_snr_floor("-18")
        self.assertTrue(ok)
        self.assertEqual(val, -18)

    def test_non_numeric_rejected(self):
        ok, err = dashboard._validate_snr_floor("bogus")
        self.assertFalse(ok)
        self.assertIn("numeric", err)

    def test_none_rejected(self):
        ok, err = dashboard._validate_snr_floor(None)
        self.assertFalse(ok)

    def test_out_of_range_rejected(self):
        ok, err = dashboard._validate_snr_floor(50)
        self.assertFalse(ok)
        self.assertIn("range", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
