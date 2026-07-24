#!/usr/bin/env python3
"""Tests for bin/mode_switch.py -- the M0 sequenced, polled mode-changeover
state machine. No real subprocess, no real radio, no real wall-clock wait:
sleep_fn/clock_fn/load_mode_fn are all injected.
Run: python3 tools/test_mode_switch.py
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODE_SWITCH = os.path.join(ROOT, "bin", "mode_switch.py")


def _mode_switch_module():
    spec = importlib.util.spec_from_file_location("mode_switch", MODE_SWITCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mode_switch = _mode_switch_module()


class FakePipeline:
    """Stub standing in for bin/modes/<name>/pipeline.py in tests. running
    starts True for "already deployed" modes, False for a fresh one; stop()
    flips it False (so is_running() reflects a real stop) unless
    stays_running is set, simulating a stuck process."""

    def __init__(self, running=False, stays_running=False, sanity_ok=True, sanity_detail="clear",
                 preflight_ok=True, preflight_detail="clear"):
        self.running = running
        self.stays_running = stays_running
        self.sanity_ok = sanity_ok
        self.sanity_detail = sanity_detail
        self.preflight_ok = preflight_ok
        self.preflight_detail = preflight_detail
        self.start_calls = []
        self.stop_calls = []

    def start(self, dryrun=False):
        self.start_calls.append(dryrun)
        self.running = True
        return {"started": True}

    def stop(self, dryrun=False):
        self.stop_calls.append(dryrun)
        if not self.stays_running:
            self.running = False
        return {"stopped": True}

    def is_running(self):
        return self.running

    def sanity_check(self):
        return self.sanity_ok, self.sanity_detail

    def preflight(self):
        return self.preflight_ok, self.preflight_detail


class FakeEngine:
    pass


class TestPlanChangeoverStages(unittest.TestCase):
    def test_boot_case_no_current_mode(self):
        self.assertEqual(mode_switch.plan_changeover_stages(None, "ft8"),
                          ["sanity_check", "starting", "done"])

    def test_switch_between_two_modes(self):
        self.assertEqual(mode_switch.plan_changeover_stages("ft8", "js8"),
                          ["stopping", "verifying", "sanity_check", "starting", "done"])

    def test_same_mode_short_circuits(self):
        self.assertEqual(mode_switch.plan_changeover_stages("ft8", "ft8"), ["already_active"])


class TestRunChangeover(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.status_path = os.path.join(self.tmpdir, "mode-switch.json")
        self.active_mode_path = os.path.join(self.tmpdir, "active-mode.json")
        self.sleeps = []

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _clock(self, step=5):
        state = {"t": 0}

        def clock_fn():
            state["t"] += step
            return state["t"]
        return clock_fn

    def _write_active_mode(self, mode):
        with open(self.active_mode_path, "w") as f:
            json.dump({"mode": mode}, f)

    def test_boot_success_writes_active_mode(self):
        ft8 = FakePipeline()
        ok, detail = mode_switch.run_changeover(
            "ft8", sleep_fn=self.sleeps.append, clock_fn=self._clock(),
            status_path=self.status_path, active_mode_path=self.active_mode_path,
            load_mode_fn=lambda name: (ft8, FakeEngine()))
        self.assertTrue(ok, detail)
        self.assertEqual(ft8.start_calls, [False])
        with open(self.active_mode_path) as f:
            self.assertEqual(json.load(f)["mode"], "ft8")
        with open(self.status_path) as f:
            self.assertEqual(json.load(f)["stage"], "done")

    def test_switch_stops_current_then_starts_target(self):
        self._write_active_mode("ft8")
        ft8 = FakePipeline(running=True)
        js8 = FakePipeline(running=False)
        registry = {"ft8": (ft8, FakeEngine()), "js8": (js8, FakeEngine())}
        ok, detail = mode_switch.run_changeover(
            "js8", sleep_fn=self.sleeps.append, clock_fn=self._clock(),
            status_path=self.status_path, active_mode_path=self.active_mode_path,
            load_mode_fn=lambda name: registry[name])
        self.assertTrue(ok, detail)
        self.assertEqual(len(ft8.stop_calls), 1)
        self.assertEqual(len(js8.start_calls), 1)
        with open(self.active_mode_path) as f:
            self.assertEqual(json.load(f)["mode"], "js8")

    def test_stuck_process_times_out_and_does_not_switch(self):
        self._write_active_mode("ft8")
        ft8 = FakePipeline(running=True, stays_running=True)
        js8 = FakePipeline()
        registry = {"ft8": (ft8, FakeEngine()), "js8": (js8, FakeEngine())}
        ok, detail = mode_switch.run_changeover(
            "js8", poll_timeout_s=10, sleep_fn=self.sleeps.append, clock_fn=self._clock(step=5),
            status_path=self.status_path, active_mode_path=self.active_mode_path,
            load_mode_fn=lambda name: registry[name])
        self.assertFalse(ok)
        self.assertIn("did not stop", detail)
        self.assertEqual(js8.start_calls, [])
        with open(self.active_mode_path) as f:
            self.assertEqual(json.load(f)["mode"], "ft8")
        with open(self.status_path) as f:
            self.assertEqual(json.load(f)["stage"], "error")

    def test_failed_sanity_check_hard_aborts(self):
        self._write_active_mode("ft8")
        ft8 = FakePipeline(running=True, sanity_ok=False, sanity_detail="PTT stuck")
        js8 = FakePipeline()
        registry = {"ft8": (ft8, FakeEngine()), "js8": (js8, FakeEngine())}
        ok, detail = mode_switch.run_changeover(
            "js8", sleep_fn=self.sleeps.append, clock_fn=self._clock(),
            status_path=self.status_path, active_mode_path=self.active_mode_path,
            load_mode_fn=lambda name: registry[name])
        self.assertFalse(ok)
        self.assertEqual(detail, "PTT stuck")
        self.assertEqual(js8.start_calls, [])
        with open(self.active_mode_path) as f:
            self.assertEqual(json.load(f)["mode"], "ft8")

    def test_same_mode_is_a_clean_noop(self):
        self._write_active_mode("ft8")
        ft8 = FakePipeline(running=True)
        ok, detail = mode_switch.run_changeover(
            "ft8", sleep_fn=self.sleeps.append, clock_fn=self._clock(),
            status_path=self.status_path, active_mode_path=self.active_mode_path,
            load_mode_fn=lambda name: (ft8, FakeEngine()))
        self.assertTrue(ok, detail)
        self.assertEqual(ft8.start_calls, [])
        self.assertEqual(ft8.stop_calls, [])
        with open(self.status_path) as f:
            self.assertEqual(json.load(f)["stage"], "already_active")

    def test_boot_preflight_failure_hard_aborts(self):
        # e.g. the CAT port has physically vanished -- same scenario as the
        # real disconnect hit earlier this session (bin/seeq's own preflight
        # caught it the same way).
        ft8 = FakePipeline(preflight_ok=False, preflight_detail="CAT port missing (/dev/ttyUSB0)")
        ok, detail = mode_switch.run_changeover(
            "ft8", sleep_fn=self.sleeps.append, clock_fn=self._clock(),
            status_path=self.status_path, active_mode_path=self.active_mode_path,
            load_mode_fn=lambda name: (ft8, FakeEngine()))
        self.assertFalse(ok)
        self.assertIn("CAT port missing", detail)
        self.assertEqual(ft8.start_calls, [])
        self.assertFalse(os.path.exists(self.active_mode_path))

    def test_boot_does_not_fail_if_target_pipeline_already_running(self):
        # seeq start's unconditional rx-loop autostart may have already beat
        # the changeover to it -- preflight() must not treat "already
        # running" as a failure (start() is already a safe no-op for that).
        ft8 = FakePipeline(running=True)
        ok, detail = mode_switch.run_changeover(
            "ft8", sleep_fn=self.sleeps.append, clock_fn=self._clock(),
            status_path=self.status_path, active_mode_path=self.active_mode_path,
            load_mode_fn=lambda name: (ft8, FakeEngine()))
        self.assertTrue(ok, detail)
        with open(self.active_mode_path) as f:
            self.assertEqual(json.load(f)["mode"], "ft8")

    def test_unknown_target_mode_errors_cleanly(self):
        def raiser(name):
            raise mode_switch.mode_registry.UnknownModeError(f"unknown mode {name!r}")
        ok, detail = mode_switch.run_changeover(
            "bogus", sleep_fn=self.sleeps.append, clock_fn=self._clock(),
            status_path=self.status_path, active_mode_path=self.active_mode_path,
            load_mode_fn=raiser)
        self.assertFalse(ok)
        self.assertIn("bogus", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
