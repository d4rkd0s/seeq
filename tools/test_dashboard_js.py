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


def extract_unkey_countdown_label_js():
    """Slice unkeyCountdownLabel() verbatim out of dashboard.py's rendered
    PAGE, between its declaration and updateNextTx() which consumes it."""
    page = _dashboard_module().PAGE
    start = page.index("function unkeyCountdownLabel(")
    end = page.index("\nfunction updateNextTx(", start)
    snippet = page[start:end]
    assert "ON AIR" in snippet, (
        "unkeyCountdownLabel() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_unkey_countdown_label(unkey_deadline_epoch, now_epoch_sec):
    js = extract_unkey_countdown_label_js()
    deadline = "null" if unkey_deadline_epoch is None else repr(unkey_deadline_epoch)
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(unkeyCountdownLabel(%s, %r)));"
    ) % (deadline, now_epoch_sec)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_mode_label_for_js():
    """Slice modeLabelFor() verbatim out of dashboard.py's rendered PAGE,
    between its declaration and pollModeState() which consumes it."""
    page = _dashboard_module().PAGE
    start = page.index("function modeLabelFor(")
    end = page.index("\nasync function pollModeState(", start)
    snippet = page[start:end]
    assert "registry" in snippet, (
        "modeLabelFor() not found between markers -- dashboard.py layout "
        "changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_mode_label_for(active_mode, registry):
    js = extract_mode_label_for_js()
    am = "null" if active_mode is None else json.dumps(active_mode)
    script = js + "\nprocess.stdout.write(modeLabelFor(%s, %s));" % (am, json.dumps(registry))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return r.stdout


def extract_header_status_label_js():
    """Slice headerStatusLabel() verbatim out of dashboard.py's rendered
    PAGE, between its declaration and refreshActionsState() which consumes
    it."""
    page = _dashboard_module().PAGE
    start = page.index("function headerStatusLabel(")
    end = page.index("\nasync function refreshActionsState(", start)
    snippet = page[start:end]
    assert "Transmitting" in snippet, (
        "headerStatusLabel() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_header_status_label(tx, chaser_running, rxloop_running):
    js = extract_header_status_label_js()
    script = js + "\nprocess.stdout.write(headerStatusLabel(%s,%s,%s));" % (
        json.dumps(tx), json.dumps(chaser_running), json.dumps(rxloop_running))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return r.stdout


def extract_bp_pills_html_js():
    """Slice escapeHtml()+bpPillsHtml() verbatim out of dashboard.py's
    rendered PAGE, between escapeHtml's declaration (bpPillsHtml calls it
    to neutralize hostile fields from bandpulse.net's live API response)
    and loadBandPulse() which consumes bpPillsHtml's output."""
    page = _dashboard_module().PAGE
    start = page.index("function escapeHtml(")
    end = page.index("\nasync function loadBandPulse(", start)
    snippet = page[start:end]
    assert "bpPill" in snippet, (
        "bpPillsHtml() not found between markers -- dashboard.py layout "
        "changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_bp_pills_html(top):
    js = extract_bp_pills_html_js()
    script = js + "\nprocess.stdout.write(bpPillsHtml(%s));" % json.dumps(top)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return r.stdout


def extract_pan_zoom_viewbox_js():
    """Slice panViewBox()/zoomViewBox() verbatim out of dashboard.py's
    rendered PAGE, prefixed with the two small constant-declaration lines
    they depend on (MW/MH, MIN_VB_W/MIN_VB_H) -- extracted separately
    rather than widening the main window to span everything in between
    (same technique as resolveTargetGrid()'s isGrid() dependency)."""
    page = _dashboard_module().PAGE
    c1_start = page.index("const MW=1000, MH=500;")
    c1_end = page.index("\n", c1_start)
    c2_start = page.index("const MIN_VB_W=110, MIN_VB_H=55;")
    c2_end = page.index("\n", c2_start)
    start = page.index("function panViewBox(")
    end = page.index("\nfunction lerp(", start)
    snippet = page[start:end]
    assert "zoomViewBox" in snippet, (
        "panViewBox()/zoomViewBox() not found between markers -- "
        "dashboard.py layout changed, update the markers in tools/test_dashboard_js.py")
    return page[c1_start:c1_end] + "\n" + page[c2_start:c2_end] + "\n" + snippet


def run_pan_viewbox(vb, dx_px, dy_px, svg_px_w, svg_px_h):
    js = extract_pan_zoom_viewbox_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(panViewBox(%s, %r, %r, %r, %r)));"
    ) % (json.dumps(vb), dx_px, dy_px, svg_px_w, svg_px_h)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def run_zoom_viewbox(vb, factor, cx_frac, cy_frac):
    js = extract_pan_zoom_viewbox_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(zoomViewBox(%s, %r, %r, %r)));"
    ) % (json.dumps(vb), factor, cx_frac, cy_frac)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_neighbor_zoom_js():
    """Slice resolveCountryIso2()/unionBBox()/neighborZoomBBox() verbatim
    out of dashboard.py's rendered PAGE, between their declaration and
    computeTargetBBox() which consumes them."""
    page = _dashboard_module().PAGE
    start = page.index("function resolveCountryIso2(")
    end = page.index("\nfunction computeTargetBBox(", start)
    snippet = page[start:end]
    assert "neighborZoomBBox" in snippet, (
        "resolveCountryIso2()/neighborZoomBBox() not found between markers -- "
        "dashboard.py layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_resolve_country_iso2(dxcc_name, countries):
    js = extract_neighbor_zoom_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(resolveCountryIso2(%s, %s)));"
    ) % (json.dumps(dxcc_name), json.dumps(countries))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def run_neighbor_zoom_bbox(target_iso2, countries_by_iso2, adjacency):
    js = extract_neighbor_zoom_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(neighborZoomBBox(%s, %s, %s)));"
    ) % (json.dumps(target_iso2), json.dumps(countries_by_iso2), json.dumps(adjacency))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_popup_screen_pos_js():
    """Slice popupScreenPos() verbatim out of dashboard.py's rendered PAGE,
    between its declaration and openCountryCard() which consumes it."""
    page = _dashboard_module().PAGE
    start = page.index("function popupScreenPos(")
    end = page.index("\nasync function openCountryCard(", start)
    snippet = page[start:end]
    assert "anchorX" in snippet, (
        "popupScreenPos() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_popup_screen_pos(rect, vb, px, py, popup_w, popup_h, gap, viewport_w, viewport_h):
    js = extract_popup_screen_pos_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(popupScreenPos(%s, %s, %r, %r, %r, %r, %r, %r, %r)));"
    ) % (json.dumps(rect), json.dumps(vb), px, py, popup_w, popup_h, gap, viewport_w, viewport_h)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_target_pick_message_js():
    """Slice targetPickMessage() verbatim out of dashboard.py's rendered
    PAGE, between its declaration and the next function after it."""
    page = _dashboard_module().PAGE
    start = page.index("function targetPickMessage(")
    end = page.index("\ndocument.getElementById('ccCallBtn')", start)
    snippet = page[start:end]
    assert "needsConfirm" in snippet, (
        "targetPickMessage() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_target_pick_message(ok, chaser_running, call):
    js = extract_target_pick_message_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(targetPickMessage(%s, %s, %s)));"
    ) % ("true" if ok else "false", "true" if chaser_running else "false", json.dumps(call))
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


