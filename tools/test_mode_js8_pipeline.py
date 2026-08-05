#!/usr/bin/env python3
"""Tests for bin/modes/js8/pipeline.py -- JS8 mode's RX/process lifecycle.

Nothing here launches JS8Call-improved, opens the CAT port, or touches the
radio: every external effect (installing the binary, spawning a process,
probing the API) is injected. The AppImage-launching path is verified for
*sequencing* -- particularly that stop() tries to halt the transmitter before
it kills anything -- which is the part that matters for safety.
Run: python3 tools/test_mode_js8_pipeline.py
"""
import configparser
import importlib.util
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_PY = os.path.join(ROOT, "bin", "modes", "js8", "pipeline.py")


def _pipeline_module():
    spec = importlib.util.spec_from_file_location("js8_pipeline", PIPELINE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pipeline = _pipeline_module()


class FakeClient:
    """Stands in for api.Js8Client in stop()'s safety path."""

    def __init__(self, halt_raises=False, connect_raises=False):
        self.halt_raises = halt_raises
        self.connect_raises = connect_raises
        self.calls = []

    def connect(self):
        self.calls.append("connect")
        if self.connect_raises:
            raise pipeline.api.Js8ApiError("refused")
        return self

    def tx_halt(self):
        self.calls.append("tx_halt")
        if self.halt_raises:
            raise pipeline.api.Js8ApiError("no answer")
        return True

    def close(self):
        self.calls.append("close")


class TestConfigPath(unittest.TestCase):
    def test_ini_lives_beside_other_qt_configs_not_in_a_subdir(self):
        # MultiSettings.cpp builds the path as
        #   QStandardPaths::writableLocation(ConfigLocation) + applicationName() + ".ini"
        # ConfigLocation on Linux is ~/.config itself (AppConfigLocation would
        # have been the ~/.config/JS8Call/ form), so the file sits directly in
        # ~/.config -- worth pinning, since guessing the subdir form would mean
        # writing settings JS8Call never reads.
        path = pipeline.config_ini_path(config_home="/tmp/cfg")
        self.assertEqual(os.path.dirname(path), "/tmp/cfg")
        self.assertTrue(path.endswith(".ini"))

    def test_ini_is_namespaced_to_seeqs_own_instance(self):
        # --rig-name appends " - <name>" to applicationName, so SeeQ gets its
        # own settings file and never edits a hand-run JS8Call's config.
        path = pipeline.config_ini_path(config_home="/tmp/cfg")
        self.assertIn(pipeline.RIG_NAME, os.path.basename(path))
        self.assertNotEqual(os.path.basename(path), "JS8Call.ini")

    def test_respects_xdg_config_home(self):
        path = pipeline.config_ini_path(config_home="/somewhere/else")
        self.assertTrue(path.startswith("/somewhere/else"))


class TestBuildSettings(unittest.TestCase):
    def test_enables_the_tcp_api_on_the_right_port(self):
        s = pipeline.build_settings({}, tcp_port=2442)
        self.assertEqual(s["AcceptTCPRequests"], "true")
        self.assertEqual(s["TCPServerPort"], "2442")

    def test_does_not_enable_the_udp_server(self):
        # Nothing in SeeQ speaks UDP to JS8Call; leaving a second listener open
        # is surface area for no benefit.
        s = pipeline.build_settings({}, tcp_port=2442)
        self.assertEqual(s.get("AcceptUDPRequests", "false"), "false")

    def test_carries_station_identity_across(self):
        s = pipeline.build_settings({"MYCALL": "N0CALL", "MYGRID": "AA00aa"}, tcp_port=2442)
        self.assertEqual(s["MyCall"], "N0CALL")
        self.assertEqual(s["MyGrid"], "AA00aa")

    def test_never_writes_ptt_method_or_rig_model(self):
        # Deliberate scope limit. PTTMethod decides *how* the radio gets keyed
        # and Rig decides whether CAT is opened at all; both are enum/string
        # values that have not been verified against a live instance, and
        # guessing wrong is a TX-safety misconfiguration, not a cosmetic bug.
        # Rig + PTT stay a one-time GUI step done with the control operator.
        s = pipeline.build_settings({"RIG_MODEL": "3060", "CAT_PORT": "/dev/ttyUSB0"},
                                     tcp_port=2442)
        self.assertNotIn("PTTMethod", s)
        self.assertNotIn("Rig", s)


class TestWriteSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ini = os.path.join(self.tmp, "JS8Call - SeeQ.ini")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self):
        cp = configparser.RawConfigParser()
        cp.optionxform = str
        cp.read(self.ini)
        return cp

    def test_creates_the_file_with_a_general_section(self):
        # Configuration.cpp reads these with no beginGroup(), which in Qt's ini
        # format means the [General] section.
        pipeline.write_settings(self.ini, {"AcceptTCPRequests": "true"})
        cp = self._read()
        self.assertEqual(cp["General"]["AcceptTCPRequests"], "true")

    def test_preserves_unrelated_existing_settings(self):
        # the control operator may have configured audio, rig, macros -- rewriting the file
        # from scratch would silently destroy all of it.
        with open(self.ini, "w") as f:
            f.write("[General]\nSoundInName=DE-19\nMyCall=N0CALL\n\n[MultiSettings]\nx=1\n")
        pipeline.write_settings(self.ini, {"AcceptTCPRequests": "true"})
        cp = self._read()
        self.assertEqual(cp["General"]["SoundInName"], "DE-19")
        self.assertEqual(cp["General"]["MyCall"], "N0CALL")
        self.assertEqual(cp["MultiSettings"]["x"], "1")
        self.assertEqual(cp["General"]["AcceptTCPRequests"], "true")

    def test_preserves_key_case(self):
        # Qt keys are case-sensitive; configparser lowercases by default, which
        # would turn AcceptTCPRequests into a key JS8Call never looks up.
        pipeline.write_settings(self.ini, {"TCPServerPort": "2442"})
        with open(self.ini) as f:
            self.assertIn("TCPServerPort", f.read())

    def test_overwrites_a_stale_value(self):
        with open(self.ini, "w") as f:
            f.write("[General]\nAcceptTCPRequests=false\nTCPServerPort=9999\n")
        pipeline.write_settings(self.ini, {"AcceptTCPRequests": "true", "TCPServerPort": "2442"})
        cp = self._read()
        self.assertEqual(cp["General"]["AcceptTCPRequests"], "true")
        self.assertEqual(cp["General"]["TCPServerPort"], "2442")

    def test_tolerates_qt_ini_values_containing_percent_signs(self):
        # Qt escapes some values with % sequences; configparser's default
        # interpolation would raise on them.
        with open(self.ini, "w") as f:
            f.write("[General]\nSomeKey=%7B%7D\n")
        pipeline.write_settings(self.ini, {"AcceptTCPRequests": "true"})
        self.assertEqual(self._read()["General"]["SomeKey"], "%7B%7D")


class TestStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ini = os.path.join(self.tmp, "JS8Call - SeeQ.ini")
        self.spawned = []
        self.sleeps = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _start(self, running=False, reachable=True, **kw):
        reach = reachable if callable(reachable) else (lambda: reachable)
        return pipeline.start(
            ini_path=self.ini,
            ensure_fn=lambda: ("/fake/JS8Call.AppImage", "cache"),
            spawn_fn=lambda cmd, log: self.spawned.append(cmd),
            running_fn=lambda: running,
            reachable_fn=reach,
            sleep_fn=self.sleeps.append,
            clock_fn=iter(range(0, 2000, 5)).__next__,
            **kw)

    def test_launches_the_appimage_with_its_own_instance_name(self):
        r = self._start()
        self.assertTrue(r["started"])
        launch = self.spawned[0]
        self.assertEqual(launch[0], "/fake/JS8Call.AppImage")
        self.assertIn("--rig-name", launch)
        self.assertIn(pipeline.RIG_NAME, launch)

    def test_writes_the_api_settings_before_launching(self):
        self._start()
        cp = configparser.RawConfigParser()
        cp.optionxform = str
        cp.read(self.ini)
        self.assertEqual(cp["General"]["AcceptTCPRequests"], "true")

    def test_also_starts_the_decode_capture_process(self):
        # JS8's equivalent of rx-loop.sh: a separate long-lived process, since
        # a thread inside this one would die the moment start() returns.
        self._start()
        self.assertEqual(len(self.spawned), 2, self.spawned)
        self.assertTrue(any("rx_capture.py" in " ".join(c) for c in self.spawned))

    def test_no_ops_when_already_running(self):
        r = self._start(running=True)
        self.assertFalse(r["started"])
        self.assertTrue(r["already"])
        self.assertEqual(self.spawned, [])

    def test_dryrun_launches_nothing_and_writes_nothing(self):
        r = self._start(dryrun=True)
        self.assertTrue(r["dryrun"])
        self.assertEqual(self.spawned, [])
        self.assertFalse(os.path.exists(self.ini))

    def test_fails_cleanly_if_the_api_never_comes_up(self):
        # A GUI that launches but whose API never answers is a failed start,
        # not a successful one -- mode_switch must not mark JS8 active.
        r = self._start(reachable=False)
        self.assertFalse(r["started"])
        self.assertIn("api", r["error"].lower())

    def test_waits_for_the_api_rather_than_assuming_instant_readiness(self):
        # The GUI takes seconds to boot; the first probe will normally fail.
        state = {"n": 0}

        def reachable():
            state["n"] += 1
            return state["n"] >= 3
        r = self._start(reachable=reachable)
        self.assertTrue(r["started"])
        self.assertGreaterEqual(len(self.sleeps), 2)


