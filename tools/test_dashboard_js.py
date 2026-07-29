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


def extract_qrz_auto_should_arm_js():
    """Slice qrzAutoShouldArmOnLoad() verbatim out of dashboard.py's rendered
    PAGE, between its declaration and the comment introducing the next
    function (qrzWidgetShowsSyncFailed)."""
    page = _dashboard_module().PAGE
    start = page.index("function qrzAutoShouldArmOnLoad(")
    end = page.index("\n// Pure predicate behind the QRZ widget", start)
    snippet = page[start:end]
    assert "storedPref!=='0'" in snippet, (
        "qrzAutoShouldArmOnLoad() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_qrz_auto_should_arm(stored_pref):
    """Evaluate the real qrzAutoShouldArmOnLoad() JS (via Node).
    stored_pref=None maps to JS null (never set in localStorage)."""
    js = extract_qrz_auto_should_arm_js()
    pref = "null" if stored_pref is None else json.dumps(stored_pref)
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(qrzAutoShouldArmOnLoad(%s)));"
    ) % pref
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_qrz_widget_sync_failed_js():
    """Slice qrzWidgetShowsSyncFailed() verbatim out of dashboard.py's
    rendered PAGE, between its declaration and the qrzAuto* state variables
    that follow it."""
    page = _dashboard_module().PAGE
    start = page.index("function qrzWidgetShowsSyncFailed(")
    end = page.index("\nlet qrzAutoArmedAt", start)
    snippet = page[start:end]
    assert "lastSyncOk===false" in snippet, (
        "qrzWidgetShowsSyncFailed() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_qrz_widget_sync_failed(last_sync_ok):
    """Evaluate the real qrzWidgetShowsSyncFailed() JS (via Node).
    last_sync_ok=None maps to JS null (never synced yet)."""
    js = extract_qrz_widget_sync_failed_js()
    val = "null" if last_sync_ok is None else json.dumps(last_sync_ok)
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(qrzWidgetShowsSyncFailed(%s)));"
    ) % val
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_decode_other_callsign_js():
    """Slice decodeOtherCallsign() verbatim out of dashboard.py's rendered
    PAGE, between its declaration and the tick() function that follows it.
    Depends only on isGrid() (defined earlier in PAGE), so the extracted
    snippet includes that too via a wider start marker isn't needed --
    isGrid() is re-declared standalone here since it's a tiny, stable pure
    function and duplicating it keeps this extractor self-contained."""
    page = _dashboard_module().PAGE
    start = page.index("function decodeOtherCallsign(")
    end = page.index("\nasync function tick(", start)
    snippet = page[start:end]
    assert "return tk[0];" in snippet, (
        "decodeOtherCallsign() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    is_grid_start = page.index("function isGrid(")
    is_grid_end = page.index("\n", is_grid_start)
    return page[is_grid_start:is_grid_end] + "\n" + snippet


def run_decode_other_callsign(msg, mycall):
    """Evaluate the real decodeOtherCallsign() JS (via Node)."""
    js = extract_decode_other_callsign_js()
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(decodeOtherCallsign(%s, %s)));"
    ) % (json.dumps(msg), json.dumps(mycall))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return json.loads(r.stdout)


def extract_freq_lock_should_arm_js():
    """Slice freqLockShouldArmOnLoad() verbatim out of dashboard.py's
    rendered PAGE, between its declaration and wireFreqLock() that follows
    it."""
    page = _dashboard_module().PAGE
    start = page.index("function freqLockShouldArmOnLoad(")
    end = page.index("\nfunction wireFreqLock(", start)
    snippet = page[start:end]
    assert "storedPref!=='0'" in snippet, (
        "freqLockShouldArmOnLoad() not found between markers -- dashboard.py "
        "layout changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_freq_lock_should_arm(stored_pref):
    """Evaluate the real freqLockShouldArmOnLoad() JS (via Node).
    stored_pref=None maps to JS null (never set in localStorage)."""
    js = extract_freq_lock_should_arm_js()
    pref = "null" if stored_pref is None else json.dumps(stored_pref)
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(freqLockShouldArmOnLoad(%s)));"
    ) % pref
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


