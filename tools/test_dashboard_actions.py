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

    def test_js8_rejected_while_in_development(self):
        """Hiding the button is not enough -- the endpoint itself must refuse.

        The chooser stops offering JS8 when its status is in-development, but
        /action/mode/switch is reachable directly (curl, a stale tab, a
        bookmarked POST). Since activating JS8 would launch the unverified
        wrapper, the server has to be the thing that says no.
        """
        mode, err = dashboard._validate_mode_switch({"mode": "js8"})
        self.assertIsNone(mode)
        self.assertIn("unknown mode", err)

    def test_unknown_mode_rejected(self):
        # "ft4" is MODE_INFO-only (status: planned) with no bin/modes/ft4/
        # package -- switching to it must be refused, not attempted.
        mode, err = dashboard._validate_mode_switch({"mode": "ft4"})
        self.assertIsNone(mode)
        self.assertIn("unknown mode", err)

    def test_missing_mode_rejected(self):
        mode, err = dashboard._validate_mode_switch({})
        self.assertIsNone(mode)
        self.assertEqual(err, "mode required")


class TestTuneWindow(unittest.TestCase):
    """The TUNE window must suppress Freq Lock's automatic retuning.

    TUNE stops the chaser and opens a 30 s window for a manual ATU tune cycle,
    during which the operator deliberately moves slightly off the calling
    frequency -- you don't key a tuning carrier on top of everyone else.

    Freq Lock's only skip condition used to be "qso.py is running", and TUNE
    kills qso.py. So for the entire tune window Freq Lock was live, polling
    every 5 s, and would drag the radio back onto the calling frequency while
    the operator was keying a tuning carrier. Automatic correction must never
    fight the operator's hand on the VFO.
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "tune-window.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_file_means_no_window(self):
        self.assertFalse(dashboard._tune_window_active(now=1000.0, path=self.path))

    def test_open_window_is_active(self):
        dashboard._begin_tune_window(30, now=1000.0, path=self.path)
        self.assertTrue(dashboard._tune_window_active(now=1005.0, path=self.path))

    def test_window_expires_on_its_own(self):
        # Nothing has to remember to close it -- a browser tab that vanishes
        # mid-tune must not leave freq lock suppressed forever.
        dashboard._begin_tune_window(30, now=1000.0, path=self.path)
        self.assertFalse(dashboard._tune_window_active(now=1031.0, path=self.path))

    def test_boundary_is_not_still_active(self):
        dashboard._begin_tune_window(30, now=1000.0, path=self.path)
        self.assertFalse(dashboard._tune_window_active(now=1030.0, path=self.path))

    def test_corrupt_file_does_not_suppress_forever(self):
        # Fail toward the normal behaviour, not toward permanently disabling
        # a safety feature.
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertFalse(dashboard._tune_window_active(now=1000.0, path=self.path))

    def test_duration_is_capped(self):
        # A stuck or malicious request must not disable freq lock for hours.
        dashboard._begin_tune_window(99999, now=1000.0, path=self.path)
        self.assertLessEqual(
            dashboard._read_tune_until(self.path) - 1000.0,
            dashboard.TUNE_WINDOW_MAX_S)

    def test_nonsense_duration_falls_back_to_the_default(self):
        for bad in (None, 0, -5, "abc"):
            dashboard._begin_tune_window(bad, now=1000.0, path=self.path)
            self.assertEqual(dashboard._read_tune_until(self.path),
                             1000.0 + dashboard.TUNE_WINDOW_DEFAULT_S, bad)


class TestValidateJs8Send(unittest.TestCase):
    """_validate_js8_send(): the dashboard-side half of the transmit gate.

    engine.send() enforces confirm independently; this is checked in both
    places on purpose, so neither one is the only thing between a stray POST
    and a keyed transmitter.
    """

    def test_confirmed_text_ok(self):
        text, err = dashboard._validate_js8_send({"text": "K1ABC HELLO", "confirm": True})
        self.assertEqual(text, "K1ABC HELLO")
        self.assertIsNone(err)

    def test_missing_confirm_rejected(self):
        text, err = dashboard._validate_js8_send({"text": "K1ABC HELLO"})
        self.assertIsNone(text)
        self.assertEqual(err, "confirm required")

    def test_falsey_confirm_rejected(self):
        for bad in (False, 0, "", None):
            text, err = dashboard._validate_js8_send({"text": "HI", "confirm": bad})
            self.assertIsNone(text, bad)

    def test_empty_text_rejected(self):
        for bad in ("", "   ", None):
            text, err = dashboard._validate_js8_send({"text": bad, "confirm": True})
            self.assertIsNone(text)
            self.assertEqual(err, "text required")

    def test_text_is_stripped(self):
        text, _ = dashboard._validate_js8_send({"text": "  HI  ", "confirm": True})
        self.assertEqual(text, "HI")

    def test_overlong_text_rejected(self):
        text, err = dashboard._validate_js8_send(
            {"text": "A" * (dashboard.JS8_MAX_TEXT + 1), "confirm": True})
        self.assertIsNone(text)
        self.assertIn("too long", err)


class TestJs8PanelMarkup(unittest.TestCase):
    """The JS8 panel has to reuse FT8's widget chrome rather than inventing
    its own, or the two modes stop looking like one application."""

    def test_js8_widgets_use_the_shared_widget_shell(self):
        for key in ("js8status", "js8actions", "js8conversation", "js8compose",
                    "js8activity", "js8inbox"):
            self.assertIn(f"data-key={key}", dashboard.PAGE, key)
            self.assertIn(f".widget[data-key={key}]", dashboard.PAGE, f"{key} has no size rule")

    def test_ft8_widgets_are_mode_tagged(self):
        for key in ("decodes", "ops", "txpanel", "actions", "waterfall", "events"):
            self.assertIn(f"data-mode=ft8 data-key={key}", dashboard.PAGE, key)

    def test_shared_chrome_is_not_mode_tagged(self):
        # These must stay visible in every mode: station status, config, the
        # logbook and QRZ sync all describe the station, not the mode.
        for key in ("status", "stationcfg", "qrz", "logbook", "map", "moon"):
            self.assertIn(f"<div class=widget data-key={key}>", dashboard.PAGE, key)

    def test_stop_button_lives_outside_the_mode_scoped_area(self):
        # applyModeVisibility only ever touches '#dash .widget[data-mode]'.
        # The unkey control sits in #cockpit, so no mode can hide it.
        cockpit = dashboard.PAGE.split("<div id=cockpit>")[1].split("<div id=dash>")[0]
        self.assertIn("btnUnkey", cockpit)

    def test_transmit_control_is_marked_tx_capable(self):
        # Same visual contract FT8's TX controls use.
        self.assertIn('id=btnJs8Send class="actionbtn warn tx-capable"', dashboard.PAGE)

    def test_panel_carries_the_watchdog_limitation_notice(self):
        # watchdog.py documents that a JS8 halt is a request to a process that
        # may itself be wedged. The operator should meet that fact on the
        # dashboard, not only in a source comment.
        self.assertIn("js8WatchdogNote", dashboard.PAGE)
        self.assertIn("owns the CAT port", dashboard.PAGE)


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


class TestResetStaleEngineState(unittest.TestCase):
    """reset_stale_engine_state(): the crash/power-loss counterpart to
    idle_engine_snapshot() above. That test class fixed the STOP-button
    case (qso.py confirmed not running via an explicit user action); this
    covers qso.py dying without any stop action at all -- killed, crashed,
    or the whole box losing power mid-QSO. On the next dashboard.py start,
    nothing has called _action_unkey() yet, so engine.json can still hold
    a tx:true/state:'qso' snapshot from before the outage even though the
    radio itself is idle -- /actions/state would report a phantom PTT-on
    to anyone glancing at the dashboard. Uses a real tmp file (path is
    injectable), same pattern as TestQrzLastSyncOk below."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".engine.json")
        self.path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _write(self, obj):
        with open(self.path, "w") as f:
            import json
            json.dump(obj, f)

    def test_qso_running_leaves_engine_json_untouched(self):
        stale = {"state": "qso", "tx": True, "target": "WS7M"}
        self._write(stale)
        changed = dashboard.reset_stale_engine_state(True, path=self.path)
        self.assertFalse(changed)
        with open(self.path) as f:
            import json
            self.assertEqual(json.load(f), stale)

    def test_qso_not_running_resets_to_idle(self):
        self._write({"state": "qso", "tx": True, "target": "WS7M"})
        changed = dashboard.reset_stale_engine_state(False, path=self.path)
        self.assertTrue(changed)
        with open(self.path) as f:
            import json
            on_disk = json.load(f)
        self.assertEqual(on_disk, dashboard.idle_engine_snapshot())

    def test_missing_file_is_created_idle(self):
        os.unlink(self.path)
        changed = dashboard.reset_stale_engine_state(False, path=self.path)
        self.assertTrue(changed)
        with open(self.path) as f:
            import json
            self.assertEqual(json.load(f), dashboard.idle_engine_snapshot())


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
