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


def extract_qrz_job_due_js():
    """Slice the qrzJobDue() scheduling function verbatim out of dashboard.py's
    rendered PAGE, between two stable markers: its declaration and the next
    line (the qrzAuto* state variables that follow it)."""
    page = _dashboard_module().PAGE
    start = page.index("function qrzJobDue(")
    end = page.index("\nlet qrzAutoArmedAt", start)
    snippet = page[start:end]
    assert "return (elapsedMs-lastFireMs)" in snippet, (
        "qrzJobDue() not found between markers -- dashboard.py layout changed, "
        "update the markers in tools/test_dashboard_js.py")
    return snippet


def run_qrz_job_due(elapsed_ms, period_ms, offset_ms, last_fire_ms):
    """Evaluate the real qrzJobDue() JS (via Node) for one set of args.
    last_fire_ms=None maps to JS null."""
    js = extract_qrz_job_due_js()
    last = "null" if last_fire_ms is None else str(last_fire_ms)
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(qrzJobDue(%d, %d, %d, %s)));"
    ) % (elapsed_ms, period_ms, offset_ms, last)
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


def extract_should_flash_new_country_js():
    """Slice shouldFlashNewCountry() verbatim out of dashboard.py's rendered
    PAGE, between its declaration and triggerNewCountryFlash() right after."""
    page = _dashboard_module().PAGE
    start = page.index("function shouldFlashNewCountry(")
    end = page.index("\nfunction triggerNewCountryFlash(", start)
    snippet = page[start:end]
    assert "new_country" in snippet, (
        "shouldFlashNewCountry() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_should_flash_new_country(e, chaser_running, tx, last_tx):
    js = extract_should_flash_new_country_js()
    e_json = "null" if e is None else json.dumps(e)
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(shouldFlashNewCountry(%s, %s, %s, %s)));"
    ) % (e_json, "true" if chaser_running else "false",
         "true" if tx else "false", "true" if last_tx else "false")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_is_grid_js():
    """Slice the one-line isGrid() helper verbatim out of dashboard.py's
    rendered PAGE -- resolveTargetGrid() depends on it, and rather than
    duplicate the regex we prefix this in ourselves when testing that
    function (see run_resolve_target_grid)."""
    page = _dashboard_module().PAGE
    start = page.index("function isGrid(")
    end = page.index("\n", start)
    return page[start:end]