def run_tx_is_live(e, chaser_running):
    """txIsLive() lives in the same source span extract_tx_line_helpers_js()
    already captures (declared right after txLineActive(), before
    resolveTargetGrid()), so no separate extraction marker is needed."""
    js = extract_tx_line_helpers_js()
    e_json = "null" if e is None else json.dumps(e)
    script = js + (
        "\nprocess.stdout.write(JSON.stringify(txIsLive(%s, %s)));"
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


def run_snr_floor_label(floor_db):
    """snrFloorLabel() lives in the same source span extract_snr_risk_level_js()
    already captures (between snrRiskLevel()'s declaration and loadCfg()),
    so no separate extraction marker is needed."""
    js = extract_snr_risk_level_js()
    script = js + "\nprocess.stdout.write(snrFloorLabel(%r));" % floor_db
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return r.stdout


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


def extract_terminator_path_d_js():
    """Slice the MW/MH constants, ll2xy(), and terminatorPathD() verbatim
    out of dashboard.py's rendered PAGE -- terminatorPathD() projects
    bin/astro.py's [lat,lon] night-hemisphere polygon through the map's
    real equirectangular projection into an SVG path 'd' string."""
    page = _dashboard_module().PAGE
    c_start = page.index("const MW=1000, MH=500;")
    c_end = page.index("\n", c_start)
    start = page.index("function ll2xy(")
    end = page.index("\nfunction isGrid(", start)
    snippet = page[start:end]
    assert "function terminatorPathD(" in snippet, (
        "terminatorPathD() not found between markers -- dashboard.py layout "
        "changed, update the markers in tools/test_dashboard_js.py")
    return page[c_start:c_end] + "\n" + snippet


def run_terminator_path_d(poly):
    js = extract_terminator_path_d_js()
    script = js + "\nprocess.stdout.write(terminatorPathD(%s));" % json.dumps(poly)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return r.stdout


def extract_moon_widget_html_js():
    """Slice escapeHtml()+moonWidgetHtml() verbatim out of dashboard.py's
    rendered PAGE (the Moon widget's phase/illumination/sub-lunar-point
    text, driven by /astro/state's "moon" field). escapeHtml()'s own full
    body is grabbed via its stable next-function marker (bpPillsHtml),
    same technique as extract_bp_pills_html_js() above -- reused rather
    than re-derived so both stay correct if escapeHtml() ever moves."""
    page = _dashboard_module().PAGE
    esc_start = page.index("function escapeHtml(")
    esc_end = page.index("\nfunction bpPillsHtml(", esc_start)
    start = page.index("function moonWidgetHtml(")
    end = page.index("\nfunction renderMoonMarker(", start)
    return page[esc_start:esc_end] + "\n" + page[start:end]


def run_moon_widget_html(m):
    js = extract_moon_widget_html_js()
    script = js + "\nprocess.stdout.write(moonWidgetHtml(%s));" % json.dumps(m)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("node failed: %s" % r.stderr)
    return r.stdout


def extract_mode_card_html_js():
    """Slice modeCardHtml()+renderModeChooserButtons()+modeLabelFor()+
    escapeHtml() verbatim out of dashboard.py's rendered PAGE (the mode
    chooser's explanatory cards -- label, description, protocol link, and
    an available/planned Select-vs-coming-soon footer -- driven by
    mode_registry.MODE_INFO, see bin/mode_registry.py). modeCardHtml() is
    declared before escapeHtml() in source but calls it (JS function
    declarations hoist within a scope, so this is fine in the browser);
    the slice has to include both since they're extracted out of that
    scope for this test."""
    page = _dashboard_module().PAGE
    start = page.index("function modeCardHtml(")
    end = page.index("\nfunction bpPillsHtml(", start)
    snippet = page[start:end]
    assert "function escapeHtml(" in snippet, (
        "escapeHtml() not found between markers -- dashboard.py layout "
        "changed, update the markers in tools/test_dashboard_js.py")
    return snippet


def run_mode_card_html(key, m):
    js = extract_mode_card_html_js()
    script = js + "\nprocess.stdout.write(modeCardHtml(%s, %s));" % (json.dumps(key), json.dumps(m))
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


class TestQrzAutoShouldArmOnLoad(unittest.TestCase):
    """Auto sync & upload must default ON the first time a key is confirmed
    on file (a real key sitting unsynced with nothing flagging it was the
    bug) -- but an explicit prior opt-out must stick across reloads."""

    def test_never_set_defaults_to_armed(self):
        self.assertTrue(run_qrz_auto_should_arm(None))

    def test_explicit_prior_on_stays_armed(self):
        self.assertTrue(run_qrz_auto_should_arm("1"))

    def test_explicit_prior_off_stays_disarmed(self):
        self.assertFalse(run_qrz_auto_should_arm("0"))


class TestFreqLockShouldArmOnLoad(unittest.TestCase):
    """Freq Lock must default ON (Logan's explicit feedback after trying the
    off-by-default version -- a config-panel checkbox is too easy to miss)
    but still respect an explicit prior opt-out across reloads."""

    def test_never_set_defaults_to_armed(self):
        self.assertTrue(run_freq_lock_should_arm(None))

    def test_explicit_prior_on_stays_armed(self):
        self.assertTrue(run_freq_lock_should_arm("1"))

    def test_explicit_prior_off_stays_disarmed(self):
        self.assertFalse(run_freq_lock_should_arm("0"))


class TestQrzWidgetShowsSyncFailed(unittest.TestCase):
    """Red-border flag: only an explicit completed failure (exit code != 0)
    lights it up -- never-synced-yet (null) must NOT look like a failure."""

    def test_never_synced_yet_is_not_a_failure(self):
        self.assertFalse(run_qrz_widget_sync_failed(None))

    def test_last_sync_ok_is_not_a_failure(self):
        self.assertFalse(run_qrz_widget_sync_failed(True))

    def test_last_sync_failed_shows_red_border(self):
        self.assertTrue(run_qrz_widget_sync_failed(False))


class TestDecodeOtherCallsign(unittest.TestCase):
    """Feeds the Decodes table's flag column -- must correctly pull the
    OTHER station's callsign out of CQ lines and standard exchange lines,
    regardless of which side of the exchange mycall is on."""

    MYCALL = "N0CALL"

    def test_plain_cq_with_grid(self):
        self.assertEqual(run_decode_other_callsign("CQ K1ABC FN20", self.MYCALL), "K1ABC")

    def test_cq_with_qualifier_and_grid(self):
        self.assertEqual(run_decode_other_callsign("CQ DX K1ABC FN20", self.MYCALL), "K1ABC")
        self.assertEqual(run_decode_other_callsign("CQ POTA K1ABC FN20", self.MYCALL), "K1ABC")

    def test_cq_without_grid(self):
        self.assertEqual(run_decode_other_callsign("CQ K1ABC", self.MYCALL), "K1ABC")

    def test_bare_cq_returns_none(self):
        self.assertIsNone(run_decode_other_callsign("CQ", self.MYCALL))

    def test_exchange_where_mycall_is_first_token(self):
        self.assertEqual(run_decode_other_callsign("N0CALL K1ABC -12", self.MYCALL), "K1ABC")

    def test_exchange_where_mycall_is_second_token(self):
        self.assertEqual(run_decode_other_callsign("K1ABC N0CALL FN20", self.MYCALL), "K1ABC")

    def test_exchange_report_and_rr73_variants(self):
        self.assertEqual(run_decode_other_callsign("K1ABC N0CALL R-09", self.MYCALL), "K1ABC")
        self.assertEqual(run_decode_other_callsign("K1ABC N0CALL RR73", self.MYCALL), "K1ABC")
        self.assertEqual(run_decode_other_callsign("K1ABC N0CALL 73", self.MYCALL), "K1ABC")

    def test_third_party_exchange_falls_back_to_first_token(self):
        self.assertEqual(run_decode_other_callsign("W1AW K1ABC RR73", self.MYCALL), "W1AW")

    def test_empty_message_returns_none(self):
        self.assertIsNone(run_decode_other_callsign("", self.MYCALL))

    def test_single_token_non_cq_returns_none(self):
        self.assertIsNone(run_decode_other_callsign("garbled", self.MYCALL))


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


class TestTxIsLive(unittest.TestCase):
    """Drives the cockpit's 'ON AIR' pulse/countdown, the STOP button's
    live-glow, and (via the same principle applied in refreshActionsState())
    the page-wide TX siren. engine.json's tx field is a snapshot qso.py
    never resets on an abnormal exit -- a killed/crashed run can leave
    tx:true on disk forever, which without this chaserRunning guard means
    STOP can never visibly clear the "ON AIR -- unkey now" state (STOP's
    real job, force-unkeying the physical rig, already happened; only the
    stale display was left behind)."""

    def test_tx_true_with_chaser_running_is_live(self):
        self.assertTrue(run_tx_is_live({"tx": True}, True))

    def test_stale_tx_true_while_chaser_not_running_is_not_live(self):
        self.assertFalse(run_tx_is_live({"tx": True}, False))

    def test_tx_false_with_chaser_running_is_not_live(self):
        self.assertFalse(run_tx_is_live({"tx": False}, True))

    def test_null_engine_is_not_live(self):
        self.assertFalse(run_tx_is_live(None, True))


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


class TestSnrFloorLabel(unittest.TestCase):
    """snrFloorLabel(): one distinct description per dB across the
    slider's full -30..+10 range -- every step reads as fresh, specific
    wording rather than repeating a handful of risk buckets."""

    def test_every_step_in_slider_range_has_a_label(self):
        seen = set()
        for db in range(-30, 11):
            label = run_snr_floor_label(db)
            self.assertTrue(label, db)
            seen.add(label)
        # 41 distinct dB values -> 41 distinct labels, no two steps sharing text.
        self.assertEqual(len(seen), 41)

    def test_station_default_label(self):
        self.assertIn("Marginal", run_snr_floor_label(-16))

    def test_dx_mode_floor_label_mentions_dx(self):
        self.assertIn("DX", run_snr_floor_label(-18))

    def test_strongest_end_reads_positive(self):
        label = run_snr_floor_label(10).lower()
        self.assertTrue("strong" in label or "boom" in label)

    def test_weakest_end_reads_extreme(self):
        label = run_snr_floor_label(-30).lower()
        self.assertTrue("edge" in label or "buried" in label or "fringe" in label)

    def test_clamps_beyond_slider_range(self):
        self.assertEqual(run_snr_floor_label(-30), run_snr_floor_label(-99))
        self.assertEqual(run_snr_floor_label(10), run_snr_floor_label(99))

    def test_rounds_non_integer_input(self):
        self.assertEqual(run_snr_floor_label(-16), run_snr_floor_label(-16.4))


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


class TestTerminatorPathD(unittest.TestCase):
    """terminatorPathD(): projects bin/astro.py's [lat,lon] night-hemisphere
    polygon through the map's real ll2xy() equirectangular projection into
    an SVG path 'd' string."""

    def test_empty_polygon_renders_empty_string(self):
        self.assertEqual(run_terminator_path_d([]), "")

    def test_starts_with_move_and_ends_with_close(self):
        d = run_terminator_path_d([[0, -180], [10, 0], [0, 180]])
        self.assertTrue(d.startswith("M"))
        self.assertTrue(d.rstrip().endswith("Z"))

    def test_point_count_matches_input(self):
        poly = [[0, -180], [45, -90], [0, 0], [-45, 90], [0, 180]]
        d = run_terminator_path_d(poly)
        # One M + (n-1) L commands for n points.
        self.assertEqual(d.count("L"), len(poly) - 1)

    def test_known_point_projects_to_expected_pixel(self):
        # lat=0,lon=-180 (map's far west edge, vertical center) -> x=0, y=MH/2=250.
        d = run_terminator_path_d([[0, -180]])
        self.assertTrue(d.startswith("M0.00 250.00"))

    def test_north_pole_projects_to_top_edge(self):
        d = run_terminator_path_d([[90, 0]])
        self.assertTrue(d.startswith("M500.00 0.00"))


class TestMoonWidgetHtml(unittest.TestCase):
    """moonWidgetHtml(): pure rendering for the Moon widget's phase name,
    illuminated percentage, age, and sub-lunar point text."""

    SAMPLE = {"lat": -12.3, "lon": 45.6, "illuminated_fraction": 0.796,
              "elongation_deg": 126.3, "phase_name": "Waxing Gibbous", "age_days": 10.36}

    def test_no_data_renders_placeholder(self):
        self.assertEqual(run_moon_widget_html(None), "no data")

    def test_phase_name_and_percentage_visible(self):
        html = run_moon_widget_html(self.SAMPLE)
        self.assertIn("Waxing Gibbous", html)
        self.assertIn("80% illuminated", html)

    def test_age_and_subpoint_visible(self):
        html = run_moon_widget_html(self.SAMPLE)
        self.assertIn("10.4d since new moon", html)
        self.assertIn("-12.3", html)
        self.assertIn("45.6", html)

    def test_hostile_phase_name_is_escaped_not_rendered_as_html(self):
        hostile = dict(self.SAMPLE, phase_name="<img src=x onerror=alert(1)>")
        html = run_moon_widget_html(hostile)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)


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