class TestStopAndChecks(unittest.TestCase):
    def setUp(self):
        self.killed = []

    def _stop(self, client=None, **kw):
        return pipeline.stop(
            client_fn=lambda: client if client is not None else FakeClient(),
            pkill_fn=lambda pat: (self.killed.append(pat), True)[1],
            **kw)

    def test_halts_the_transmitter_before_killing_anything(self):
        # Same ordering rule as FT8's stop(), where `rigctl T 0` fires first and
        # unconditionally: never kill the only process that can unkey the radio
        # while it might still be keyed.
        c = FakeClient()
        self._stop(client=c)
        self.assertIn("tx_halt", c.calls)
        self.assertTrue(self.killed)

    def test_still_kills_when_the_api_is_unreachable(self):
        # If JS8Call is already wedged or gone, the halt will fail -- that must
        # not abort the shutdown, or a hung GUI would survive a stop request.
        c = FakeClient(connect_raises=True)
        r = self._stop(client=c)
        self.assertTrue(self.killed)
        self.assertTrue(r["stopped"])

    def test_still_kills_when_the_halt_itself_errors(self):
        c = FakeClient(halt_raises=True)
        self._stop(client=c)
        self.assertTrue(self.killed)

    def test_kills_the_capture_process_too(self):
        self._stop()
        self.assertTrue(any("rx_capture.py" in p for p in self.killed), self.killed)

    def test_dryrun_kills_nothing(self):
        r = pipeline.stop(dryrun=True,
                           client_fn=lambda: FakeClient(),
                           pkill_fn=lambda pat: self.killed.append(pat))
        self.assertTrue(r["dryrun"])
        self.assertEqual(self.killed, [])

    def test_sanity_check_fails_while_the_process_is_still_up(self):
        ok, detail = pipeline.sanity_check(running_fn=lambda: True,
                                            reachable_fn=lambda: False)
        self.assertFalse(ok)
        self.assertIn("still running", detail)

    def test_sanity_check_passes_once_everything_is_down(self):
        ok, detail = pipeline.sanity_check(running_fn=lambda: False,
                                            reachable_fn=lambda: False)
        self.assertTrue(ok, detail)

    def test_sanity_check_fails_if_the_api_still_answers(self):
        # A live API means something is still holding the audio/CAT devices,
        # even if the process pattern didn't match.
        ok, detail = pipeline.sanity_check(running_fn=lambda: False,
                                            reachable_fn=lambda: True)
        self.assertFalse(ok)
        self.assertIn("api", detail.lower())

    def test_preflight_fails_without_a_binary(self):
        ok, detail = pipeline.preflight(find_fn=lambda: None)
        self.assertFalse(ok)
        self.assertIn("JS8Call", detail)

    def test_preflight_passes_with_a_binary(self):
        ok, detail = pipeline.preflight(find_fn=lambda: "/fake/JS8Call.AppImage")
        self.assertTrue(ok, detail)


RX_CAPTURE_PY = os.path.join(ROOT, "bin", "modes", "js8", "rx_capture.py")


