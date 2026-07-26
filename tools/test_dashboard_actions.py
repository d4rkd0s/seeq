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


class TestLoadDishFlower(unittest.TestCase):
    """_load_dish_flower(): same fail-open convention as every other
    embedded-data loader in this app (dxcc.py's _load_prefixes(),
    country_borders.py's _load_countries(), etc.) -- research-curated data
    (no free geo dataset has this), loaded once at import time into
    DISH_FLOWER_JSON."""

    def test_loaded_data_is_nonempty_dict(self):
        self.assertGreater(len(dashboard._load_dish_flower()), 100)

    def test_known_country_has_expected_shape(self):
        d = dashboard._load_dish_flower()
        self.assertIn("US", d)
        self.assertIn("dish", d["US"])


class TestValidateModeSwitch(unittest.TestCase):
    """_validate_mode_switch(): pure validation for /action/mode/switch's
    POST body, mirroring _build_chase_args's (value_or_none, err_or_none)
    shape. No subprocess-spawn testing here -- matches this file's
    established boundary (see module docstring)."""

    def test_known_mode_ok(self):
        mode, err = dashboard._validate_mode_switch({"mode": "ft8"})
        self.assertEqual(mode, "ft8")
        self.assertIsNone(err)

    def test_unknown_mode_rejected(self):
        mode, err = dashboard._validate_mode_switch({"mode": "js8"})
        self.assertIsNone(mode)
        self.assertIn("unknown mode", err)

    def test_missing_mode_rejected(self):
        mode, err = dashboard._validate_mode_switch({})
        self.assertIsNone(mode)
        self.assertEqual(err, "mode required")


class TestFlagCodeRegex(unittest.TestCase):
    """_FLAG_CODE_RE gates the /flags/<code>.svg endpoint before it ever
    touches the filesystem -- must accept only the exact [a-z]{2} shape
    flag-icons files are named with, and reject anything resembling a
    path-traversal attempt."""

    def test_accepts_valid_iso2(self):
        self.assertIsNotNone(dashboard._FLAG_CODE_RE.match("us"))
        self.assertIsNotNone(dashboard._FLAG_CODE_RE.match("fi"))

    def test_rejects_uppercase(self):
        self.assertIsNone(dashboard._FLAG_CODE_RE.match("US"))

    def test_rejects_path_traversal_attempts(self):
        for bad in ("../../etc/passwd", "..", "a/b", "a.b", "a..svg", ""):
            self.assertIsNone(dashboard._FLAG_CODE_RE.match(bad), bad)

    def test_rejects_wrong_length(self):
        self.assertIsNone(dashboard._FLAG_CODE_RE.match("u"))
        self.assertIsNone(dashboard._FLAG_CODE_RE.match("usa"))


class TestIdleEngineSnapshot(unittest.TestCase):
    """idle_engine_snapshot(): the safe 'nothing is happening' shape
    _action_unkey() writes to data/engine.json once qso.py is confirmed
    not running, so a killed/crashed chase run can't leave a stale
    tx:true/state:'calling' snapshot on disk forever (see the STOP-button-
    stuck-on-air bug this fixes -- also gated client-side by txIsLive() in
    PAGE's JS, but the file itself should reflect reality too)."""

    def test_tx_is_false(self):
        self.assertFalse(dashboard.idle_engine_snapshot()["tx"])

    def test_state_is_idle_not_qsos_own_init(self):
        # Distinct from qso.py's own startup default ("init", meaning
        # "never run yet") -- "idle" means "explicitly stopped after
        # running", a meaningful difference if anything ever inspects it.
        self.assertEqual(dashboard.idle_engine_snapshot()["state"], "idle")

    def test_never_looks_like_an_active_target(self):
        # txLineActive()/txIsLive() in PAGE's JS gate on state in
        # ('calling','qso') plus a target/tx -- the idle snapshot must
        # fail every one of those checks, not just the obvious tx field.
        snap = dashboard.idle_engine_snapshot()
        self.assertIsNone(snap["target"])
        self.assertNotIn(snap["state"], ("calling", "qso"))
        self.assertIsNone(snap["unkey_deadline_epoch"])

    def test_has_every_field_qso_py_writes(self):
        # Must round-trip through anything reading a real qso.py-written
        # engine.json without a KeyError -- same field set as qso.py's own
        # _engine dict (see bin/qso.py), just idle values.
        expected_fields = {"utc", "state", "target", "grid", "tx", "dx_mode", "msg",
                            "offset", "next_tx_epoch", "unkey_deadline_epoch", "tx_msg",
                            "tx_offset", "qso_step", "msg_tx_count", "snr_floor", "new_country"}
        self.assertEqual(set(dashboard.idle_engine_snapshot().keys()), expected_fields)


class TestQrzLastSyncOk(unittest.TestCase):
    """_qrz_last_sync_ok(): reads the exit-code file the /action/qrz/sync
    spawn wrapper writes (see _action_qrz_sync) once logsync.py finishes --
    drives the QRZ widget's red-border failure flag. Uses a real tmp file
    (path is injectable) rather than mocking open()."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".qrz-exit")
        self.path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _write(self, content):
        with open(self.path, "w") as f:
            f.write(content)

    def test_exit_zero_is_ok(self):
        self._write("0")
        ok, at = dashboard._qrz_last_sync_ok(self.path)
        self.assertTrue(ok)
        self.assertIsNotNone(at)

    def test_exit_nonzero_is_not_ok(self):
        self._write("1")
        ok, at = dashboard._qrz_last_sync_ok(self.path)
        self.assertFalse(ok)
        self.assertIsNotNone(at)

    def test_missing_file_is_unknown(self):
        os.unlink(self.path)
        ok, at = dashboard._qrz_last_sync_ok(self.path)
        self.assertIsNone(ok)
        self.assertIsNone(at)

    def test_malformed_content_is_unknown(self):
        self._write("not-a-number")
        ok, at = dashboard._qrz_last_sync_ok(self.path)
        self.assertIsNone(ok)
        self.assertIsNone(at)


class TestRetuneResultNote(unittest.TestCase):
    """_retune_result_note(): pure formatting of /action/station/set's
    response note, given whether the post-save CAT retune was confirmed via
    read-back -- extracted so it's testable without mocking subprocess."""

    def test_confirmed_retune_mentions_readback(self):
        note = dashboard._retune_result_note(14074000, True, None)
        self.assertIn("14.074 MHz", note)
        self.assertIn("confirmed via CAT read-back", note)

    def test_failed_retune_says_verify_manually(self):
        note = dashboard._retune_result_note(7074000, False, "Communication timed out")
        self.assertIn("did NOT confirm", note)
        self.assertIn("Communication timed out", note)
        self.assertIn("verify/retune manually", note)

    def test_failed_retune_without_detail_still_readable(self):
        note = dashboard._retune_result_note(7074000, False, None)
        self.assertIn("did NOT confirm", note)
        self.assertNotIn("None", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