class TestUnkeyCountdownLabel(unittest.TestCase):
    """'Time to unkey' while hot (cpNextTx while tx=true): unkey_deadline_epoch
    is qso.py's own watchdog fire time (boundary + WATCHDOG_S), not an
    estimate. Missing/None falls back to plain 'ON AIR' (older engine.json,
    or the brief window before the first TX of a session sets the field)."""

    def test_missing_deadline_falls_back_to_plain_on_air(self):
        self.assertEqual(run_unkey_countdown_label(None, 1000.0), "ON AIR")

    def test_counts_down_while_time_remains(self):
        self.assertEqual(run_unkey_countdown_label(1014.0, 1000.0), "ON AIR — unkey in 14.0s")

    def test_counts_down_to_fractional_seconds(self):
        self.assertEqual(run_unkey_countdown_label(1003.4, 1000.0), "ON AIR — unkey in 3.4s")

    def test_at_deadline_shows_unkey_now(self):
        self.assertEqual(run_unkey_countdown_label(1000.0, 1000.0), "ON AIR — unkey now")

    def test_past_deadline_still_shows_unkey_now(self):
        # Watchdog fired but the dashboard hasn't polled a fresh tx=false yet
        # -- must never show a negative countdown.
        self.assertEqual(run_unkey_countdown_label(999.0, 1000.0), "ON AIR — unkey now")


