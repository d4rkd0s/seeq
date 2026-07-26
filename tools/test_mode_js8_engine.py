#!/usr/bin/env python3
"""Tests for bin/modes/js8/engine.py and watchdog.py -- JS8 mode's TX path.

This is the safety-critical suite. Nothing here opens a socket to a real
JS8Call-improved, spawns a real watchdog, or keys a real radio: the API client,
the clock, and the process spawner are all injected, so the multi-minute
timing behaviour is exercised in milliseconds.

What's being pinned down, in rough order of how badly it would matter:

  * send() refuses outright unless the caller explicitly confirms -- CLAUDE.md
    rule 1, "never key PTT / transmit autonomously".
  * send() reads the dial back and refuses on mismatch, and refuses a
    frequency outside the operator's licence privileges, before anything is
    queued.
  * the watchdog re-arms per transmitted frame rather than trusting one
    deadline for a whole multi-frame message, and still has an absolute
    session cap so a long queue can't key indefinitely.
  * a halt is attempted on every failure path, never skipped because some
    earlier step already failed.

Run: python3 tools/test_mode_js8_engine.py
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watchdog = _load("js8_watchdog", "bin/modes/js8/watchdog.py")
engine = _load("js8_engine", "bin/modes/js8/engine.py")


class FakeClient:
    def __init__(self, dial=7078000, offset=1500, speed=0, ptt=False, fail_on=None):
        self.dial = dial
        self.offset = offset
        self.speed = speed
        self.ptt = ptt
        self.fail_on = fail_on or set()
        self.calls = []

    def _maybe_fail(self, what):
        if what in self.fail_on:
            raise engine.api.Js8ApiError(f"simulated {what} failure")

    def connect(self):
        self.calls.append("connect")
        self._maybe_fail("connect")
        return self

    def close(self):
        self.calls.append("close")

    def get_freq(self):
        self.calls.append("get_freq")
        self._maybe_fail("get_freq")
        return self.dial, self.offset

    def get_ptt(self):
        self.calls.append("get_ptt")
        return self.ptt, ""

    def get_speed(self):
        self.calls.append("get_speed")
        return self.speed

    def set_speed(self, n):
        self.calls.append(f"set_speed:{n}")
        self.speed = n

    def send_message(self, text):
        self.calls.append(f"send_message:{text}")
        self._maybe_fail("send_message")

    def tx_halt(self):
        self.calls.append("tx_halt")
        self._maybe_fail("tx_halt")
        return True

    def queue_depth(self):
        self.calls.append("queue_depth")
        return 0

    def inbox_store(self, call, text):
        self.calls.append(f"inbox_store:{call}:{text}")
        return 1


class TestDeadlines(unittest.TestCase):
    """FT8's 14 s watchdog is tuned to a 15 s cycle. JS8 frames run from 3.95 s
    (JS8 40) to 25.28 s (Slow), so a single borrowed constant would either fire
    mid-transmission or leave a stuck carrier up far too long."""

    def test_every_speed_has_a_deadline_longer_than_its_frame(self):
        for speed, frame_s in engine.api.SPEED_TX_SECONDS.items():
            d = watchdog.deadline_for_speed(speed)
            self.assertGreater(d, frame_s, f"speed {speed} deadline must clear its frame")

    def test_deadlines_are_ordered_like_the_frame_lengths(self):
        self.assertLess(watchdog.deadline_for_speed(engine.api.SPEED_TURBO),
                         watchdog.deadline_for_speed(engine.api.SPEED_FAST))
        self.assertLess(watchdog.deadline_for_speed(engine.api.SPEED_FAST),
                         watchdog.deadline_for_speed(engine.api.SPEED_NORMAL))
        self.assertLess(watchdog.deadline_for_speed(engine.api.SPEED_NORMAL),
                         watchdog.deadline_for_speed(engine.api.SPEED_SLOW))

    def test_slow_mode_deadline_covers_its_25_second_frame(self):
        self.assertGreater(watchdog.deadline_for_speed(engine.api.SPEED_SLOW), 25.28)

    def test_unknown_speed_falls_back_to_the_most_generous_deadline(self):
        # Fail safe, not fail short: cutting a legitimate transmission is a
        # worse first guess than waiting a little longer to act.
        self.assertGreaterEqual(watchdog.deadline_for_speed(99),
                                 watchdog.deadline_for_speed(engine.api.SPEED_SLOW))


class TestWatchdogState(unittest.TestCase):
    def _wd(self, **kw):
        kw.setdefault("deadline_s", 17.0)
        kw.setdefault("max_session_s", 300.0)
        kw.setdefault("started_at", 0.0)
        return watchdog.WatchdogState(**kw)

    def test_idle_watchdog_never_halts(self):
        wd = self._wd()
        halt, why = wd.check(now=1000.0)
        self.assertFalse(halt, why)

    def test_arms_on_ptt_on_and_halts_when_the_frame_overruns(self):
        wd = self._wd()
        wd.on_ptt(True, now=10.0)
        self.assertFalse(wd.check(now=20.0)[0])
        halt, why = wd.check(now=28.0)
        self.assertTrue(halt)
        self.assertIn("deadline", why.lower())

    def test_ptt_off_in_time_disarms(self):
        wd = self._wd()
        wd.on_ptt(True, now=10.0)
        wd.on_ptt(False, now=22.0)
        self.assertFalse(wd.check(now=1000.0)[0])

    def test_rearms_per_frame_for_a_multi_frame_message(self):
        # One TX.SEND_MESSAGE can produce several PTT on/off cycles (API.md's
        # own example shows two). A single whole-message deadline would either
        # be too short for a long queue or uselessly long for one frame; the
        # unit being protected is the individual key-up.
        wd = self._wd()
        for i in range(6):
            t = 10.0 + i * 20.0
            wd.on_ptt(True, now=t)
            self.assertFalse(wd.check(now=t + 12.0)[0], f"frame {i} cut short")
            wd.on_ptt(False, now=t + 13.0)
        self.assertFalse(wd.check(now=200.0)[0])

    def test_absolute_session_cap_halts_even_while_frames_keep_rearming(self):
        # Without this, a big enough queue could legitimately re-arm forever.
        wd = self._wd(max_session_s=100.0)
        t = 0.0
        halted = False
        while t < 400.0:
            wd.on_ptt(True, now=t)
            halt, why = wd.check(now=t + 5.0)
            if halt:
                halted = True
                self.assertIn("session", why.lower())
                break
            wd.on_ptt(False, now=t + 6.0)
            t += 20.0
        self.assertTrue(halted, "session cap never fired")

    def test_session_cap_is_under_the_ten_minute_id_requirement(self):
        # 47 CFR 97.119: identify at least every 10 minutes. JS8 carries the
        # callsign in its directed frames, but the cap should not be the thing
        # that makes that marginal.
        self.assertLess(watchdog.MAX_SESSION_S, 600)

    def test_frequency_mismatch_while_keyed_halts(self):
        wd = self._wd(expected_dial=7078000, dial_tolerance_hz=100)
        wd.on_ptt(True, now=10.0)
        wd.on_dial(7040000, now=10.5)
        halt, why = wd.check(now=11.0)
        self.assertTrue(halt)
        self.assertIn("dial", why.lower())

    def test_dial_within_tolerance_is_fine(self):
        wd = self._wd(expected_dial=7078000, dial_tolerance_hz=100)
        wd.on_ptt(True, now=10.0)
        wd.on_dial(7078050, now=10.5)
        self.assertFalse(wd.check(now=11.0)[0])

    def test_no_expected_dial_means_no_frequency_gate(self):
        wd = self._wd(expected_dial=None)
        wd.on_ptt(True, now=10.0)
        wd.on_dial(1, now=10.5)
        self.assertFalse(wd.check(now=11.0)[0])


class TestWatchdogRun:
    pass


class TestWatchdogLoop(unittest.TestCase):
    def test_halts_the_transmitter_when_a_frame_overruns(self):
        # Drive the real loop with a scripted event stream and a fake clock:
        # PTT goes on and never goes off.
        c = FakeClient()
        events = [{"type": "RIG.PTT", "value": "on", "params": {"PTT": True}}]
        ticks = iter([0.0, 1.0, 5.0, 40.0, 41.0])

        def read_event(timeout=None):
            return events.pop(0) if events else None
        c.read_event = read_event
        result = watchdog.run(client=c, clock_fn=lambda: next(ticks),
                               sleep_fn=lambda s: None, deadline_s=17.0,
                               max_session_s=300.0, max_iterations=5)
        self.assertIn("tx_halt", c.calls)
        self.assertTrue(result["halted"])

    def test_exits_cleanly_without_halting_when_transmission_completes(self):
        c = FakeClient()
        events = [{"type": "RIG.PTT", "value": "on", "params": {"PTT": True}},
                  {"type": "RIG.PTT", "value": "off", "params": {"PTT": False}}]
        ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])

        def read_event(timeout=None):
            return events.pop(0) if events else None
        c.read_event = read_event
        result = watchdog.run(client=c, clock_fn=lambda: next(ticks),
                               sleep_fn=lambda s: None, deadline_s=17.0,
                               max_session_s=300.0, max_iterations=5)
        self.assertNotIn("tx_halt", c.calls)
        self.assertFalse(result["halted"])


class TestSend(unittest.TestCase):
    def _send(self, text="N0CALL TEST", confirm=True, client=None, spawned=None,
              conf=None, **kw):
        c = client or FakeClient()
        conf = conf or {"BAND": "40m", "LICENSE_CLASS": "general"}
        return engine.send(text, confirm=confirm,
                            client_fn=lambda: c,
                            spawn_fn=lambda cmd, log: (spawned if spawned is not None else []).append(cmd),
                            conf=conf, **kw), c

    def test_refuses_without_an_explicit_confirm(self):
        # The single most important assertion in this file.
        (ok, detail), c = self._send(confirm=False)
        self.assertFalse(ok)
        self.assertIn("confirm", detail.lower())
        self.assertEqual([x for x in c.calls if x.startswith("send_message")], [])

    def test_refuses_empty_text(self):
        (ok, detail), c = self._send(text="   ")
        self.assertFalse(ok)
        self.assertNotIn("send_message:", " ".join(c.calls))

    def test_reads_the_dial_back_before_transmitting(self):
        (ok, detail), c = self._send()
        self.assertTrue(ok, detail)
        self.assertLess(c.calls.index("get_freq"),
                         [i for i, x in enumerate(c.calls) if x.startswith("send_message")][0],
                         "dial must be read back BEFORE the message is queued")

    def test_refuses_when_the_dial_is_not_the_js8_calling_frequency(self):
        c = FakeClient(dial=7040000)  # 40m, but nowhere near 7.078
        (ok, detail), c = self._send(client=c)
        self.assertFalse(ok)
        self.assertIn("dial", detail.lower())
        self.assertNotIn("send_message:N0CALL TEST", c.calls)

    def test_refuses_a_frequency_outside_licence_privileges(self):
        # Reuses bin/band_plan.py's existing FCC tables rather than a second
        # copy of the privilege ranges that could drift out of step.
        c = FakeClient(dial=7200000)  # 40m phone segment -- not data
        (ok, detail), _ = self._send(client=c, conf={"BAND": "40m", "LICENSE_CLASS": "general"})
        self.assertFalse(ok)
        self.assertTrue("privile" in detail.lower() or "dial" in detail.lower(), detail)

    def test_refuses_a_band_with_no_known_js8_calling_frequency(self):
        # Fail closed: guessing a frequency for an untabled band is exactly the
        # kind of assumption that puts a signal outside the band edge.
        (ok, detail), _ = self._send(conf={"BAND": "6m", "LICENSE_CLASS": "general"})
        self.assertFalse(ok)
        self.assertIn("6m", detail)

    def test_arms_the_watchdog_before_returning(self):
        spawned = []
        (ok, detail), c = self._send(spawned=spawned)
        self.assertTrue(ok, detail)
        self.assertTrue(spawned, "no watchdog was spawned")
        self.assertTrue(any("watchdog.py" in " ".join(cmd) for cmd in spawned), spawned)

    def test_watchdog_is_armed_before_the_message_is_queued(self):
        # Pre-armed, exactly like qso.py arms its unkey subprocess before
        # key-up -- arming afterwards leaves a window with no backstop.
        order = []
        c = FakeClient()
        real_send = c.send_message

        def tracked_send(text):
            order.append("send_message")
            real_send(text)
        c.send_message = tracked_send
        engine.send("HELLO", confirm=True, client_fn=lambda: c,
                     spawn_fn=lambda cmd, log: order.append("spawn_watchdog"),
                     conf={"BAND": "40m", "LICENSE_CLASS": "general"})
        self.assertEqual(order, ["spawn_watchdog", "send_message"])

    def test_dryrun_transmits_nothing(self):
        (ok, detail), c = self._send(dryrun=True)
        self.assertTrue(ok, detail)
        self.assertNotIn("send_message:N0CALL TEST", c.calls)

    def test_api_failure_during_send_attempts_a_halt(self):
        # If we can't tell whether the message went out, assume the worst.
        c = FakeClient(fail_on={"send_message"})
        (ok, detail), c = self._send(client=c)
        self.assertFalse(ok)
        self.assertIn("tx_halt", c.calls)

    def test_unreachable_api_is_a_clean_refusal_not_a_crash(self):
        c = FakeClient(fail_on={"connect"})
        (ok, detail), _ = self._send(client=c)
        self.assertFalse(ok)
        self.assertTrue(detail)


class TestHaltAndHelpers(unittest.TestCase):
    def test_halt_calls_tx_halt(self):
        c = FakeClient()
        ok, detail = engine.halt(client_fn=lambda: c)
        self.assertTrue(ok, detail)
        self.assertIn("tx_halt", c.calls)

    def test_halt_reports_failure_rather_than_claiming_success(self):
        # A STOP button that lies is worse than one that says it failed.
        c = FakeClient(fail_on={"tx_halt"})
        ok, detail = engine.halt(client_fn=lambda: c)
        self.assertFalse(ok)
        self.assertTrue(detail)

    def test_set_speed_validates(self):
        c = FakeClient()
        ok, _ = engine.set_speed(engine.api.SPEED_SLOW, client_fn=lambda: c)
        self.assertTrue(ok)
        self.assertIn("set_speed:4", c.calls)
        ok, detail = engine.set_speed(3, client_fn=lambda: c)
        self.assertFalse(ok)

    def test_calling_frequency_table_matches_the_skill_file(self):
        self.assertEqual(engine.JS8_CALLING_HZ["40m"], 7078000)
        self.assertEqual(engine.JS8_CALLING_HZ["20m"], 14078000)
        self.assertEqual(engine.JS8_CALLING_HZ["30m"], 10130000)

    def test_every_calling_frequency_is_inside_general_data_privileges(self):
        # Including the top of the audio passband, since the emitted signal is
        # dial + offset, not the dial itself.
        for band, dial in engine.JS8_CALLING_HZ.items():
            for probe in (dial, dial + engine.MAX_OFFSET_HZ):
                ok, detail = engine.band_plan.check_frequency("general", band, probe, "data")
                self.assertTrue(ok, f"{band} {probe} Hz: {detail}")


class TestContract(unittest.TestCase):
    def test_exposes_what_the_dashboard_and_mode_switch_need(self):
        for fn in ("send", "halt", "set_speed", "status"):
            self.assertTrue(callable(getattr(engine, fn)), fn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