class TestModeCardHtml(unittest.TestCase):
    """modeCardHtml(): one explanatory card per mode_registry.MODE_INFO
    entry in the boot/mode chooser -- label, description, a link to the
    protocol's own reference page, and a Select button (available) or a
    muted "coming soon" tag (planned, e.g. FT4/JS8/Winlink) instead."""

    AVAILABLE = {"label": "FT8", "status": "available",
                 "description": "Weak-signal digital mode.",
                 "protocol_url": "https://wsjt.sourceforge.io/wsjtx.html"}
    PLANNED = {"label": "JS8", "status": "planned",
               "description": "Adds free-text keyboard-to-keyboard messaging.",
               "protocol_url": "http://js8call.com/"}

    def test_available_mode_gets_select_button(self):
        html = run_mode_card_html("ft8", self.AVAILABLE)
        self.assertIn('data-mode="ft8"', html)
        self.assertIn("Select FT8", html)

    def test_available_mode_has_no_planned_badge_or_soon_tag(self):
        html = run_mode_card_html("ft8", self.AVAILABLE)
        self.assertNotIn("modeCardBadge", html)
        self.assertNotIn("coming soon", html)
        self.assertNotIn("planned", html)  # not even in the card's class list

    IN_DEVELOPMENT = {"label": "JS8", "status": "in-development",
                      "description": "Native JS8 modem, built in SeeQ.",
                      "protocol_url": "http://js8call.com/"}

    def test_planned_mode_has_no_select_button(self):
        html = run_mode_card_html("js8", self.PLANNED)
        self.assertNotIn("data-mode=", html)
        self.assertIn("coming soon", html)
        self.assertIn("modeCardBadge", html)

    def test_in_development_mode_is_labelled_in_development(self):
        html = run_mode_card_html("js8", self.IN_DEVELOPMENT)
        self.assertIn("In Development", html)

    def test_in_development_mode_is_not_selectable(self):
        """Not switchable until Logan has exercised it and released v4.0.0."""
        html = run_mode_card_html("js8", self.IN_DEVELOPMENT)
        self.assertNotIn("data-mode=", html)
        self.assertNotIn("Select JS8", html)

    def test_in_development_is_distinguishable_from_planned(self):
        """"Nearly there" must not read the same as "not started"."""
        dev = run_mode_card_html("js8", self.IN_DEVELOPMENT)
        planned = run_mode_card_html("js8", self.PLANNED)
        self.assertNotIn("coming soon", dev)
        self.assertNotIn("In Development", planned)

    def test_description_and_protocol_link_present(self):
        html = run_mode_card_html("ft8", self.AVAILABLE)
        self.assertIn("Weak-signal digital mode.", html)
        self.assertIn('href="https://wsjt.sourceforge.io/wsjtx.html"', html)
        self.assertIn("target=_blank", html)

    def test_hostile_description_is_escaped_not_rendered_as_html(self):
        # MODE_INFO is server-authored today, not user input -- but
        # escapeHtml() is cheap and this keeps the rendering path safe if
        # that ever changes (e.g. a future mode's info sourced externally).
        hostile = dict(self.AVAILABLE, description="<img src=x onerror=alert(1)>")
        html = run_mode_card_html("ft8", hostile)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)

    def test_hostile_protocol_url_cannot_break_out_of_href_attribute(self):
        hostile = dict(self.AVAILABLE, protocol_url='javascript:alert(1)"><script>alert(2)</script>')
        html = run_mode_card_html("ft8", hostile)
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