class TestModeLabelFor(unittest.TestCase):
    """modeLabelFor(): header 'Mode: X' text -- looks up the active mode's
    label in the already-polled registry, same data pollModeState()/
    loadModeRegistry() already fetch, no extra network call."""

    def test_no_active_mode_shows_dash(self):
        self.assertEqual(run_mode_label_for(None, {}), "—")

    def test_known_mode_shows_registry_label(self):
        self.assertEqual(run_mode_label_for("ft8", {"ft8": {"label": "FT8"}}), "FT8")

    def test_unknown_mode_falls_back_to_raw_key(self):
        self.assertEqual(run_mode_label_for("js8", {"ft8": {"label": "FT8"}}), "js8")


class TestHeaderStatusLabel(unittest.TestCase):
    """headerStatusLabel(): replaces the old static 'RX monitor' header
    text with the same live tx/chaser/rxloop signal refreshActionsState()
    already polls every 3s -- tx beats chasing beats plain receiving."""

    def test_tx_wins_over_everything(self):
        self.assertEqual(run_header_status_label(True, True, True), "Transmitting")

    def test_chaser_armed_not_yet_keyed(self):
        self.assertEqual(run_header_status_label(False, True, True), "Chasing")

    def test_rx_only(self):
        self.assertEqual(run_header_status_label(False, False, True), "Receiving")

    def test_idle_when_nothing_running(self):
        self.assertEqual(run_header_status_label(False, False, False), "Idle")


class TestBpPillsHtml(unittest.TestCase):
    """bpPillsHtml(): pure rendering for the bandpulse.net top-3-bands
    banner -- one <span class="bpPill st-<state>"> per band, state drives
    the color (green/yellow/red/gray, see #bpBanner CSS)."""

    def test_renders_one_pill_per_band(self):
        html = run_bp_pills_html([
            {"id": "40m", "name": "40 m", "state": "green", "label": "Open", "score": 88},
            {"id": "20m", "name": "20 m", "state": "yellow", "label": "Holding", "score": 60},
        ])
        self.assertEqual(html.count("bpPill"), 2)

    def test_state_drives_css_class(self):
        html = run_bp_pills_html([{"id": "40m", "name": "40 m", "state": "green", "label": "Open", "score": 88}])
        self.assertIn("st-green", html)

    def test_band_name_and_score_visible(self):
        html = run_bp_pills_html([{"id": "40m", "name": "40 m", "state": "green", "label": "Open", "score": 88}])
        self.assertIn("40 m", html)
        self.assertIn("score 88", html)

    def test_empty_list_renders_nothing(self):
        self.assertEqual(run_bp_pills_html([]), "")

    def test_hostile_band_name_is_escaped_not_rendered_as_html(self):
        # band name/state/label come from bandpulse.net's live API response --
        # a bad field must render as inert text, never break out into markup.
        html = run_bp_pills_html([
            {"id": "40m", "name": "<img src=x onerror=alert(1)>", "state": "green",
             "label": "Open", "score": 88},
        ])
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)

    def test_hostile_state_cannot_break_out_of_class_attribute(self):
        html = run_bp_pills_html([
            {"id": "40m", "name": "40 m", "state": 'green"><script>alert(1)</script>',
             "label": "Open", "score": 88},
        ])
        self.assertNotIn("<script>", html)
        self.assertNotIn('"><script>', html)

    def test_hostile_label_cannot_break_out_of_title_attribute(self):
        html = run_bp_pills_html([
            {"id": "40m", "name": "40 m", "state": "green",
             "label": '"><script>alert(1)</script>', "score": 88},
        ])
        self.assertNotIn("<script>", html)


class TestPanViewBox(unittest.TestCase):
    """Hand-rolled drag-to-pan: content follows the cursor (grab-map
    semantics, like Google Maps) -- dragging right reveals content that
    was off-screen to the left, so viewBox.x decreases."""

    def test_drag_right_decreases_x(self):
        r = run_pan_viewbox({"x": 100, "y": 50, "w": 200, "h": 100}, 50, 0, 1000, 500)
        self.assertAlmostEqual(r["x"], 90, places=3)
        self.assertEqual(r["y"], 50)

    def test_drag_down_decreases_y(self):
        r = run_pan_viewbox({"x": 100, "y": 50, "w": 200, "h": 100}, 0, 50, 1000, 500)
        self.assertAlmostEqual(r["y"], 40, places=3)

    def test_clamped_at_left_edge(self):
        r = run_pan_viewbox({"x": 5, "y": 0, "w": 200, "h": 100}, 500, 0, 1000, 500)
        self.assertEqual(r["x"], 0)

    def test_clamped_at_right_edge(self):
        r = run_pan_viewbox({"x": 795, "y": 0, "w": 200, "h": 100}, -500, 0, 1000, 500)
        self.assertEqual(r["x"], 800)  # MW(1000) - w(200)

    def test_size_unchanged(self):
        r = run_pan_viewbox({"x": 100, "y": 50, "w": 200, "h": 100}, 10, 10, 1000, 500)
        self.assertEqual(r["w"], 200)
        self.assertEqual(r["h"], 100)