def _capture_module():
    spec = importlib.util.spec_from_file_location("js8_rx_capture", RX_CAPTURE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


capture = _capture_module()

DIRECTED_MSG = {
    "params": {"CMD": " MSG", "DIAL": 7078000, "FROM": "HC5PH", "GRID": " FI06",
               "OFFSET": 2420, "SNR": -24, "SPEED": 1, "TEXT": "hello",
               "TO": "@SITREP", "UTC": 1769740328005, "_ID": -1},
    "type": "RX.DIRECTED", "value": "HC5PH: @SITREP MSG hello"}
PTT_PUSH = {"params": {"PTT": True, "UTC": 1768760160665, "_ID": -1},
            "type": "RIG.PTT", "value": "on"}


class TestCaptureParsing(unittest.TestCase):
    def test_directed_message_is_flattened(self):
        rec = capture.event_record(DIRECTED_MSG)
        self.assertEqual(rec["type"], "RX.DIRECTED")
        self.assertEqual(rec["from"], "HC5PH")
        self.assertEqual(rec["to"], "@SITREP")
        self.assertEqual(rec["snr"], -24)
        self.assertEqual(rec["utc"], 1769740328005)

    def test_grid_whitespace_is_stripped(self):
        # The API pads several string fields (" FI06", " MSG"); storing them
        # raw makes every downstream comparison and lookup subtly wrong.
        self.assertEqual(capture.event_record(DIRECTED_MSG)["grid"], "FI06")
        self.assertEqual(capture.event_record(DIRECTED_MSG)["cmd"], "MSG")

    def test_ptt_push_records_the_boolean(self):
        rec = capture.event_record(PTT_PUSH)
        self.assertIs(rec["ptt"], True)

    def test_uninteresting_events_are_dropped(self):
        self.assertIsNone(capture.event_record({"type": "TX.FRAME", "params": {}}))
        self.assertIsNone(capture.event_record({"type": "STATION.STATUS", "params": {}}))

    def test_missing_utc_falls_back_to_now(self):
        rec = capture.event_record({"type": "RX.SPOT", "params": {"CALL": "K1ABC"}},
                                    now_fn=lambda: 1700.0)
        self.assertEqual(rec["utc"], 1700000)


class TestCaptureRolling(unittest.TestCase):
    def test_directed_messages_accumulate(self):
        state = capture.roll(capture.initial_state(), capture.event_record(DIRECTED_MSG))
        self.assertEqual(len(state["directed"]), 1)
        self.assertEqual(state["directed"][0]["from"], "HC5PH")

    def test_scrollback_is_bounded(self):
        # Unbounded growth would make js8-state.json grow without limit across
        # a multi-hour session and slow the dashboard poll to a crawl.
        state = capture.initial_state()
        rec = capture.event_record(DIRECTED_MSG)
        for _ in range(capture.MAX_DIRECTED + 25):
            state = capture.roll(state, rec)
        self.assertEqual(len(state["directed"]), capture.MAX_DIRECTED)
        self.assertLessEqual(len(state["activity"]), capture.MAX_ACTIVITY)

    def test_heard_stations_are_deduped_by_callsign(self):
        state = capture.initial_state()
        for _ in range(3):
            state = capture.roll(state, capture.event_record(DIRECTED_MSG))
        self.assertEqual(list(state["heard"]), ["HC5PH"])

    def test_ptt_push_updates_state_without_polluting_the_message_log(self):
        state = capture.roll(capture.initial_state(), capture.event_record(PTT_PUSH))
        self.assertTrue(state["ptt"])
        self.assertEqual(state["directed"], [])
        self.assertEqual(state["activity"], [])

    def test_roll_does_not_mutate_its_input(self):
        original = capture.initial_state()
        capture.roll(original, capture.event_record(DIRECTED_MSG))
        self.assertEqual(original["directed"], [])


class TestCaptureRotation(unittest.TestCase):
    def test_decode_log_is_rotated_hourly_like_ft8s(self):
        p = capture.decode_path(now=0)  # 1970-01-01T00:00Z
        self.assertTrue(p.endswith(os.path.join("1970-01-01", "00.jsonl")), p)


class TestContract(unittest.TestCase):
    def test_exposes_the_same_five_functions_ft8_does(self):
        # mode_switch.run_changeover() calls exactly these.
        for fn in ("start", "stop", "is_running", "sanity_check", "preflight"):
            self.assertTrue(callable(getattr(pipeline, fn)), fn)

    def test_process_pattern_is_specific_to_our_instance(self):
        # pgrep -f on a bare "JS8Call" would match a hand-run copy too, and
        # stop() would kill the operator's own session out from under them.
        self.assertIn(pipeline.RIG_NAME, pipeline.PROC_PATTERN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