def extract_should_show_chooser_js():
    page = _dashboard_module().PAGE
    start = page.index("function shouldShowChooser(activeMode, forced, inflight){")
    end = page.index("\n}", start) + 2
    return page[start:end]


def run_should_show_chooser(cases):
    """Evaluate the real shouldShowChooser() for a list of
    [activeMode, forced, inflight] triples."""
    script = extract_should_show_chooser_js() + (
        "\nconst __c = %s;"
        "\nprocess.stdout.write(JSON.stringify(__c.map(a => shouldShowChooser(a[0],a[1],a[2]))));"
    ) % json.dumps(cases)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise AssertionError(f"node failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


class TestShouldShowChooser(unittest.TestCase):
    """Mode chooser visibility. As first shipped this was just
    `activeMode ? hide : show`, which meant that once a mode was active there
    was no way to reach the chooser again -- switching mode required
    restarting the dashboard. These pin the corrected rules."""

    def test_boot_with_no_mode_always_shows(self):
        # Ground rule: never silently default into a mode.
        self.assertEqual(run_should_show_chooser([[None, False, False]]), [True])

    def test_active_mode_hides_it_by_default(self):
        self.assertEqual(run_should_show_chooser([["ft8", False, False]]), [False])

    def test_user_can_reopen_it_while_a_mode_is_active(self):
        # The header mode button sets `forced`. This is the regression the
        # whole change exists for.
        self.assertEqual(run_should_show_chooser([["ft8", True, False]]), [True])

    def test_stays_open_through_a_changeover(self):
        # The switch is a deliberate 30-45s sequence; its staged progress is
        # the only feedback, so the overlay must not vanish mid-way.
        self.assertEqual(run_should_show_chooser([["ft8", False, True]]), [True])

    def test_boot_cannot_be_dismissed(self):
        # forced=false + no active mode must still show: there is nothing to
        # fall back to, so a dismissable boot chooser would strand the UI.
        self.assertEqual(run_should_show_chooser([[None, False, False],
                                                   [None, True, False]]), [True, True])


def extract_chooser_flags_js():
    page = _dashboard_module().PAGE
    start = page.index("function chooserFlagsAfterPoll(stage, forced, inflight){")
    end = page.index("\n}", start) + 2
    return page[start:end]


def run_chooser_flags(cases):
    script = extract_chooser_flags_js() + (
        "\nconst __c = %s;"
        "\nprocess.stdout.write(JSON.stringify("
        "__c.map(a => chooserFlagsAfterPoll(a[0],a[1],a[2]))));"
    ) % json.dumps(cases)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise AssertionError(f"node failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


class TestChooserFlagsAfterPoll(unittest.TestCase):
    """What a /mode/state poll is allowed to do to the chooser's flags.

    data/mode-switch.json persists after a changeover completes, so every poll
    keeps seeing stage 'done' indefinitely. The first version cleared the
    user-opened flag on any 'done', which meant clicking the header's switch
    button set the flag and the very next poll wiped it -- the button was
    visible and did nothing. A completed switch may only close the chooser if
    THIS page actually started it.
    """

    def test_stale_done_does_not_slam_the_chooser_shut(self):
        # forced=true, inflight=false: the user just opened it by hand and the
        # 'done' on disk is left over from an earlier switch.
        self.assertEqual(run_chooser_flags([["done", True, False]]), [[True, False]])

    def test_our_own_completed_switch_closes_it(self):
        self.assertEqual(run_chooser_flags([["done", True, True]]), [[False, False]])

    def test_already_active_behaves_like_done(self):
        self.assertEqual(run_chooser_flags([["already_active", True, True]]), [[False, False]])
        self.assertEqual(run_chooser_flags([["already_active", True, False]]), [[True, False]])

    def test_error_stops_inflight_but_keeps_the_chooser_open(self):
        # The operator needs to read the failure and decide what to do.
        self.assertEqual(run_chooser_flags([["error", True, True]]), [[True, False]])

    def test_in_progress_stages_change_nothing(self):
        for stage in ("stopping", "verifying", "sanity_check", "starting"):
            self.assertEqual(run_chooser_flags([[stage, True, True]]), [[True, True]], stage)

    def test_null_stage_changes_nothing(self):
        self.assertEqual(run_chooser_flags([[None, True, False]]), [[True, False]])


def extract_mode_visibility_js():
    """The real applyModeVisibility() source, verbatim from PAGE."""
    page = _dashboard_module().PAGE
    start = page.index("function applyModeVisibility(activeMode){")
    end = page.index("\n}", start) + 2
    return page[start:end]


def run_mode_visibility(active_mode, widgets):
    """Run the real applyModeVisibility() against a tiny fake DOM.

    `widgets` is a list of (data-mode or None) values; returns the resulting
    display value for each, in order. A hand-rolled stub DOM keeps this a
    dependency-free `node -e` run like the rest of this file.
    """
    js = extract_mode_visibility_js()
    script = """
const WIDGETS = %s.map(m => ({dataset: m === null ? {} : {mode: m}, style: {display: ''}}));
const document = {
  querySelectorAll(sel) {
    if (sel !== '#dash .widget[data-mode]') throw new Error('unexpected selector: ' + sel);
    // Only elements that actually carry data-mode match the selector.
    return WIDGETS.filter(w => w.dataset.mode !== undefined);
  }
};
%s
applyModeVisibility(%s);
process.stdout.write(JSON.stringify(WIDGETS.map(w => w.style.display)));
""" % (json.dumps(widgets), js, json.dumps(active_mode))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise AssertionError(f"node failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


class TestApplyModeVisibility(unittest.TestCase):
    """Mode-scoped widget visibility (M1). Getting this wrong either shows
    both modes' panels at once or hides the active one, so it's worth running
    the real function rather than trusting it by inspection."""

    def test_js8_mode_hides_ft8_widgets_and_shows_js8_ones(self):
        out = run_mode_visibility("js8", ["ft8", "js8", None])
        self.assertEqual(out[0], "none")   # ft8 widget hidden
        self.assertEqual(out[1], "")       # js8 widget shown
        self.assertEqual(out[2], "")       # untagged shared chrome untouched

    def test_ft8_mode_is_the_mirror_image(self):
        out = run_mode_visibility("ft8", ["ft8", "js8", None])
        self.assertEqual(out[0], "")
        self.assertEqual(out[1], "none")
        self.assertEqual(out[2], "")

    def test_shared_chrome_is_never_hidden_in_any_mode(self):
        for mode in ("ft8", "js8", None):
            out = run_mode_visibility(mode, [None, None])
            self.assertEqual(out, ["", ""], mode)

    def test_no_active_mode_hides_nothing(self):
        # Before a mode is chosen the chooser overlay covers the page anyway;
        # hiding widgets underneath it would just cause a flash of relayout
        # when the overlay clears.
        out = run_mode_visibility(None, ["ft8", "js8", None])
        self.assertEqual(out, ["", "", ""])


if __name__ == "__main__":
    unittest.main(verbosity=2)