class TestZoomViewBox(unittest.TestCase):
    """Hand-rolled wheel-zoom: zooms toward the cursor position (given as a
    0..1 fraction of the map's rendered box), aspect ratio always locked to
    MW/MH (2:1), clamped to [MIN_VB_W..MW] x [MIN_VB_H..MH]."""

    def test_zoom_in_centered_shrinks_and_recenters(self):
        r = run_zoom_viewbox({"x": 0, "y": 0, "w": 1000, "h": 500}, 0.5, 0.5, 0.5)
        self.assertAlmostEqual(r["w"], 500, places=3)
        self.assertAlmostEqual(r["h"], 250, places=3)
        self.assertAlmostEqual(r["x"], 250, places=3)
        self.assertAlmostEqual(r["y"], 125, places=3)

    def test_aspect_ratio_always_locked(self):
        r = run_zoom_viewbox({"x": 0, "y": 0, "w": 1000, "h": 500}, 0.3, 0.2, 0.8)
        self.assertAlmostEqual(r["w"] / r["h"], 2.0, places=3)

    def test_clamps_at_minimum_zoom_in(self):
        r = run_zoom_viewbox({"x": 400, "y": 200, "w": 120, "h": 60}, 0.1, 0.5, 0.5)
        self.assertGreaterEqual(r["w"], 110)
        self.assertGreaterEqual(r["h"], 55)

    def test_clamps_at_full_world_zoom_out(self):
        r = run_zoom_viewbox({"x": 0, "y": 0, "w": 900, "h": 450}, 5, 0.5, 0.5)
        self.assertEqual(r["w"], 1000)
        self.assertEqual(r["h"], 500)

    def test_stays_within_world_bounds_after_zoom(self):
        r = run_zoom_viewbox({"x": 900, "y": 0, "w": 100, "h": 50}, 3, 0.9, 0.5)
        self.assertGreaterEqual(r["x"], 0)
        self.assertLessEqual(r["x"] + r["w"], 1000)


class TestResolveCountryIso2(unittest.TestCase):
    """DXCC entity names (from callCountry(), dxcc_prefixes.json) don't
    always match Natural Earth's political admin-0 names 1:1 (e.g. "Puerto
    Rico" vs "United States of America") -- resolveCountryIso2() must
    gracefully return null for those rather than guessing wrong."""

    COUNTRIES = [{"name": "Finland", "admin": "Finland", "iso2": "FI"},
                 {"name": "United States of America", "admin": "United States of America", "iso2": "US"}]

    def test_matches_by_name(self):
        self.assertEqual(run_resolve_country_iso2("Finland", self.COUNTRIES), "FI")

    def test_no_match_returns_null(self):
        self.assertIsNone(run_resolve_country_iso2("Puerto Rico", self.COUNTRIES))

    def test_empty_name_returns_null(self):
        self.assertIsNone(run_resolve_country_iso2("", self.COUNTRIES))