def extract_tx_line_helpers_js():
    """Slice txLineActive()/resolveTargetGrid() verbatim out of dashboard.py's
    rendered PAGE, between their declaration and the renderTX() function that
    consumes them."""
    page = _dashboard_module().PAGE
    start = page.index("function txLineActive(")
    end = page.index("\nfunction renderTX(", start)
    snippet = page[start:end]
    assert "resolveTargetGrid" in snippet, (
        "txLineActive()/resolveTargetGrid() not found between markers -- "
        "dashboard.py layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_tx_line_active(e, chaser_running):
    js = extract_tx_line_helpers_js()
    e_json = "null" if e is None else json.dumps(e)
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(txLineActive(%s, %s)));"
    ) % (e_json, "true" if chaser_running else "false")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def run_resolve_target_grid(target, engine_grid, recent_grid_by_call):
    js = extract_is_grid_js() + "\n" + extract_tx_line_helpers_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(resolveTargetGrid(%s, %s, %s)));"
    ) % (json.dumps(target), json.dumps(engine_grid), json.dumps(recent_grid_by_call))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_snr_risk_level_js():
    """Slice snrRiskLevel() verbatim out of dashboard.py's rendered PAGE,
    between its declaration and the loadCfg() function that wires it into
    the SNR floor slider's initial display."""
    page = _dashboard_module().PAGE
    start = page.index("function snrRiskLevel(")
    end = page.index("\nasync function loadCfg(", start)
    snippet = page[start:end]
    assert "pct" in snippet, (
        "snrRiskLevel() not found between markers -- dashboard.py layout "
        "changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_snr_risk_level(floor_db):
    js = extract_snr_risk_level_js()
    script = js + "\nprocess.stdout.write(JSON.stringify(snrRiskLevel(%r)));" % floor_db
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_rough_tx_label_js():
    """Slice roughTxLabel() verbatim out of dashboard.py's rendered PAGE,
    between its declaration and updateNextTx() which consumes it."""
    page = _dashboard_module().PAGE
    start = page.index("function roughTxLabel(")
    end = page.index("\nfunction updateNextTx(", start)
    snippet = page[start:end]
    assert "tx-soon" in snippet, (
        "roughTxLabel() not found between markers -- dashboard.py layout "
        "changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_rough_tx_label(secs):
    js = extract_rough_tx_label_js()
    script = js + "\nprocess.stdout.write(JSON.stringify(roughTxLabel(%r)));" % secs
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


class TestQrzJobDue(unittest.TestCase):
    """Sync fires at t=0,120s,240s,... (offset 0); refresh fires at
    t=60s,180s,300s,... (offset 60s) -- each job repeats every 120s once
    started, and the two are staggered 60s apart from each other."""

    PERIOD = 120000
    STAGGER = 60000

    def test_sync_fires_immediately_when_never_fired(self):
        self.assertTrue(run_qrz_job_due(0, self.PERIOD, 0, None))

    def test_refresh_not_due_before_its_stagger_offset(self):
        self.assertFalse(run_qrz_job_due(0, self.PERIOD, self.STAGGER, None))

    def test_refresh_fires_at_its_first_stagger_offset(self):
        self.assertTrue(run_qrz_job_due(self.STAGGER, self.PERIOD, self.STAGGER, None))

    def test_sync_not_due_again_before_full_period(self):
        self.assertFalse(run_qrz_job_due(119000, self.PERIOD, 0, 0))

    def test_sync_due_again_exactly_at_full_period(self):
        self.assertTrue(run_qrz_job_due(120000, self.PERIOD, 0, 0))

    def test_refresh_second_fire_is_two_minutes_after_its_first(self):
        self.assertFalse(run_qrz_job_due(179000, self.PERIOD, self.STAGGER, self.STAGGER))
        self.assertTrue(run_qrz_job_due(180000, self.PERIOD, self.STAGGER, self.STAGGER))


class TestShouldFlashNewCountry(unittest.TestCase):
    """The new-country flash must be edge-triggered off an ACTUAL
    transmission toward a new-country target -- never off the passive
    candidate list (that made a page reload immediately re-flash whatever
    was last shown, since the in-memory dedup list reset to empty). Fires
    once per real TX start ("each call to it"), stops the moment the
    target is no longer being actively pursued (state leaves calling/qso --
    e.g. once logged, "QSO'd fully")."""

    CALLING = {"state": "calling", "target": "DL2XYZ", "new_country": True}
    QSO = {"state": "qso", "target": "DL2XYZ", "new_country": True}
    LOGGED = {"state": "logged", "target": "DL2XYZ", "new_country": True}
    NOT_NEW = {"state": "calling", "target": "W1ABC", "new_country": False}

    def test_fires_on_tx_rising_edge_while_calling_new_country(self):
        self.assertTrue(run_should_flash_new_country(self.CALLING, True, True, False))

    def test_fires_on_tx_rising_edge_during_qso_exchange(self):
        # "each call to it" -- rrpt/b73 steps happen in state 'qso', not just 'calling'
        self.assertTrue(run_should_flash_new_country(self.QSO, True, True, False))

    def test_does_not_refire_mid_transmission(self):
        # tx already true last tick -- not a fresh call, don't re-flash continuously
        self.assertFalse(run_should_flash_new_country(self.CALLING, True, True, True))

    def test_false_when_not_transmitting(self):
        self.assertFalse(run_should_flash_new_country(self.CALLING, True, False, False))

    def test_false_once_qsod_fully_logged(self):
        self.assertFalse(run_should_flash_new_country(self.LOGGED, True, True, False))

    def test_false_when_target_country_not_new(self):
        self.assertFalse(run_should_flash_new_country(self.NOT_NEW, True, True, False))

    def test_false_when_chaser_not_actually_running(self):
        # stale engine.json snapshot -- same staleness guard as txLineActive
        self.assertFalse(run_should_flash_new_country(self.CALLING, False, True, False))

    def test_false_when_hunting_no_target_locked(self):
        self.assertFalse(run_should_flash_new_country(
            {"state": "hunting", "target": None, "new_country": False}, True, True, False))

    def test_false_when_engine_null(self):
        self.assertFalse(run_should_flash_new_country(None, True, True, False))


class TestSecsToNextSlot(unittest.TestCase):
    def test_at_slot_boundary_returns_full_slot(self):
        self.assertEqual(run_secs_to_next_slot(0), 15)

    def test_mid_slot(self):
        self.assertAlmostEqual(run_secs_to_next_slot(14.5), 0.5, places=5)

    def test_exact_boundary_wraps_to_full_slot_not_zero(self):
        self.assertEqual(run_secs_to_next_slot(15), 15)

    def test_second_slot_mid_point(self):
        self.assertAlmostEqual(run_secs_to_next_slot(22.3), 7.7, places=5)


class TestTxLineActive(unittest.TestCase):
    """The map's red TX line must reflect whether the chaser process is
    actually alive, not just what engine.json's snapshot last said --
    engine.json is never reset when the chaser exits, so a killed/finished
    run can leave a stale 'calling' state on disk (and on the map) forever."""

    def test_calling_with_chaser_running_is_active(self):
        self.assertTrue(run_tx_line_active({"state": "calling", "target": "OH3JF"}, True))

    def test_qso_state_is_active(self):
        self.assertTrue(run_tx_line_active({"state": "qso", "target": "OH3JF"}, True))

    def test_stale_state_while_chaser_not_running_is_inactive(self):
        self.assertFalse(run_tx_line_active({"state": "calling", "target": "OH3JF"}, False))

    def test_hunting_state_is_inactive(self):
        self.assertFalse(run_tx_line_active({"state": "hunting", "target": None}, True))

    def test_no_target_is_inactive(self):
        self.assertFalse(run_tx_line_active({"state": "calling", "target": None}, True))

    def test_null_engine_is_inactive(self):
        self.assertFalse(run_tx_line_active(None, True))


class TestResolveTargetGrid(unittest.TestCase):
    """Many CQs omit their grid, and engine.json's grid field is only ever
    set from the CQ we originally answered -- so a gridless CQ meant the TX
    line never drew for that whole chase, even mid-transmission. Fall back to
    any grid we've recently heard for that same call elsewhere."""

    def test_uses_engine_grid_when_present(self):
        self.assertEqual(run_resolve_target_grid("OH3JF", "KP20", {}), "KP20")

    def test_falls_back_to_recently_heard_grid_when_engine_grid_blank(self):
        self.assertEqual(run_resolve_target_grid("OH3JF", "", {"OH3JF": "KP20"}), "KP20")

    def test_engine_grid_wins_over_recent_cache(self):
        self.assertEqual(run_resolve_target_grid("OH3JF", "KP20", {"OH3JF": "JN58"}), "KP20")

    def test_no_grid_anywhere_returns_empty(self):
        self.assertEqual(run_resolve_target_grid("OH3JF", "", {}), "")

    def test_ignores_garbage_in_recent_cache(self):
        self.assertEqual(run_resolve_target_grid("OH3JF", "", {"OH3JF": "RR73"}), "")


class TestSnrRiskLevel(unittest.TestCase):
    """A lower (more negative) SNR floor lets weaker candidates through --
    weaker candidates are less likely to hear our own QRP signal back
    (reciprocity), so risk of no response rises as the floor drops. Pure
    function driving the dashboard's SNR-floor slider risk meter."""

    def test_station_default_is_moderate(self):
        r = run_snr_risk_level(-16)
        self.assertEqual(r["level"], "moderate")

    def test_deep_floor_is_high_risk(self):
        r = run_snr_risk_level(-24)
        self.assertEqual(r["level"], "high")
        self.assertEqual(r["pct"], 100)

    def test_strong_signals_only_is_minimal_risk(self):
        r = run_snr_risk_level(0)
        self.assertEqual(r["level"], "minimal")
        self.assertEqual(r["pct"], 0)

    def test_risk_increases_as_floor_drops(self):
        weak = run_snr_risk_level(-22)
        strong = run_snr_risk_level(-4)
        self.assertGreater(weak["pct"], strong["pct"])

    def test_clamps_beyond_practical_range(self):
        below = run_snr_risk_level(-40)
        above = run_snr_risk_level(15)
        self.assertEqual(below["pct"], 100)
        self.assertEqual(above["pct"], 0)


class TestRoughTxLabel(unittest.TestCase):
    """The rough 'time to next slot' cockpit label: shown while Automatic CQ
    is running but no target/next_tx_epoch is locked in yet. Above 5s
    remaining it's a dim '~Ns to next slot' estimate; that stops ("ends")
    once 5s remain; from 3s remaining down it becomes an urgent
    'Transmitting in Ns' countdown (same tx-soon styling as a real
    scheduled key-up) -- the 2s in between (5s..3s) is intentionally blank,
    since nothing meaningful can be claimed about an imminent TX in that gap."""

    def test_above_five_seconds_shows_rough_estimate(self):
        r = run_rough_tx_label(10.0)
        self.assertEqual(r["text"], "~10.0s to next slot")
        self.assertEqual(r["cls"], "tx-rough")

    def test_just_above_five_still_rough(self):
        r = run_rough_tx_label(5.1)
        self.assertEqual(r["cls"], "tx-rough")

    def test_at_five_seconds_rough_estimate_ends(self):
        r = run_rough_tx_label(5.0)
        self.assertEqual(r["text"], "—")
        self.assertEqual(r["cls"], "")

    def test_between_five_and_three_is_blank(self):
        r = run_rough_tx_label(4.0)
        self.assertEqual(r["text"], "—")
        self.assertEqual(r["cls"], "")

    def test_at_three_seconds_urgent_countdown_begins(self):
        r = run_rough_tx_label(3.0)
        self.assertEqual(r["text"], "Transmitting in 3.00s")
        self.assertEqual(r["cls"], "tx-soon")

    def test_urgent_countdown_continues_below_three(self):
        r = run_rough_tx_label(1.2)
        self.assertEqual(r["text"], "Transmitting in 1.20s")
        self.assertEqual(r["cls"], "tx-soon")


if __name__ == "__main__":
    unittest.main(verbosity=2)