class TestNeighborZoomBBox(unittest.TestCase):
    """Unions the target country's bbox with every neighbor's bbox (from
    the adjacency table) -- graceful null when the target's country can't
    be resolved or has no bbox, rather than throwing."""

    COUNTRIES_BY_ISO2 = {
        "FI": {"bbox": [557.3, 55.4, 587.6, 83.8]},
        "SE": {"bbox": [540.0, 60.0, 560.0, 90.0]},
        "NO": {"bbox": [520.0, 50.0, 545.0, 95.0]},
        "RU": {"bbox": [576.0, 34.1, 1000.0, 135.6]},
        "JP": {"bbox": [900.0, 130.0, 950.0, 160.0]},
    }
    ADJACENCY = {"FI": ["NO", "RU", "SE"], "JP": []}

    def test_unions_target_and_all_neighbors(self):
        r = run_neighbor_zoom_bbox("FI", self.COUNTRIES_BY_ISO2, self.ADJACENCY)
        self.assertAlmostEqual(r[0], 520.0, places=3)   # min x across FI+SE+NO+RU
        self.assertAlmostEqual(r[1], 34.1, places=3)    # min y
        self.assertAlmostEqual(r[2], 1000.0, places=3)  # max x
        self.assertAlmostEqual(r[3], 135.6, places=3)   # max y

    def test_island_nation_with_no_neighbors_returns_own_bbox(self):
        r = run_neighbor_zoom_bbox("JP", self.COUNTRIES_BY_ISO2, self.ADJACENCY)
        self.assertEqual(r, [900.0, 130.0, 950.0, 160.0])

    def test_unresolved_target_returns_null(self):
        self.assertIsNone(run_neighbor_zoom_bbox(None, self.COUNTRIES_BY_ISO2, self.ADJACENCY))
        self.assertIsNone(run_neighbor_zoom_bbox("XX", self.COUNTRIES_BY_ISO2, self.ADJACENCY))



class TestPopupScreenPos(unittest.TestCase):
    """The country card is a small popup anchored ABOVE a specific map
    point (not a dashboard-wide modal) -- converts an SVG-space point to a
    fixed-position screen coordinate via the map's current viewBox and
    on-screen rendered box, centered horizontally on the point and sitting
    just above it, clamped so it never renders off-screen."""

    RECT = {"left": 0, "top": 0, "width": 1000, "height": 500}
    VB = {"x": 0, "y": 0, "w": 1000, "h": 500}

    def test_centers_above_the_point(self):
        r = run_popup_screen_pos(self.RECT, self.VB, 500, 250, 200, 100, 10, 2000, 1000)
        self.assertAlmostEqual(r["left"], 400, places=3)   # 500 - 200/2
        self.assertAlmostEqual(r["top"], 140, places=3)    # 250 - 100 - 10
        self.assertAlmostEqual(r["anchorX"], 500, places=3)
        self.assertAlmostEqual(r["anchorY"], 250, places=3)

    def test_accounts_for_a_zoomed_in_viewbox(self):
        vb = {"x": 400, "y": 200, "w": 200, "h": 100}
        r = run_popup_screen_pos(self.RECT, vb, 500, 250, 200, 100, 10, 2000, 1000)
        # point is at the exact center of this sub-box -> still screen-center
        self.assertAlmostEqual(r["anchorX"], 500, places=3)
        self.assertAlmostEqual(r["anchorY"], 250, places=3)

    def test_clamps_left_edge(self):
        r = run_popup_screen_pos(self.RECT, self.VB, 5, 250, 200, 100, 10, 2000, 1000)
        self.assertGreaterEqual(r["left"], 4)

    def test_clamps_right_edge(self):
        r = run_popup_screen_pos(self.RECT, self.VB, 995, 250, 200, 100, 10, 1000, 1000)
        self.assertLessEqual(r["left"] + 200, 1000 - 4 + 0.001)

    def test_clamps_top_edge(self):
        r = run_popup_screen_pos(self.RECT, self.VB, 500, 2, 200, 100, 10, 2000, 1000)
        self.assertGreaterEqual(r["top"], 4)


class TestTargetPickMessage(unittest.TestCase):
    """Regression: 'Call this station' (and the candidate-chip buttons)
    write a target-request file that's only ever read from inside qso.py's
    hunt loop -- while idle, that's a silent no-op, so the UI must never
    claim it "requested" the call. When the chaser isn't running, the
    caller must be told to confirm-start Automatic CQ, not given false
    confidence that something is already in motion."""

    def test_success_while_chaser_running(self):
        r = run_target_pick_message(True, True, "W1AW")
        self.assertIn("W1AW", r["msg"])
        self.assertFalse(r["needsConfirm"])

    def test_success_while_chaser_idle_prompts_confirm(self):
        r = run_target_pick_message(True, False, "W1AW")
        self.assertIn("W1AW", r["msg"])
        self.assertTrue(r["needsConfirm"])
        self.assertNotIn("requested", r["msg"].lower())

    def test_failure_never_needs_confirm(self):
        r = run_target_pick_message(False, False, "W1AW")
        self.assertFalse(r["needsConfirm"])
        self.assertIn("failed", r["msg"].lower())

    def test_failure_while_running_is_still_a_failure(self):
        r = run_target_pick_message(False, True, "W1AW")
        self.assertFalse(r["needsConfirm"])
        self.assertIn("failed", r["msg"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
