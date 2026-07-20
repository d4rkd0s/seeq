#!/usr/bin/env python3
"""Claude-on-AIR dashboard — stdlib only.
Usage: dashboard.py [port]   (port defaults to HTTP_PORT in station.conf, then 8074)

Mostly read-only display. A small set of local-only control endpoints (Actions
widget) let the OPERATOR start/stop RX, start/stop the chaser, request a target/
skip, and hit STOP+UNKEY. Every action is logged to data/actions.log. Set
COA_DRYRUN=1 to log intended commands without executing them (used for testing).
"""
import http.server, json, os, re, socketserver, subprocess, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adif
import dxcc
import logbook
import station_config
import world_map                      # embedded coastline path (no network at runtime)
import country_borders                # embedded country outlines (Natural Earth 50m admin-0)
import state_borders                  # embedded state/province outlines (Natural Earth 50m admin-1)
import country_adjacency              # ISO2 -> neighboring ISO2s (geodatasource/country-borders)
import logsync                        # QRZ Logbook status (read-only here) + sync subprocess
import qrz_xml_api                    # QRZ XML (callsign/bio/photo) lookup -- separate subscription+auth from Logbook

_C = station_config.load()
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.expanduser(_C.get("DATA", os.path.join(_ROOT, "data")))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(_C.get("HTTP_PORT", 8074))
MYCALL = _C.get("MYCALL", "N0CALL")
MYGRID = _C.get("MYGRID", "AA00")
CHASELOG = os.path.join(DATA, "chase.log")
ACTIONS_LOG = os.path.join(DATA, "actions.log")
LAYOUT_JSON = os.path.join(DATA, "ui-layout.json")
TARGET_REQ = os.path.join(DATA, "target-request.json")
SKIP_REQ = os.path.join(DATA, "skip-request.json")
SNR_FLOOR_REQ = os.path.join(DATA, "snr-floor-request.json")
ANTENNAS_JSON = os.path.join(DATA, "antennas.json")
EVENT_LINES = 20
MAX_POST_BODY = 65536

# General-class HF data sub-bands (CLAUDE.md's own table) mapped to the
# community-standard FT8 calling frequency for each — band/freq selection in
# the dashboard is LOCKED to this list (no free-form Hz entry). 60 m excluded
# on purpose: it's channelized with its own mode/power rules that get revised
# more often than the rest of the band plan (see skills/antenna-atu.md) — pick
# it by hand and edit station.conf directly rather than trusting a baked-in
# channel list here.
BANDS = {
    "160m": {"freq_hz": 1840000,  "cap_w": None},
    "80m":  {"freq_hz": 3573000,  "cap_w": None},
    "40m":  {"freq_hz": 7074000,  "cap_w": None},
    "30m":  {"freq_hz": 10136000, "cap_w": 200},   # §97.313: 200 W PEP cap, all classes, no exceptions
    "20m":  {"freq_hz": 14074000, "cap_w": None},
    "17m":  {"freq_hz": 18100000, "cap_w": None},
    "15m":  {"freq_hz": 21074000, "cap_w": None},
    "12m":  {"freq_hz": 24915000, "cap_w": None},
    "10m":  {"freq_hz": 28074000, "cap_w": None},
}
ABS_MAX_W = 1500      # §97.313 General-class PEP ceiling — sanity backstop only
DEFAULT_MAX_W = 5      # conservative cap for an antenna with no confirmed RF-exposure-verified max

DRYRUN = os.environ.get("COA_DRYRUN", "") not in ("", "0", "false", "False")
QSO_PY = os.path.join(_BIN, "qso.py")
RXLOOP_SH = os.path.join(_BIN, "rx-loop.sh")
LOGSYNC_PY = os.path.join(_BIN, "logsync.py")
QRZ_FETCH_PY = os.path.join(_BIN, "qrz_fetch.py")
QRZ_SYNC_LOG = os.path.join(DATA, "qrz-sync.log")
QRZ_CACHE = os.path.join(DATA, "qrz-logbook.json")
RIG_MODEL = _C.get("RIG_MODEL", "3060")
CAT_PORT = _C.get("CAT_PORT", "/dev/ttyUSB0")
CAT_BAUD = _C.get("CAT_BAUD", "19200")

CONFIG = {"mycall": MYCALL, "mygrid": MYGRID, "band": _C.get("BAND", ""),
          "dial_hz": int(_C.get("DIAL_HZ", "0") or 0),
          "tx_pwr": _C.get("TX_PWR", ""), "mode": "FT8",
          "antenna": _C.get("ANTENNA", ""),
          "max_repeat": int(_C.get("MAX_REPEAT", 6)),
          "snr_floor_default": int(_C.get("SNR_FLOOR", -16))}

# Pre-encoded once at import time (349KB/261KB) rather than on every request --
# these are only fetched lazily by the map widget's JS, never inlined into PAGE.
COUNTRY_BORDERS_JSON = json.dumps(country_borders.COUNTRIES, separators=(",", ":")).encode()
STATE_BORDERS_JSON = json.dumps(state_borders.STATES, separators=(",", ":")).encode()
COUNTRY_ADJACENCY_JSON = json.dumps(country_adjacency.ADJACENCY, separators=(",", ":")).encode()


def _load_dish_flower():
    """ISO2 -> {"dish":..., "flower":...} (either key may be absent when
    unknown) for the country info card. Hand/research-curated (no free geo
    dataset has this), not part of the Natural Earth conversion pipeline --
    fails open to {} if the file doesn't exist yet, same convention as
    every other embedded-data loader in this app."""
    try:
        with open(os.path.join(_BIN, "country_dish_flower.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


DISH_FLOWER_JSON = json.dumps(_load_dish_flower(), separators=(",", ":")).encode()

FLAGS_DIR = os.path.join(_BIN, "flags")
_FLAG_CODE_RE = re.compile(r"^[a-z]{2}$")  # matches flag-icons' iso2-lowercase.svg naming

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>COTA — __MYCALL__</title>
<style>
 body{background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif;margin:0;padding:14px}
 h1{font-size:18px;margin:0 0 10px;color:#58a6ff} h1 small{color:#8b949e;font-weight:normal}
 img{max-width:100%;border-radius:4px;background:#000}
 table{border-collapse:collapse;width:100%;font-size:13px;font-family:ui-monospace,monospace}
 td,th{padding:2px 8px;text-align:left;border-bottom:1px solid #21262d;white-space:nowrap}
 th{color:#8b949e;font-weight:600}
 .cq{color:#3fb950;font-weight:600} .me{color:#f85149;font-weight:700;background:#2d1214}
 .next{font-size:15px} .next .callchip-main{font-size:21px;padding:6px 14px}
 .dim{color:#8b949e;font-size:12px} .snr-good{color:#3fb950}.snr-bad{color:#8b949e}
 #stale{display:none;color:#f85149;font-weight:700}
 #events{font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;
  overflow-x:auto;max-height:100%;overflow-y:auto;margin:0;color:#d2a8ff}
 #events .tx{color:#f0883e;font-weight:600} #events .good{color:#3fb950;font-weight:600}
 #events .bad{color:#f85149;font-weight:600}
 /* ---- skip: a graceful "moving on" decision (SNR floor, busy-hold,
    no-response-after-N-tries) -- distinct from .bad's real operational
    failures. Reuses the existing "hunting"/QRZ-"uploaded" blue. ---- */
 #events .skip{color:#56d4dd}
 /* ---- dx: DX-Mode-specific lines (armed-session banner, DX-filter skips,
    new-country priority decisions) -- matches the DX-armed page-glow blue. ---- */
 #events .dx{color:#58a6ff;font-weight:600}
 /* ---- unknownctry: a DX-filter skip caused by an UNRESOLVED prefix, not a
    legitimate same-country match -- these are dxcc_prefixes.json gaps
    worth closing, so they get their own loud color, distinct from the
    routine blue .dx skips. ---- */
 #events .unknownctry{color:#ff2ecc;font-weight:700}
 /* ---- info: session start/stop, breathers, housekeeping -- this app's
    existing #events base color (#d2a8ff) was already this category's de
    facto color via the no-class fallback; made explicit here rather than
    left implicit. ---- */
 #events .info{color:#d2a8ff}
 #map{width:100%;display:block;background:#0d1117;border-radius:4px;cursor:grab;touch-action:none}
 .mlabel{font-size:calc(11px * var(--map-scale, 1));font-family:ui-monospace,monospace;font-weight:600}
 /* ---- every CONTACT dot type is clickable (opens the country info card +
    locks the map zoom onto it) -- cursor + hover-grow signal that
    uniformly. home is the operator's own station, not a contact, so it
    stays non-interactive. ---- */
 #map .dot-rx,#map .dot-tx,#map .dot-qso{cursor:pointer}
 #map .dot-rx:hover,#map .dot-tx:hover,#map .dot-qso:hover{r:calc(6.5px * var(--map-scale, 1))}
 #map .dot-rx{r:calc(3.4px * var(--map-scale, 1))}
 #map .dot-home{r:calc(5px * var(--map-scale, 1))}
 #map .dot-tx{r:calc(5px * var(--map-scale, 1))}
 #map .dot-qso{r:calc(4.6px * var(--map-scale, 1))}
 /* ---- country/state border lines: subdued so the RX/QSO/TX overlays
    (the actual point of the map) stay the visual focus. Country lines a
    shade brighter than state lines -- secondary detail, visible mainly
    once zoomed in. vector-effect keeps stroke width constant across zoom. ---- */
 #countryBorders path{fill:none;stroke:#3d4a5c;stroke-width:0.5;vector-effect:non-scaling-stroke}
 #stateBorders path{fill:none;stroke:#2a323d;stroke-width:0.35;vector-effect:non-scaling-stroke}
 .txflow{animation:flow 1s linear infinite}
 @keyframes flow{to{stroke-dashoffset:-17}}
 @keyframes pulse{50%{opacity:.35}}
 .infobar{display:flex;gap:30px;flex-wrap:wrap;align-items:baseline}
 .infobar .it{display:flex;gap:8px;align-items:baseline}
 .infobar .k{color:#8b949e;font-size:11px;letter-spacing:.08em}
 .infobar .v{font-family:ui-monospace,monospace;font-size:15px;color:#c9d1d9;font-weight:600}

 /* ---- cockpit (always visible, glanceable from across the room) ---- */
 #cockpit{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:26px;
  background:#0d1117;padding:6px 2px 14px;flex-wrap:wrap;border-bottom:1px solid #21262d;margin-bottom:12px}
 #cockpit .cpitem{display:flex;flex-direction:column;gap:2px}
 #cockpit .cpk{font-size:10px;letter-spacing:.12em;color:#8b949e}
 #cockpit .cpv{font-size:28px;font-weight:800;font-family:ui-monospace,monospace;line-height:1.1}
 /* ---- red means ONE thing everywhere in this UI: literally keyed, on air,
    right now (tx===true). Anything short of that (calling, mid-QSO, armed)
    is orange -- "active" but not hot. Whole-page background follows the
    same rule (body.tx-live only), no separate "pursuing" tint. ---- */
 #cpState.st-tx,#cpState.tx-live{color:#f85149;animation:pulse 1s ease-in-out infinite}
 #cpState.st-calling{color:#f0883e} #cpState.st-qso{color:#3fb950}
 #cpState.st-hunting{color:#56d4dd}
 #cpState.st-breather,#cpState.st-idle,#cpState.st-init,#cpState.st-{color:#8b949e}
 #cpCalling{font-size:16px;color:#f0883e}
 #cpCalling.tx-live{color:#f85149;animation:pulse .6s ease-in-out infinite}
 #cpQsoStep{font-size:16px;color:#8b949e}
 #cpQsoStep.active{color:#f0883e}
 #cpNext{color:#3fb950}
 /* ---- NEXT TX cockpit countdown: idle / counting-down / on-air / aborted ---- */
 #cpNextTx{color:#8b949e}
 #cpNextTx.tx-soon{color:#f0883e}
 #cpNextTx.tx-live{color:#f85149;animation:pulse .6s ease-in-out infinite}
 #cpNextTx.tx-abort{color:#f85149}
 #cpNextTx.tx-rough{color:#6e7681}
 #cockpit .spacer{flex:1}
 /* ---- STOP+UNKEY: neutral outline at rest (this is a control, not an alarm);
    full red + a layered "siren" glow/ring animation ONLY while e.tx===true.
    Always clickable regardless of visual state — see wireActions(). ---- */
 #btnUnkey{position:relative;background:#21262d;color:#f85149;border:2px solid #f85149;
  border-radius:6px;font-size:17px;font-weight:800;padding:14px 22px;cursor:pointer;
  letter-spacing:.03em;transition:background .15s,color .15s}
 #btnUnkey:hover{background:#2d1214} #btnUnkey:active{background:#3d1a16}
 #btnTune30{position:relative;background:#21262d;color:#58a6ff;border:2px solid #1f6feb;
  border-radius:6px;font-size:15px;font-weight:800;padding:14px 18px;cursor:pointer;
  letter-spacing:.03em;transition:background .15s,color .15s}
 #btnTune30:hover{background:#0d2650} #btnTune30:active{background:#123166}
 #btnTune30:disabled{opacity:.6;cursor:default}
 /* ---- DX Mode toggle (cockpit, between TUNE and STOP): a compact chip
    matching its neighbors' height/weight. Green (#3fb950, this app's
    existing "confirmed/good" color -- see .callchip-main, lb-confirmed)
    rather than TUNE's blue, so the two controls read as visually distinct
    even though the DX-armed PAGE GLOW itself stays blue (#1f6feb,
    unrelated choice, unchanged) -- the toggle's own color and the glow's
    color are allowed to differ, they answer different questions ("is DX
    Mode armed" vs "is DX Mode active on the tone I'm choosing to render").
    A real flip-switch, not a checkbox+label: the native <input> stays
    functionally in place (opacity:0, full-size, on top) for click/keyboard/
    a11y, purely visually replaced by .dxSwitchTrack's thumb via the
    adjacent-sibling :checked selector. ---- */
 #dxToggleWrap{display:flex;align-items:center;gap:8px;background:#21262d;
  border:2px solid #3fb950;border-radius:6px;padding:12px 14px;color:#3fb950;
  font-size:13px;font-weight:700;letter-spacing:.03em;cursor:pointer}
 .dxSwitch{position:relative;display:inline-block;width:34px;height:18px;flex:0 0 auto}
 .dxSwitch input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer;z-index:1}
 .dxSwitchTrack{position:absolute;inset:0;background:#30363d;border-radius:10px;
  transition:background .15s}
 .dxSwitchTrack::before{content:'';position:absolute;left:2px;top:2px;width:14px;height:14px;
  background:#8b949e;border-radius:50%;transition:transform .15s,background .15s}
 .dxSwitch input:checked+.dxSwitchTrack{background:#173a20}
 .dxSwitch input:checked+.dxSwitchTrack::before{transform:translateX(16px);background:#3fb950}
 /* ---- help (i) icon: rightmost cockpit element, always visible (cockpit
    is position:sticky) -- satisfies "upper right of the page". ---- */
 #btnInfo{position:relative;background:#21262d;color:#8b949e;border:1px solid #30363d;
  border-radius:50%;width:34px;height:34px;font-size:15px;font-weight:800;cursor:pointer;
  display:flex;align-items:center;justify-content:center;padding:0}
 #btnInfo:hover{color:#58a6ff;border-color:#58a6ff}
 #btnUnkey.live{background:#f85149;color:#fff;border-color:#f85149;
  animation:sirenGlow 1s ease-in-out infinite}
 #btnUnkey.live::before,#btnUnkey.live::after{content:'';position:absolute;inset:-3px;
  border-radius:9px;border:2px solid #f85149;opacity:0;pointer-events:none;
  animation:sirenRing 1.3s ease-out infinite}
 #btnUnkey.live::after{animation-delay:.55s}
 @keyframes sirenGlow{0%,100%{box-shadow:0 0 6px 2px rgba(248,81,73,.5)}50%{box-shadow:0 0 24px 9px rgba(248,81,73,.9)}}
 @keyframes sirenRing{0%{transform:scale(1);opacity:.75}100%{transform:scale(1.7);opacity:0}}
 /* ---- TX-capable markers: three tiers, so a glance answers "can this
    transmit" vs "is this armed" vs "is this transmitting right now":
    1) .tx-capable — static red outline, permanent property of any control
       whose click can eventually lead to a real key-up (Chase button).
    2) .armed — chaser process alive: a transmission could happen any
       moment once a CQ is found. Steady red widget border.
    3) .armed.live — engine tx===true, actually keyed this instant: upgrades
       to the same pulsing siren glow as STOP+UNKEY. ---- */
 .tx-capable{border-color:#f85149!important;box-shadow:0 0 0 1px rgba(248,81,73,.35)}
 .widget[data-key=actions].armed{border-color:#f85149;box-shadow:0 0 0 1px rgba(248,81,73,.35);
  transition:border-color .2s,box-shadow .2s}
 .widget[data-key=actions].armed.live{animation:sirenGlow 1s ease-in-out infinite}
 #stChaser.armed{color:#f85149;font-weight:700}
 #stRx.tx-live{color:#f85149;font-weight:700;animation:pulse .6s ease-in-out infinite}
 #stRxLabel{color:inherit}
 /* ---- whole-page "ON AIR" indicator: impossible to miss from across the
    room, not just a widget detail. A fixed full-viewport glow layer (so it
    isn't clipped by scrolling content) plus a background tint on <body>
    itself. Toggled by refreshActionsState() off the same j.ptt used
    everywhere else -- one source of truth for "are we keyed right now". ---- */
 body.tx-live{background:#1a0605}
 body.tx-live::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:9998;
  box-shadow:inset 0 0 10vw 2vw rgba(248,81,73,.65);animation:pageGlow 1s ease-in-out infinite}
 @keyframes pageGlow{0%,100%{box-shadow:inset 0 0 8vw 1.5vw rgba(248,81,73,.45)}
  50%{box-shadow:inset 0 0 14vw 3vw rgba(248,81,73,.85)}}
 /* ---- DX Mode armed: separate fixed layer + z-index from tx-live's red
    layer (z-index 9998) so both can coexist at all times -- neither class
    ever toggles the other off, pure z-index layering decides who paints on
    top. TX-live ALWAYS wins when both are active (its z-index is higher).
    Blue at red's base geometry (10vw blur / 2vw spread) but ~25% of its
    base alpha (.65 * .25 = .1625) -- and deliberately not animated: DX-armed
    is a standing-readiness state (like .armed's steady border), pulsing is
    reserved for actual TX (tx-live / .armed.live), matching that existing
    tiering. Driven purely by /actions/state's dx_mode field (the RUNNING
    chaser's real state), same as tx-live is driven by j.ptt -- see
    refreshActionsState(). ---- */
 body.dx-armed{background:#0d1420}
 body.dx-armed::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:9997;
  box-shadow:inset 0 0 10vw 2vw rgba(31,111,235,.1625)}
 /* ---- New country flash (DX Mode only): a one-shot, finite yellow pulse --
    the first non-looping page animation in this file (tx-live/dx-armed/
    sirenGlow/pageGlow/flow/pulse are all `infinite`). It needs its own DOM
    element rather than a third body::before/::after layer -- an element can
    only ever have one ::before and one ::after, both already spoken for.
    Amber (#e3b341/rgba(227,179,65,*)) reuses this app's existing
    "attention" accent (see #dryrunBanner) instead of inventing a fourth
    hue alongside tx-live's red and dx-armed's blue. z-index 10500:
    unambiguously above tx-live (9998), dx-armed (9997), and the DX-confirm
    modal (.modalOverlay, 9999) -- with headroom above 10000 too, since a
    separate in-flight (uncommitted) UI batch uses #helpModal{z-index:
    10000}; this leaves both efforts room to reconcile later without a
    collision. JS toggles the .flash/.show classes (added, then removed via
    setTimeout) rather than an infinite animation -- see
    shouldFlashNewCountry()/triggerNewCountryFlash() near engTick(). ---- */
 #newCountryGlow{position:fixed;inset:0;pointer-events:none;z-index:10500;opacity:0}
 #newCountryGlow.flash{opacity:1;animation:newCountryPulse 1.4s ease-in-out 2}
 @keyframes newCountryPulse{0%,100%{box-shadow:inset 0 0 8vw 1.5vw rgba(227,179,65,.55)}
  50%{box-shadow:inset 0 0 16vw 4vw rgba(227,179,65,.95)}}
 #newCountryBanner{position:fixed;top:14px;left:50%;z-index:10501;
  transform:translateX(-50%) translateY(-14px);background:#3d2f00;color:#e3b341;
  border:2px solid #e3b341;border-radius:8px;padding:12px 22px;text-align:center;
  box-shadow:0 8px 24px rgba(0,0,0,.6);opacity:0;pointer-events:none;
  transition:opacity .25s ease-out,transform .25s ease-out}
 #newCountryBanner.show{opacity:1;transform:translateX(-50%) translateY(0)}
 .newCountryBannerTitle{font-size:17px;font-weight:800;letter-spacing:.03em}
 .newCountryBannerBody{font-size:13px;font-weight:600;margin-top:4px;color:#f2d67a}
 /* ---- DX Mode confirm modal: z-index 9999, above both glow layers, so it
    stays fully legible/clickable regardless of TX/DX-armed state. ---- */
 .modalOverlay{position:fixed;inset:0;z-index:9999;background:rgba(1,4,9,.72);
  display:flex;align-items:center;justify-content:center}
 .modalBox{background:#161b22;border:1px solid #30363d;border-radius:8px;
  max-width:420px;padding:16px 18px;box-shadow:0 8px 24px rgba(0,0,0,.5)}
 .modalTitle{font-size:15px;font-weight:700;color:#58a6ff;margin-bottom:8px}
 .modalBody{font-size:12.5px;color:#c9d1d9;line-height:1.5}
 .modalBody ul{margin:8px 0;padding-left:18px}
 .modalBody li{margin:4px 0}
 /* ---- Help modal: reuses .modalOverlay/.modalBox above but a much bigger
    box + an internal tab bar. z-index 10000, above both glow layers (9997/
    9998) and the DX-confirm modal (9999) -- the two modals aren't expected
    to ever be open together, but if they somehow were, help should still
    win. ---- */
 #helpModal{z-index:10000}
 #helpModal .modalBox{max-width:92vw;width:920px;height:88vh;display:flex;
  flex-direction:column;padding:0}
 .helpHead{display:flex;align-items:center;padding:12px 16px;border-bottom:1px solid #21262d}
 .helpHead .modalTitle{flex:1;margin:0}
 .helpClose{background:none;border:1px solid #30363d;color:#8b949e;border-radius:5px;
  width:26px;height:26px;cursor:pointer;font-size:14px;line-height:1}
 .helpClose:hover{color:#c9d1d9;border-color:#484f58}
 /* ---- country info card: a SMALL popup anchored above the specific map
    point clicked (a contact dot or a Logbook row's grid) -- not a
    dashboard-wide modal. position:fixed, left/top set in JS by
    popupScreenPos(); no full-screen backdrop, so the rest of the
    dashboard stays visible/usable while it's open. ---- */
 #countryCard{position:fixed;z-index:10600;pointer-events:none}
 #countryCardBox{pointer-events:auto;width:300px;max-width:92vw;background:#161b22;
  border:1px solid #30363d;border-radius:8px;box-shadow:0 10px 28px rgba(0,0,0,.55);position:relative}
 #countryCardBox::after{content:'';position:absolute;top:100%;left:50%;transform:translateX(-50%);
  border:7px solid transparent;border-top-color:#30363d}
 #countryCardBox::before{content:'';position:absolute;top:100%;left:50%;transform:translateX(-50%) translateY(-1px);
  border:6px solid transparent;border-top-color:#161b22;z-index:1}
 #ccTitle{font-size:14px;font-weight:700;color:#58a6ff}
 #ccTop{display:flex;align-items:center;gap:12px;padding:12px 14px 4px}
 #ccFlag{line-height:1;min-width:56px}
 #ccFlag img{width:56px;height:auto;border-radius:3px;border:1px solid #30363d;display:block}
 #ccPhoto{max-width:110px;max-height:80px;border-radius:6px;object-fit:cover;border:1px solid #30363d}
 #ccFacts{padding:4px 14px 0}
 .ccRow{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #21262d;font-size:12px}
 .ccLabel{color:#8b949e}
 .ccVal{color:#c9d1d9;font-weight:600;text-align:right}
 #ccPhotoStatus{padding:4px 14px 0;font-size:11px;color:#8b949e}
 #countryCard .arow{padding:0 14px 12px;margin-top:8px}
 .helpTabs{display:flex;gap:4px;padding:8px 16px 0}
 .helpTab{background:none;border:1px solid #30363d;border-bottom:none;color:#8b949e;
  border-radius:6px 6px 0 0;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer}
 .helpTab.active{background:#161b22;color:#58a6ff;border-color:#1f6feb}
 .helpBody{flex:1;overflow:auto;padding:16px 20px;font-size:13px;color:#c9d1d9;line-height:1.6}
 .helpBody h3{color:#58a6ff;font-size:14px;margin:0 0 10px}
 .helpBody ol,.helpBody ul{margin:6px 0 14px;padding-left:20px}
 .helpBody li{margin:6px 0}
 .helpBody code{background:#0d1117;border:1px solid #30363d;border-radius:3px;
  padding:1px 5px;font-family:ui-monospace,monospace;font-size:12px}
 .helpPane{display:none}
 .helpPane.active{display:block}
 #btnBell.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
 #dryrunBanner{background:#3d2f00;color:#e3b341;border:1px solid #6b5300;border-radius:6px;
  padding:4px 10px;font-size:12px;font-weight:700;display:none;margin-bottom:8px}
 /* ---- TX transparency widget ---- */
 .widget[data-key=txpanel]{width:420px;height:320px}
 #txMsg{font-size:19px;font-weight:800;font-family:ui-monospace,monospace;color:#8b949e}
 #txMsg.tx-live{color:#f85149;animation:pulse 1s ease-in-out infinite}
 #txAbortMsg{color:#f85149;font-weight:700}

 /* ---- widget system ---- */
 #dash{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start}
 .widget{background:#161b22;border:1px solid #30363d;border-radius:8px;display:flex;
  flex-direction:column;resize:both;overflow:auto;min-width:230px;min-height:96px;box-sizing:border-box}
 .widget.collapsed{resize:none;height:auto!important;min-height:0}
 .widget.collapsed .wbody{display:none}
 .wtitle{display:flex;align-items:center;gap:8px;padding:7px 10px;cursor:grab;user-select:none;
  border-bottom:1px solid #21262d;background:#11151c;flex:0 0 auto;border-radius:7px 7px 0 0}
 .wtitle:active{cursor:grabbing}
 .wtitle .wname{flex:1;font-size:12px;font-weight:700;color:#8b949e;letter-spacing:.04em;text-transform:uppercase}
 .wtitle .maptbtn{font-size:11px;padding:1px 8px}
 .wtitle .maptbtn.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
 .wcollapse{background:none;border:1px solid #30363d;color:#8b949e;border-radius:4px;
  font-size:11px;width:20px;height:18px;cursor:pointer;line-height:1}
 .wcollapse::before{content:'\\2013'}
 .widget.collapsed .wcollapse::before{content:'+'}
 .wcollapse:hover{color:#c9d1d9;border-color:#484f58}
 .wbody{padding:10px;flex:1 1 auto;overflow:auto;min-height:0}
 .widget[data-key=waterfall]{width:600px;height:220px}
 .widget[data-key=map]{width:380px;height:250px}
 .widget[data-key=decodes]{width:540px;height:320px}
 .widget[data-key=ops]{width:300px;height:320px}
 .widget[data-key=events]{width:880px;height:170px}
 .widget[data-key=actions]{width:300px;height:400px}
 .widget[data-key=stationcfg]{width:340px;height:420px}
 .widget[data-key=qrz]{width:340px;height:340px}
 .widget[data-key=logbook]{width:560px;height:340px}
 #lbTable td.lb-confirmed{color:#3fb950;font-weight:700}
 #lbTable td.lb-uploaded{color:#56d4dd}
 #lbTable td.lb-notsynced{color:#8b949e}
 #lbTable tr.lbRow{cursor:pointer}
 #lbTable tr.lbRow:hover{background:#161b22}
 .widget[data-key=status]{width:100%;height:66px}

 .actionbtn{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:5px;
  padding:5px 10px;font-size:12px;cursor:pointer}
 #snrRiskBar{height:5px;border-radius:3px;background:#21262d;margin:4px 0;overflow:hidden}
 #snrRiskFill{height:100%;width:0%;background:#3fb950;transition:width .15s,background .15s}
 .actionbtn:hover{border-color:#58a6ff} .actionbtn:disabled{opacity:.5;cursor:default}
 .actionbtn.warn{background:#3d1f16;border-color:#f0883e;color:#f0883e}
 .arow{display:flex;gap:8px;align-items:center;margin:6px 0;flex-wrap:wrap}
 .astatus{display:flex;gap:16px;margin-bottom:6px;flex-wrap:wrap}
 .callchip{background:#0d1117;border:1px solid #30363d;color:#56d4dd;border-radius:12px;
  padding:2px 9px;font-size:12px;font-family:ui-monospace,monospace;cursor:pointer;margin:2px 3px 2px 0}
 .callchip:hover{border-color:#56d4dd} .callchip:disabled{opacity:.5;cursor:default}
 .callchip-main{color:#3fb950;border-color:#3fb950}
 select,input[type=number],input[type=text]{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:3px}
 details summary{cursor:pointer} details>.arow{margin:6px 0}
</style></head><body>
<div id=newCountryGlow></div>
<div id=newCountryBanner><div class=newCountryBannerTitle>✨ NEW COUNTRY ✨</div><div id=newCountryBannerBody class=newCountryBannerBody></div></div>
<h1>\U0001F4FB COTA <small>— __MYCALL__ · __MYGRID__ · Mode: FT8 · RX monitor</small> <span id=stale>⚠ STALE — rx-loop not updating</span></h1>
<div id=cockpit>
 <div class=cpitem><span class=cpk>STATE</span><span class="cpv st-" id=cpState>—</span></div>
 <div class=cpitem><span class=cpk>CALLING</span><span class=cpv id=cpCalling title="where the current target is (DXCC-style prefix lookup, best-effort)">—</span></div>
 <div class=cpitem><span class=cpk>QSO STEP</span><span class=cpv id=cpQsoStep title="progress through the current exchange: call -&gt; report -&gt; RR73/73 -&gt; done">—</span></div>
 <div class=cpitem><span class=cpk>BAND</span><span class=cpv id=cpBand>—</span></div>
 <div class=cpitem><span class=cpk>NEXT CALL</span><span class="cpv" id=cpNext>—</span></div>
 <div class=cpitem><span class=cpk>NEXT TX</span><span class=cpv id=cpNextTx title="countdown to the next scheduled key-up, or ON AIR while transmitting">—</span></div>
 <div class=spacer></div>
 <button id=btnBell class=actionbtn title="desktop alerts: new QSO, Automatic CQ ended, watchdog/abort, decode silence &gt;3 min">Alerts: OFF</button>
 <button id=resetLayout class=actionbtn title="restore default widget layout">Reset layout</button>
 <button id=btnTune30 title="stop Automatic CQ + rigctl T 0, then a 30s window to run a manual TUNE cycle — does not auto-resume, click Automatic CQ again when done">TUNE</button>
 <label id=dxToggleWrap title="chase stations outside your own country/DXCC entity only, and allow directed CQ DX">
  DX Mode
  <span class=dxSwitch><input type=checkbox id=dxModeToggle><span class=dxSwitchTrack></span></span>
 </label>
 <button id=btnUnkey title="stop Automatic CQ + rigctl T 0 — no confirmation">STOP</button>
 <button id=btnInfo title="help: quickstart, controls, widgets">ℹ</button>
</div>
<div id=dash>

 <div class=widget data-key=status>
  <div class=wtitle><span class=wname>Status</span><button class=wcollapse></button></div>
  <div class=wbody><div class=infobar id=info><span class=dim>loading station config…</span></div></div>
 </div>

 <div class=widget data-key=decodes>
  <div class=wtitle><span class=wname>Decodes</span><span class=dim id=upd></span><button class=wcollapse></button></div>
  <div class=wbody><table id=dec><tr><th>slot</th><th>SNR</th><th>DT</th><th>Hz</th><th>message</th></tr></table></div>
 </div>

 <div class=widget data-key=ops>
  <div class=wtitle><span class=wname>Next call</span><button class=wcollapse></button></div>
  <div class=wbody id=opsBody>
   <div class="next dim">suggestion:</div>
   <div class=next id=next>—</div>
   <div class=dim id=cand></div>
   <div class=arow><button id=btnSkip class=actionbtn>Skip current target</button>
    <span class=dim id=targetStatus></span></div>
   <div style="margin-top:10px"><span class=wname style="text-transform:none;font-size:11px">Calling ME</span>
    <div id=me class=dim>nobody yet</div></div>
   <div class=dim style="margin-top:8px">Click a callsign to request it as next target. Display + request only — the control operator transmits.</div>
  </div>
 </div>

 <div class=widget data-key=txpanel>
  <div class=wtitle><span class=wname>TX transparency</span><span class=dim id=txPanelSub>no TX yet this session</span><button class=wcollapse></button></div>
  <div class=wbody>
   <div class=dim style="margin-bottom:6px">The exact message and spectrogram actually keyed — full visibility, for troubleshooting "why didn't it transmit".</div>
   <div id=txMsg>—</div>
   <div id=txAbortMsg style="display:none"></div>
   <img id=txwf style="display:none;margin-top:8px" src="">
  </div>
 </div>

 <div class=widget data-key=actions id=actionsWidget>
  <div class=wtitle><span class=wname>Actions</span><button class=wcollapse></button></div>
  <div class=wbody>
   <div id=dryrunBanner>DRY-RUN MODE — actions are logged, not executed</div>
   <div class=astatus>
    <span class=it><span class=k id=stRxLabel>RX&nbsp;</span><span class=v id=stRx>—</span></span>
    <span class=it><span class=k>AUTO&nbsp;CQ&nbsp;</span><span class=v id=stChaser>—</span></span>
    <span class=it><span class=k>PTT&nbsp;</span><span class=v id=stPtt>—</span></span>
   </div>
   <div class=dim style="margin-bottom:4px">Receive-only monitoring — no TX is possible in this mode.</div>
   <div class=arow><button id=btnRxStart class=actionbtn>Start monitoring (RX only)</button>
    <button id=btnRxStop class=actionbtn>Stand down (stop RX + Automatic CQ)</button></div>
   <div class=dim style="margin:8px 0 4px">
    <span class=tx-capable style="border:1px solid;border-radius:4px;padding:1px 5px">TX-capable</span>
    — starts monitoring automatically if needed, then calls CQs and WILL key the radio when it finds one.
   </div>
   <div class=arow>
    <input id=chaseN type=number min=1 max=180 value=1>
    <select id=chaseMode><option value=qsos>QSOs</option><option value=minutes>minutes</option></select>
    <button id=btnChaseStart class="actionbtn warn tx-capable">Automatic CQ</button>
    <button id=btnChaseStop class=actionbtn>Stop</button>
   </div>
   <div class=arow style="margin-top:8px">
    <span class=dim style="min-width:64px">SNR floor</span>
    <input id=snrFloorSlider type=range min=-30 max=10 step=1 value=-16 style="flex:1 1 auto">
    <span id=snrFloorVal class=dim style="min-width:52px;text-align:right">-16 dB</span>
    <button id=snrFloorReset class=actionbtn title="reset to station.conf default">Reset</button>
   </div>
   <div id=snrRiskBar><div id=snrRiskFill></div></div>
   <div id=snrRiskLabel class=dim style="margin-bottom:6px"></div>
   <div id=chaseConfirmMsg class=dim style="display:none">You are the control operator — stay at the
    station and watch NEXT TX (top center) count down once a CQ is found; FT8 keys up on 15 s cycles.
    <div class=arow><button id=btnChaseConfirm class="actionbtn warn tx-capable">Confirm start Automatic CQ</button>
     <button id=btnChaseCancel class=actionbtn>Cancel</button></div></div>
   <div class=dim id=actionsMsg></div>
   <div class=dim style="margin-top:6px">STOP is always available, top right — no confirmation, one click.</div>
  </div>
 </div>

 <div class=widget data-key=stationcfg>
  <div class=wtitle><span class=wname>Station config</span><button class=wcollapse></button></div>
  <div class=wbody>
   <div class=dim style="margin-bottom:6px">Band/frequency is locked to the standard FT8 calling
    frequency for the selected band — no free-form entry. Wattage is capped to the antenna's
    confirmed RF-exposure-safe max (or a conservative __DEFAULT_MAX_W__ W default if unconfirmed).</div>
   <div class=arow><select id=antSelect style="flex:1 1 auto"></select></div>
   <div class=arow><select id=bandSelect style="flex:1 1 auto"></select>
    <select id=pwrSelect></select></div>
   <div class=arow><button id=stationSaveBtn class=actionbtn>Save station config</button></div>
   <div class=dim id=stationMsg></div>
   <details style="margin-top:8px">
    <summary class=dim style="cursor:pointer">Add / edit / remove antenna</summary>
    <div class=arow style="margin-top:6px">
     <input id=antName type=text placeholder="Antenna name" style="flex:1 1 auto"></div>
    <div class=arow id=antBandsRow></div>
    <div class=arow>
     <input id=antMaxW type=number min=0 max=1500 step=0.5
      placeholder="max safe W (blank = unconfirmed)" style="flex:1 1 auto"></div>
    <div class=arow><input id=antNotes type=text placeholder="notes" style="flex:1 1 auto"></div>
    <div class=arow>
     <button id=antAddBtn class=actionbtn>Add new</button>
     <button id=antUpdateBtn class=actionbtn>Update selected</button>
     <button id=antRemoveBtn class=actionbtn>Remove selected</button>
    </div>
    <div class=dim id=antMsg></div>
   </details>
  </div>
 </div>

 <div class=widget data-key=qrz>
  <div class=wtitle><span class=wname>QRZ Logbook</span><span class=dim id=qrzConfigured></span><button class=wcollapse></button></div>
  <div class=wbody>
   <div class=dim id=qrzSetupMsg style="display:none;margin-bottom:8px">
    No QRZ API key on file yet. This never gets typed into the browser —
    on the machine running this dashboard:
    <pre style="white-space:pre-wrap;font-size:11px;margin:6px 0">mkdir -p ~/.config/cota
echo 'YOUR-KEY' &gt; ~/.config/cota/qrz.key
chmod 600 ~/.config/cota/qrz.key</pre>
    Get the key at <b>logbook.qrz.com/logbook → Settings</b> (requires the
    "XML Logbook Data" subscription). No subscription? Free manual import
    instead: <b>logbook.qrz.com/logbook → Import</b>.
   </div>
   <div class=astatus>
    <span class=it><span class=k>PENDING&nbsp;</span><span class=v id=qrzPending>—</span></span>
    <span class=it><span class=k>SYNC&nbsp;</span><span class=v id=qrzSyncing>—</span></span>
   </div>
   <div class=arow><button id=qrzSyncBtn class=actionbtn>Sync to QRZ</button></div>
   <div class=dim id=qrzMsg></div>
   <div class=arow style="margin-top:6px">
    <label class=dim id=qrzAutoLabel style="display:flex;align-items:center;gap:4px;cursor:pointer"
     title="requires a QRZ API key on file first -- see the setup note above">
     <input type=checkbox id=qrzAutoToggle disabled> Auto sync &amp; upload
    </label>
    <span class=dim id=qrzAutoStatus></span>
   </div>
   <details style="margin-top:8px">
    <summary class=dim style="cursor:pointer">Recent sync log</summary>
    <pre id=qrzLog style="font-size:11px;max-height:140px;overflow-y:auto;margin-top:6px">no syncs yet</pre>
   </details>
  </div>
 </div>

 <div class=widget data-key=logbook>
  <div class=wtitle><span class=wname>Logbook</span>
   <span class=dim id=lbSummary></span>
   <button id=lbRefreshBtn class=actionbtn>Refresh from QRZ</button>
   <button class=wcollapse></button></div>
  <div class=wbody>
   <table id=lbTable>
    <tr><th>UTC</th><th>call</th><th>grid</th><th>band</th><th>sent</th><th>rcvd</th><th>QRZ</th></tr>
   </table>
   <div class=dim style="margin-top:6px">✔ confirmed = the other station's log matched yours on QRZ
    (call+band+mode, times within ±30 min — exact FT8 slot times confirm fast; hand-entered
    times outside the window never auto-confirm). ↑ uploaded = on QRZ, awaiting their side.</div>
  </div>
 </div>

 <div class=widget data-key=map>
  <div class=wtitle><span class=wname>World map</span>
   <span class=dim style="flex:0 0 auto">heard (cyan) · QSO worked (green) · TX (red) · home (gold)</span>
   <button id=mapAuto class="actionbtn maptbtn active">Auto</button>
   <button id=mapWorld class="actionbtn maptbtn">World</button>
   <button class=wcollapse></button></div>
  <div class=wbody style="padding:4px">
   <svg id=map viewBox="0 0 1000 500" preserveAspectRatio="xMidYMid meet">
    <path d="__WORLD__" fill="#1c2430" stroke="#30363d" stroke-width="0.5" vector-effect="non-scaling-stroke"/>
    <g id=stateBorders></g><g id=countryBorders></g>
    <g id=rx></g><g id=qso></g><g id=tx></g><g id=home></g>
   </svg>
  </div>
 </div>

 <div class=widget data-key=waterfall>
  <div class=wtitle><span class=wname>Waterfall</span><button class=wcollapse></button></div>
  <div class=wbody><img id=wf src=/waterfall.png></div>
 </div>

 <div class=widget data-key=events>
  <div class=wtitle><span class=wname>Events</span><span class=dim>data/chase.log — engine diary, last __EVENT_LINES__ lines</span>
   <label class=dim style="cursor:pointer"><input type=checkbox id=evRaw> raw</label>
   <button class=wcollapse></button></div>
  <div class=wbody><pre id=events>no events yet</pre></div>
 </div>

</div>
<div id=dxModal class=modalOverlay style="display:none">
 <div class=modalBox>
  <div class=modalTitle>Arm DX Mode?</div>
  <div class=modalBody>
   DX Mode chases stations outside your own country/DXCC entity only, and stops
   skipping directed "CQ DX" calls. Everything else about this chaser's
   etiquette is unchanged — DX Mode only changes which CQs are eligible:
   <ul>
    <li><b>Split-calling is unchanged</b> — it still picks its own clear
     offset and never calls on the DX station's frequency.</li>
    <li><b>Don't create your own pileup.</b> If a DX station already has one,
     expect to wait your turn — the pileup penalty and SNR floor still apply.</li>
    <li><b>Patience beats repetition.</b> A clean, well-timed call beats
     machine-gunning the same frame.</li>
    <li>The SNR floor (live-adjustable via the Actions widget's slider —
     lower = more candidates but higher risk they won't hear our reply back),
     busy-hold, repeat cap, and unkey watchdog are <b>not</b> affected by this
     toggle.</li>
   </ul>
   See the ZL2IFB FT8 Operating Guide (README's On-air etiquette section).
  </div>
  <div class=arow style="justify-content:flex-end">
   <button id=dxModalCancel class=actionbtn>Cancel</button>
   <button id=dxModalConfirm class="actionbtn warn">Arm DX Mode</button>
  </div>
 </div>
</div>
<div id=helpModal class=modalOverlay style="display:none">
 <div class=modalBox>
  <div class=helpHead>
   <div class=modalTitle>COTA help</div>
   <button id=helpClose class=helpClose title="close">✕</button>
  </div>
  <div class=helpTabs>
   <button class="helpTab active" data-tab=quickstart>Quickstart</button>
   <button class=helpTab data-tab=controls>Controls</button>
   <button class=helpTab data-tab=widgets>Widgets &amp; modes</button>
  </div>
  <div class=helpBody>
   <div class="helpPane active" data-pane=quickstart>
    <h3>Your first QSO</h3>
    <ol>
     <li><b>Configure your station.</b> Copy <code>station.conf.example</code> to
      <code>station.conf</code> and edit every value for YOUR station (callsign,
      grid, CAT port, rig model, audio device) — or run <code>bin/coa setup</code>
      for an interactive wizard that detects your hardware. <code>station.conf</code>
      is gitignored; your settings never leave this machine.</li>
     <li><b>Preflight.</b> Run <code>bin/coa doctor</code> — checks the CAT port,
      audio source, clock sync, mixer calibration, and reads back the rig's actual
      dial frequency against your configured <code>DIAL_HZ</code>. Fix anything it
      flags before continuing.</li>
     <li><b>Start receiving.</b> Click "Start monitoring (RX only)" in the Actions
      widget (or run <code>bin/coa start</code>) — this decodes and displays FT8
      traffic but never transmits. Watch the Decodes and World map widgets fill in
      to confirm your receive chain works before you ever key up.</li>
     <li><b>Run one QSO.</b> Set the count to 1 QSO, click "Automatic CQ", then
      "Confirm start Automatic CQ" in the Actions widget. You are still the control
      operator — stay at the radio, watch NEXT TX (top center) count down, and STOP
      (top right) is always one click away with no confirmation needed.</li>
     <li><b>What happens next.</b> The chaser answers the first workable CQ it hears
      (SNR floor + on-air etiquette rules apply — see the Controls tab), sequences
      the exchange automatically, and logs the QSO locally to standard ADIF
      (<code>wsjtx_log.adi</code>) the moment it completes.</li>
     <li><b>QRZ Logbook sync is entirely optional.</b> It needs your own paid QRZ
      XML/Logbook Data subscription and API key. Every QSO is already logged
      locally regardless of QRZ — QRZ sync only additionally pushes/pulls
      confirmation status to your QRZ.com account, and nothing in this app requires
      it to work.</li>
    </ol>
   </div>
   <div class=helpPane data-pane=controls>
    <h3>Cockpit &amp; Actions controls</h3>
    <ul>
     <li><b>Alerts</b> — desktop notifications for: new QSO, Automatic CQ ended,
      watchdog/abort, decode silence &gt;3 min.</li>
     <li><b>Reset layout</b> — restores the default widget positions/sizes.</li>
     <li><b>TUNE</b> — stops Automatic CQ and unkeys, then opens a 30 s window for
      a manual TUNE cycle; does not auto-resume chasing afterward, click Automatic
      CQ again when done.</li>
     <li><b>DX Mode</b> — arms DX Mode for the <i>next</i> Automatic CQ session
      (shows an "Arm DX Mode?" confirmation first); while armed, only chases
      stations outside your own DXCC entity/country and additionally allows
      answering directed "CQ DX" calls — every other etiquette rule (split-calling,
      SNR floor, busy-hold, repeat cap) still applies unchanged. The page's blue
      ambient glow means DX Mode is armed on the currently-running chase session.</li>
     <li><b>STOP</b> — stops Automatic CQ and unkeys (<code>rigctl T 0</code>) — no
      confirmation, always available, one click.</li>
     <li><b>Start monitoring (RX only) / Stand down</b> — start/stop the
      receive+decode loop (Stand down also stops Automatic CQ if running).</li>
     <li><b>QSO count / mode selector + Automatic CQ / Confirm / Cancel</b> —
      configures and arms a chase session; this is TX-capable and <i>will</i> key
      the radio once it finds an answerable CQ, hence the explicit confirm step.</li>
     <li><b>Skip current target</b> (Next call widget) — abandons the currently
      pursued target, moves on to the next candidate.</li>
     <li><b>Whole-page red glow</b> — literally keyed/transmitting, right now.
      Always the dominant visual regardless of what else is active.</li>
    </ul>
   </div>
   <div class=helpPane data-pane=widgets>
    <h3>Widgets</h3>
    <ul>
     <li><b>Status</b> — station config summary at a glance (callsign, grid, band,
      power, dial frequency).</li>
     <li><b>Decodes</b> — live table of every FT8 decode this slot.</li>
     <li><b>Next call</b> — the chaser's top-ranked candidate plus runner-ups
      (SNR-ranked); click a callsign chip to request it as the next target.</li>
     <li><b>TX transparency</b> — the exact message and waterfall snippet actually
      keyed, for troubleshooting "why didn't it transmit".</li>
     <li><b>Actions</b> — start/stop monitoring, arm/confirm/cancel Automatic CQ.</li>
     <li><b>Station config</b> — edit callsign/grid/band/power/antenna from the
      browser; writes back to <code>station.conf</code>.</li>
     <li><b>QRZ Logbook</b> — optional QRZ.com integration: manual "Sync to QRZ"
      upload, plus an "Auto sync &amp; upload" toggle that alternates upload and
      refresh every 1 minute (each repeating every 2 minutes) while this tab stays
      open — see its tooltip for details.</li>
     <li><b>Logbook</b> — every local QSO, newest first, with a QRZ status column
      (confirmed / uploaded / not-yet-synced) and a manual "Refresh from QRZ" pull.</li>
     <li><b>World map</b> — heard stations plotted by grid square (fading over
      ~15 min), your QTH marked, an animated arc to the station currently being
      worked while keyed.</li>
     <li><b>Waterfall</b> — live spectrogram of the receive audio.</li>
     <li><b>Events</b> — the chaser's own event diary (<code>data/chase.log</code>),
      color-coded by event type.</li>
     <li><b>Dry-run mode</b> — a banner appears when the whole app is running under
      <code>COA_DRYRUN</code>: every action is logged but never actually executed
      (no rig, no network) — used for safely testing the dashboard itself.</li>
    </ul>
   </div>
  </div>
 </div>
</div>
<div id=countryCard style="display:none">
 <div id=countryCardBox>
  <div class=helpHead>
   <div class=modalTitle id=ccTitle>—</div>
   <button id=countryCardClose class=helpClose title="close">✕</button>
  </div>
  <div class=modalBody>
   <div id=ccTop>
    <div id=ccFlag>—</div>
    <img id=ccPhoto style="display:none">
   </div>
   <div id=ccFacts>
    <div class=ccRow><span class=ccLabel>Callsign</span><span id=ccCall class=ccVal>—</span></div>
    <div class=ccRow><span class=ccLabel>Population</span><span id=ccPop class=ccVal>—</span></div>
    <div id=ccDishRow class=ccRow style="display:none"><span class=ccLabel>National dish</span><span id=ccDish class=ccVal>—</span></div>
    <div id=ccFlowerRow class=ccRow style="display:none"><span class=ccLabel>National flower</span><span id=ccFlower class=ccVal>—</span></div>
   </div>
   <div id=ccPhotoStatus class=dim></div>
   <div class=arow style="justify-content:flex-end;margin-top:10px">
    <button id=ccCallBtn class="actionbtn warn" style="display:none">Call this station</button>
   </div>
  </div>
 </div>
</div>
<script>
const DRYRUN = __DRYRUN__;
function evClass(l){
 if(/\\bTX #|keyed/.test(l)) return 'tx';
 if(/LOGGED QSO|ANSWERED|QSO complete|DONE:|: done \\(completed/.test(l)) return 'good';
 if(/^ABORT|PTT did not release|STALE|never acknowledged|reporting to someone else/.test(l)) return 'bad';
 if(/DX Mode: unknown country/.test(l)) return 'unknownctry';
 if(/DX Mode|DX MODE/.test(l)) return 'dx';
 if(/^skip|skip requested|still busy|no answer at|no response from|: fail\\b/.test(l)) return 'skip';
 if(/chaser start|time budget reached|stopping:|session report|breather:/.test(l)) return 'info';
 return '';
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

/* ---- human-friendly log rendering (display only — chase.log on disk is
   untouched, this only reformats what's shown in the #events widget). Each
   pattern below mirrors one of qso.py's ev(...) call sites 1:1; anything
   that doesn't match a known pattern falls back to the raw line untouched
   so nothing is ever hidden, just possibly less pretty. ---- */
const LOG_PATTERNS=[
 [/^ABORT: '(.+)' hit the (\\d+)-repeat cap$/, m=>`🛑 Giving up on "${m[1]}" — already tried ${m[2]} times`],
 [/^ABORT TX: dial reads (\\S+), expected (\\S+) — NOT keying$/, m=>`🛑 Radio is on the wrong frequency (reads ${m[1]} Hz, expected ${m[2]} Hz) — refused to transmit`],
 [/^ABORT TX: could not schedule a slot with our parity$/, ()=>`🛑 Couldn't schedule a transmit slot — aborted`],
 [/^ABORT: PTT did not release!$/, ()=>`🛑 PTT did not release after transmitting — check the radio`],
 [/^ABORT: PTT not idle at start$/, ()=>`🛑 Radio was already transmitting at startup — refused to begin`],
 [/^TX #(\\d+) '(.+)' @ (\\d+) Hz \\(\\d+x this msg, ~13\\.5 s keyed\\)$/, m=>`📡 Transmitting #${m[1]}: "${m[2]}" @ ${m[3]} Hz`],
 [/^unkeyed, PTT verify: (\\S+)$/, m=>m[1]==='0'?`🔇 Unkeyed — radio confirmed off air`:`⚠️ Unkeyed, but PTT still reads "${m[1]}" — check the radio`],
 [/^LOGGED QSO: (\\S+) (\\S*) sent (\\S+) rcvd (\\S+) -> wsjtx_log\\.adi$/, m=>`✅ QSO logged: ${m[1]}${m[2]?` (${m[2]})`:''} — sent ${m[3]}, received ${m[4]}`],
 [/^session report written: (.+)$/, ()=>`📝 Session report saved`],
 [/^WARN: could not write session report: (.+)$/, m=>`⚠️ Couldn't save session report: ${m[1]}`],
 [/^chaser start: target (\\d+) QSO\\(s\\)(?: \\/ ([\\d.]+) min budget)?( \\[DX MODE\\])?, dial (\\d+), watchdog ([\\d.]+)s, repeat cap (\\d+)$/,
  m=>`▶️ Automatic CQ started — aiming for ${m[1]} QSO(s)${m[2]?` / ${m[2]} min budget`:''}${m[3]?' — 🌍 DX Mode':''}, dial ${(m[4]/1e6).toFixed(3)} MHz`],
 [/^time budget reached: (\\d+) QSO\\(s\\) in ([\\d.]+) min$/, m=>`⏱️ Time's up — ${m[1]} QSO(s) completed in ${m[2]} min`],
 [/^stopping: (\\d+) targets tried, (\\d+) completed$/, m=>`⏹️ Stopping — tried ${m[1]} stations, completed ${m[2]}`],
 [/^skip CQ (\\S+) (\\S+) — directed CQ not for us$/, m=>`⏭️ Skipped ${m[2]} — CQ was directed elsewhere (${m[1]})`],
 [/^skip (\\S+) — DX Mode: unknown country \\(prefix gap\\)$/, m=>`⏭️ Skipped ${m[1]} — 🟣 DX Mode: unknown country (prefix table gap)`],
 [/^skip (\\S+) — DX Mode: same country \\(not DX\\)$/, m=>`⏭️ Skipped ${m[1]} — 🌍 DX Mode: same country (not DX)`],
 [/^skip (\\S+) at (-?\\d+) dB — below SNR floor (-?\\d+) \\(reciprocity\\)$/, m=>`⏭️ Skipped ${m[1]} — too weak (${m[2]} dB, need ${m[3]}+)`],
 [/^DX Mode: prioritizing (\\S+) \\(new country\\) over (\\d+) stronger candidate\\(s\\)$/,
  m=>`🌍 DX Mode: prioritizing ${m[1]} (new country) over ${m[2]} stronger candidate${m[2]==='1'?'':'s'}`],
 [/^TARGET (\\S+) (\\S*) \\(CQ (-?\\d+) dB @ (\\d+) Hz, their parity (even|odd)\\) -> our offset (\\d+) Hz \\(gap (\\d+) Hz\\)$/,
  m=>`🎯 Targeting ${m[1]}${m[2]?` (${m[2]})`:''} — heard at ${m[3]} dB, calling on ${m[6]} Hz`],
 [/^skip requested for (\\S+) — abandoning target$/, m=>`⏭️ You skipped ${m[1]} — moving on`],
 [/^busy-hold: (\\S+) working someone else — skipping our tx cycle \\((\\d+)\\/4\\)$/, m=>`⏸️ ${m[1]} is busy with someone else — waiting (${m[2]}/4)`],
 [/^(\\S+) flipped slot parity \\((\\d+) Hz\\) — we now tx on (even|odd) slots$/, m=>`🔄 ${m[1]} switched timing — now transmitting on ${m[3]} slots`],
 [/^ANSWERED: (\\S+) gives us (\\S+) -> sending R(\\S+)$/, m=>`✅ ${m[1]} answered! They report ${m[2]} — sending our reply`],
 [/^(\\S+) sends (\\S+) — QSO complete, sending courtesy 73$/, m=>`✅ ${m[1]} confirmed — QSO complete, sending 73`],
 [/^(\\S+) is CQing again — he lost our R-report; moving (\\d+) -> (\\d+) Hz \\(gap (\\d+) Hz\\), still sending R(\\S+)$/,
  m=>`🔄 ${m[1]} didn't get our reply — retrying on ${m[3]} Hz`],
 [/^(\\S+): R-report never acknowledged after (\\d+) cycles — giving up$/, m=>`🛑 ${m[1]} never confirmed our reply after ${m[2]} tries — giving up`],
 [/^(\\S+) is reporting to someone else mid-QSO — aborting target$/, m=>`🛑 ${m[1]} switched to another station mid-QSO — giving up`],
 [/^(\\S+) still busy after 4 skipped cycles — moving on$/, m=>`⏭️ ${m[1]} still busy after waiting — moving on`],
 [/^no answer at (\\d+) Hz after 3 calls — new clear offset (\\d+) Hz \\(gap (\\d+) Hz\\)$/, m=>`🔄 No answer at ${m[1]} Hz after 3 tries — trying ${m[2]} Hz instead`],
 [/^no response from (\\S+) after 6 calls on 2 offsets — moving on$/, m=>`⏭️ No response from ${m[1]} after 6 tries on 2 frequencies — moving on`],
 [/^no response from (\\S+) after (\\d+) tries in state '(\\w+)' — moving on$/, m=>`⏭️ No response from ${m[1]} after ${m[2]} tries — moving on`],
 [/^no response from (\\S+) after 3 calls — another station is calling us, moving on to them$/,
  m=>`⏭️📻 No response from ${m[1]} after 3 tries — another station is calling us, moving on to them`],
 [/^no response from (\\S+) after (\\d+) tries in state '(\\w+)' — another station is calling us, moving on to them$/,
  m=>`⏭️📻 No response from ${m[1]} after ${m[2]} tries — another station is calling us, moving on to them`],
 [/^target (\\S+): (done|fail) \\(completed (\\d+)\\/(\\d+)\\)$/,
  m=>m[2]==='done'?`✅ ${m[1]}: done — ${m[3]}/${m[4]} QSOs this run`:`❌ ${m[1]}: no contact — ${m[3]}/${m[4]} QSOs this run`],
 [/^breather: sitting out one 15 s cycle \\((\\d+) s keyed this session\\)$/, m=>`☕ Taking a short breather (${m[1]}s keyed so far)`],
 [/^DONE: (\\d+) QSO\\(s\\) completed and logged\\. PTT: (\\S+)$/, m=>`🏁 Finished — ${m[1]} QSO(s) completed and logged`],
];
function humanizeLogLine(raw){
 const tm=raw.match(/^(\\d{2}:\\d{2}:\\d{2}) (.*)$/);
 const ts=tm?tm[1]+'Z':null, rest=tm?tm[2]:raw;
 for(const [re,fn] of LOG_PATTERNS){
  const m=rest.match(re);
  if(m) return (ts?ts+'  ':'')+fn(m);
 }
 return ts?ts+'  '+rest:rest;
}
let lastEventLines=[];
function renderEvents(){
 const el=document.getElementById('events');
 const atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<30;
 const raw=document.getElementById('evRaw').checked;
 el.innerHTML=lastEventLines.length
  ? lastEventLines.map(l=>`<span class="${evClass(l)}">${esc(raw?l:humanizeLogLine(l))}</span>`).join('\\n')
  : 'no events yet';
 if(atBottom) el.scrollTop=el.scrollHeight;
}

/* ---- world map ---- */
const MW=1000, MH=500;
let HOME=null, CFG=null;
let mapPoints={rx:[], tx:null, qso:[]};
/* ---- fallback grid source for the TX line: many CQs omit a grid, and
   engine.json's grid field is only ever set from the CQ we originally
   answered (never updated later in the exchange) -- so a gridless CQ meant
   the line never drew for that whole chase. Populated from the same recent-
   decode scan renderRX() already does, keyed by call. ---- */
let recentGridByCall={};
let snrFloorInitialized=false;
function grid2ll(g){                       // Maidenhead 4/6-char -> [lat,lon] (cell center)
 g=(g||'').trim().toUpperCase();
 if(!/^[A-R]{2}[0-9]{2}([A-X]{2})?$/.test(g)) return null;
 let lon=(g.charCodeAt(0)-65)*20-180 + (g.charCodeAt(2)-48)*2;
 let lat=(g.charCodeAt(1)-65)*10-90  + (g.charCodeAt(3)-48);
 if(g.length>=6){ lon+=(g.charCodeAt(4)-65)/12 + 1/24; lat+=(g.charCodeAt(5)-65)/24 + 1/48; }
 else           { lon+=1; lat+=0.5; }
 return [lat,lon];
}
function ll2xy(ll){ return [(ll[1]+180)/360*MW, (90-ll[0])/180*MH]; }
function isGrid(t){ return /^[A-R]{2}[0-9]{2}$/.test(t) && t!=='RR73'; }
/* ---- callsign prefix -> country, display only (best-effort DXCC-style
   lookup, not exhaustive). Longest matching prefix wins regardless of list
   order, so a 2-char entry like A7/Qatar always beats a broader single-
   letter US range -- no need to hand-sort this list for collisions. ---- */
const CALL_PREFIXES=__CALL_PREFIXES_JSON__;
function callCountry(call){
 if(!call) return '';
 const base=call.split('/')[0].toUpperCase();
 let best=null;
 for(const [pfx,country] of CALL_PREFIXES){
  if(base.startsWith(pfx) && (!best || pfx.length>best[0].length)) best=[pfx,country];
 }
 return best?best[1]:'';
}
/* ---- US state from grid square lat/lon: approximate rectangular bounding
   boxes, not real state borders -- good enough for a casual cockpit display,
   will be wrong near some state lines. [minLat,maxLat,minLon,maxLon]. ---- */
const US_STATE_BOXES=[
 ['Alabama',30.2,35.0,-88.5,-84.9],['Arizona',31.3,37.0,-114.8,-109.0],
 ['Arkansas',33.0,36.5,-94.6,-89.6],['California',32.5,42.0,-124.4,-114.1],
 ['Colorado',37.0,41.0,-109.1,-102.0],['Connecticut',41.0,42.1,-73.7,-71.8],
 ['Delaware',38.4,39.8,-75.8,-75.0],['Florida',24.5,31.0,-87.6,-80.0],
 ['Georgia',30.4,35.0,-85.6,-80.8],['Idaho',42.0,49.0,-117.2,-111.0],
 ['Illinois',37.0,42.5,-91.5,-87.0],['Indiana',37.8,41.8,-88.1,-84.8],
 ['Iowa',40.4,43.5,-96.6,-90.1],['Kansas',37.0,40.0,-102.1,-94.6],
 ['Kentucky',36.5,39.1,-89.6,-82.0],['Louisiana',29.0,33.0,-94.0,-89.0],
 ['Maine',43.0,47.5,-71.1,-66.9],['Maryland',37.9,39.7,-79.5,-75.0],
 ['Massachusetts',41.2,42.9,-73.5,-69.9],['Michigan',41.7,48.3,-90.4,-82.4],
 ['Minnesota',43.5,49.4,-97.2,-89.5],['Mississippi',30.2,35.0,-91.7,-88.1],
 ['Missouri',36.0,40.6,-95.8,-89.1],['Montana',44.4,49.0,-116.1,-104.0],
 ['Nebraska',40.0,43.0,-104.1,-95.3],['Nevada',35.0,42.0,-120.0,-114.0],
 ['New Hampshire',42.7,45.3,-72.6,-70.6],['New Jersey',38.9,41.4,-75.6,-73.9],
 ['New Mexico',31.3,37.0,-109.1,-103.0],['New York',40.5,45.0,-79.8,-71.9],
 ['North Carolina',33.8,36.6,-84.3,-75.5],['North Dakota',45.9,49.0,-104.1,-96.6],
 ['Ohio',38.4,42.0,-84.8,-80.5],['Oklahoma',33.6,37.0,-103.0,-94.4],
 ['Oregon',42.0,46.3,-124.6,-116.5],['Pennsylvania',39.7,42.3,-80.5,-74.7],
 ['Rhode Island',41.1,42.0,-71.9,-71.1],['South Carolina',32.0,35.2,-83.4,-78.5],
 ['South Dakota',42.5,45.9,-104.1,-96.4],['Tennessee',35.0,36.7,-90.3,-81.6],
 ['Texas',25.8,36.5,-106.7,-93.5],['Utah',37.0,42.0,-114.1,-109.0],
 ['Vermont',42.7,45.0,-73.5,-71.5],['Virginia',36.5,39.5,-83.7,-75.2],
 ['Washington',45.5,49.0,-124.8,-116.9],['West Virginia',37.2,40.6,-82.7,-77.7],
 ['Wisconsin',42.4,47.1,-92.9,-86.8],['Wyoming',41.0,45.0,-111.1,-104.0],
 ['District of Columbia',38.8,39.0,-77.1,-76.9],
];
function usStateFromGrid(grid){
 const ll=grid2ll(grid); if(!ll) return '';
 const [lat,lon]=ll;
 for(const [name,minLat,maxLat,minLon,maxLon] of US_STATE_BOXES){
  if(lat>=minLat && lat<=maxLat && lon>=minLon && lon<=maxLon) return name;
 }
 return '';
}
/* ---- fallback when a CQ carries no grid at all (some special-event/
   compound calls omit one): the "call area" digit right after a US call's
   prefix letters (the "5" in W5C) gives a rough historical region -- not
   reliable post-vanity-callsigns, but far better than nothing, and gives
   the map a point to plot instead of skipping the target entirely. Always
   labeled "(approx.)" so it's never confused with a real grid-derived fix. ---- */
const US_CALL_AREAS={
 '0':{label:'North Central US',ll:[40.0,-98.0]},
 '1':{label:'New England, US',ll:[42.5,-71.5]},
 '2':{label:'New York / New Jersey, US',ll:[41.0,-74.5]},
 '3':{label:'Mid-Atlantic, US',ll:[39.5,-77.0]},
 '4':{label:'Southeast US',ll:[33.5,-84.0]},
 '5':{label:'South Central US',ll:[32.5,-97.0]},
 '6':{label:'California, US',ll:[37.0,-119.5]},
 '7':{label:'Pacific NW / Mountain, US',ll:[44.0,-116.0]},
 '8':{label:'Ohio Valley, US',ll:[40.0,-82.5]},
 '9':{label:'Great Lakes, US',ll:[42.0,-89.0]},
};
function usCallAreaInfo(call){
 if(!call) return null;
 const base=call.split('/')[0].toUpperCase();
 const m=base.match(/^[A-Z]{1,2}([0-9])/);
 return m?(US_CALL_AREAS[m[1]]||null):null;
}
/* ---- full "where are we calling" label: US contacts show the actual state
   (from their grid, since a callsign prefix alone can't tell you that),
   falling back to the approximate call-area region when there's no grid;
   everything else shows the country from callCountry(). ---- */
function callLocation(call, grid){
 const country=callCountry(call);
 if(country==='United States'){
  if(grid){
   const state=usStateFromGrid(grid);
   if(state) return `${state}, USA (${grid})`;
  }
  const area=usCallAreaInfo(call);
  if(area) return `${area.label} (approx.)`;
  return grid?`United States (${grid})`:'United States';
 }
 if(country==='Alaska') return grid?`Alaska, USA (${grid})`:'Alaska, USA';
 if(country==='Hawaii / Pacific') return grid?`Hawaii, USA (${grid})`:'Hawaii, USA';
 if(country) return grid?`${country} (${grid})`:country;
 return grid||call||'';
}
/* ---- lat/lon for the map: prefer the real grid, fall back to the
   call-area's approximate center so a gridless target still gets plotted
   instead of vanishing from the map entirely. ---- */
function targetLatLon(call, grid){
 const ll=grid2ll(grid); if(ll) return ll;
 if(callCountry(call)==='United States'){
  const area=usCallAreaInfo(call);
  if(area) return area.ll;
 }
 return null;
}
function decodeTime(date,slot){            // "260704","014045" -> ms UTC
 let t=Date.UTC(2000+ +date.slice(0,2), +date.slice(2,4)-1, +date.slice(4,6),
                +slot.slice(0,2), +slot.slice(2,4), +slot.slice(4,6));
 if(t>Date.now()+60000) t-=86400000;       // midnight wrap
 return t;
}

/* ---- viewBox auto-zoom (part A): fit bbox of home+RX dots+TX endpoint,
   ~15% pad, clamped to [2-grid-field .. whole world], eased over ~450ms.
   All layers share the viewBox so they stay geometrically correct; strokes
   use vector-effect=non-scaling-stroke and marker/label sizes are scaled by
   var(--map-scale) so they stay visually constant as the box zooms. ---- */
let vb={x:0,y:0,w:MW,h:MH}, vbTarget={x:0,y:0,w:MW,h:MH};
let vbAnimFrom=null, vbAnimStart=0, vbAnimId=null;
const VB_ANIM_MS=450;
const MIN_VB_W=110, MIN_VB_H=55;            // ~2 Maidenhead grid fields (40°lon x 20°lat)
let mapMode='auto';

/* ---- hand-rolled pan/zoom: pure viewBox math, no new dependencies. Drag
   pans with "grab the map" semantics (content follows the cursor); wheel
   zooms toward the cursor position. Both pure functions for Node-harness
   testing; the DOM-touching event wiring below just calls these and
   assigns straight into vb/vbTarget (no eased animation during an active
   drag/zoom -- that's for programmatic auto-fit jumps only). ---- */
function panViewBox(vb, dxPx, dyPx, svgPxW, svgPxH){
 const sx=vb.w/svgPxW, sy=vb.h/svgPxH;
 let x=vb.x-dxPx*sx, y=vb.y-dyPx*sy;
 x=Math.max(0,Math.min(MW-vb.w,x));
 y=Math.max(0,Math.min(MH-vb.h,y));
 return {x,y,w:vb.w,h:vb.h};
}
function zoomViewBox(vb, factor, cxFrac, cyFrac){
 const px=vb.x+cxFrac*vb.w, py=vb.y+cyFrac*vb.h;
 const AR=MW/MH;
 let w=Math.max(MIN_VB_W,Math.min(MW,vb.w*factor));
 let h=w/AR;
 if(h>MH){h=MH;w=h*AR;} else if(h<MIN_VB_H){h=MIN_VB_H;w=h*AR;}
 let x=px-cxFrac*w, y=py-cyFrac*h;
 x=Math.max(0,Math.min(MW-w,x));
 y=Math.max(0,Math.min(MH-h,y));
 return {x,y,w,h};
}
function lerp(a,b,t){return a+(b-a)*t;}
function applyViewBox(v){
 const svg=document.getElementById('map');
 svg.setAttribute('viewBox', v.x.toFixed(2)+' '+v.y.toFixed(2)+' '+v.w.toFixed(2)+' '+v.h.toFixed(2));
 svg.style.setProperty('--map-scale', (v.w/MW).toFixed(4));
}
function vbEqual(a,b){return Math.abs(a.x-b.x)<0.5&&Math.abs(a.y-b.y)<0.5&&Math.abs(a.w-b.w)<0.5&&Math.abs(a.h-b.h)<0.5;}
function animateViewBoxTo(target){
 if(vbEqual(target,vbTarget)&&vbEqual(vb,target)) return;
 vbTarget=target;
 if(vbAnimId) cancelAnimationFrame(vbAnimId);
 vbAnimFrom={x:vb.x,y:vb.y,w:vb.w,h:vb.h}; vbAnimStart=performance.now();
 function step(now){
  const t=Math.min(1,(now-vbAnimStart)/VB_ANIM_MS);
  const e=1-Math.pow(1-t,3);                // easeOutCubic
  vb={x:lerp(vbAnimFrom.x,vbTarget.x,e), y:lerp(vbAnimFrom.y,vbTarget.y,e),
      w:lerp(vbAnimFrom.w,vbTarget.w,e), h:lerp(vbAnimFrom.h,vbTarget.h,e)};
  applyViewBox(vb);
  vbAnimId=(t<1)?requestAnimationFrame(step):null;
 }
 vbAnimId=requestAnimationFrame(step);
}
function computeBBox(pts){
 if(!pts){
  pts=[];
  if(HOME) pts.push(HOME);
  for(const p of mapPoints.rx) pts.push(p);
  for(const p of mapPoints.qso) pts.push(p);
  if(mapPoints.tx) pts.push(mapPoints.tx);
 }
 if(!pts.length) return {x:0,y:0,w:MW,h:MH};
 let minX=Math.min.apply(null,pts.map(p=>p[0])), maxX=Math.max.apply(null,pts.map(p=>p[0]));
 let minY=Math.min.apply(null,pts.map(p=>p[1])), maxY=Math.max.apply(null,pts.map(p=>p[1]));
 let w=maxX-minX, h=maxY-minY;
 const padX=Math.max(w*0.15,10), padY=Math.max(h*0.15,6);
 minX-=padX; maxX+=padX; minY-=padY; maxY+=padY;
 w=maxX-minX; h=maxY-minY;
 if(w<MIN_VB_W){const cx=(minX+maxX)/2; minX=cx-MIN_VB_W/2; maxX=cx+MIN_VB_W/2; w=MIN_VB_W;}
 if(h<MIN_VB_H){const cy=(minY+maxY)/2; minY=cy-MIN_VB_H/2; maxY=cy+MIN_VB_H/2; h=MIN_VB_H;}
 const AR=MW/MH;
 if(w/h<AR){ const need=h*AR; const cx=(minX+maxX)/2; minX=cx-need/2; maxX=cx+need/2; w=need; }
 else if(w/h>AR){ const need=w/AR; const cy=(minY+maxY)/2; minY=cy-need/2; maxY=cy+need/2; h=need; }
 if(w>MW){w=MW;h=MH;}
 let x=minX, y=minY;
 if(x<0)x=0; if(y<0)y=0;
 if(x+w>MW)x=MW-w; if(y+h>MH)y=MH-h;
 return {x,y,w,h};
}
/* ---- neighbor auto-zoom: resolve the target's DXCC country name
   (callCountry(), already used for the CALLING cockpit item) against
   Natural Earth's admin-0 list to get an ISO2, then union that country's
   bbox with every bordering country's bbox (country_adjacency.json).
   DXCC entities that don't map onto a single Natural Earth country (Puerto
   Rico, Hawaii, Alaska, etc. -- distinct DXCC entities but not distinct
   Natural Earth political countries) gracefully resolve to null, falling
   back to the plain target-point framing below. ---- */
function resolveCountryIso2(dxccName, countries){
 if(!dxccName) return null;
 const hit=(countries||[]).find(c=>c.name===dxccName||c.admin===dxccName);
 return hit ? (hit.iso2||null) : null;
}
function unionBBox(boxes){
 let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
 for(const b of boxes){
  if(!b) continue;
  x0=Math.min(x0,b[0]); y0=Math.min(y0,b[1]); x1=Math.max(x1,b[2]); y1=Math.max(y1,b[3]);
 }
 return (x0===Infinity) ? null : [x0,y0,x1,y1];
}
function neighborZoomBBox(targetIso2, countriesByIso2, adjacency){
 if(!targetIso2) return null;
 const target=(countriesByIso2||{})[targetIso2];
 if(!target || !target.bbox) return null;
 const neighbors=(adjacency||{})[targetIso2]||[];
 const boxes=[target.bbox];
 for(const iso of neighbors){
  const n=(countriesByIso2||{})[iso];
  if(n && n.bbox) boxes.push(n.bbox);
 }
 return unionBBox(boxes);
}
/* ---- while calling/mid-QSO with a target, zoom tight to HOME + target +
   (when resolvable) the target's country and its neighbors, instead of the
   full heard/worked point cloud -- makes the beam/line to whoever we're
   actively pursuing the obvious focus, with real geographic context.
   renderTX() clears mapPoints.tx the moment state leaves calling/qso, so
   this naturally zooms back out to the full picture on its own once we
   move to the next target. ---- */
function computeTargetBBox(){
 const pts=[]; if(HOME) pts.push(HOME); if(mapPoints.tx) pts.push(mapPoints.tx);
 if(lastEngine && lastEngine.target){
  const iso2=resolveCountryIso2(callCountry(lastEngine.target), borderCountries);
  const nb=neighborZoomBBox(iso2, countriesByIso2, borderAdjacency);
  if(nb){ pts.push([nb[0],nb[1]]); pts.push([nb[2],nb[3]]); }
 }
 return computeBBox(pts.length?pts:null);
}
function updateMapZoom(){
 if(mapMode!=='auto') return;
 animateViewBoxTo(mapPoints.tx?computeTargetBBox():computeBBox());
}
function setMapMode(m){
 mapMode=m;
 document.getElementById('mapAuto').classList.toggle('active', m==='auto');
 document.getElementById('mapWorld').classList.toggle('active', m==='world');
 if(m==='world') animateViewBoxTo({x:0,y:0,w:MW,h:MH}); else updateMapZoom();
 scheduleSaveLayout();
}

/* ---- SNR floor risk meter: a lower (more negative) floor lets weaker CQs
   through, but weaker signals are less likely to hear our own QRP signal
   back (reciprocity) -- risk of no response rises as the floor drops.
   Linear over the practical FT8 decode range (-24..0 dB), clamped. ---- */
function snrRiskLevel(floorDb){
 const clamped=Math.max(-24,Math.min(0,floorDb));
 const pct=Math.round((-clamped/24)*100);
 let level,label;
 if(pct>=75){level='high';label='High risk of no response';}
 else if(pct>=45){level='moderate';label='Moderate risk of no response';}
 else if(pct>=20){level='low';label='Low risk of no response';}
 else{level='minimal';label='Minimal risk — strong signals only';}
 return {pct,level,label};
}
const SNR_RISK_COLORS={high:'#f85149',moderate:'#f0883e',low:'#56d4dd',minimal:'#3fb950'};
function updateSnrRiskUI(floorDb){
 const r=snrRiskLevel(floorDb);
 document.getElementById('snrFloorVal').textContent=floorDb+' dB';
 const fill=document.getElementById('snrRiskFill');
 fill.style.width=r.pct+'%'; fill.style.background=SNR_RISK_COLORS[r.level];
 document.getElementById('snrRiskLabel').textContent=r.label;
}

/* ---- country/state border lines: fetched once (not on every tick --
   static data, ~610KB combined) and rendered as plain SVG paths already
   projected into the map's 1000x500 space server-side, so no client-side
   geometry work is needed here. Fire-and-forget -- doesn't block the rest
   of page init, and a failed fetch just leaves the map without borders
   rather than breaking anything else. ---- */
let borderCountries=[], countriesByIso2={}, borderAdjacency={}, dishFlowerByIso2={};
async function loadBorders(){
 try{
  const [cr, sr, ar, dr] = await Promise.all(
   [fetch('/borders/countries'), fetch('/borders/states'), fetch('/borders/adjacency'), fetch('/borders/dish_flower')]);
  const countries = cr.ok ? await cr.json() : [];
  const states = sr.ok ? await sr.json() : [];
  borderAdjacency = ar.ok ? await ar.json() : {};
  dishFlowerByIso2 = dr.ok ? await dr.json() : {};
  borderCountries = countries;
  countriesByIso2 = {};
  for(const c of countries) if(c.iso2) countriesByIso2[c.iso2]=c;
  document.getElementById('countryBorders').innerHTML =
   countries.map(c=>`<path d="${c.path}" data-iso2="${esc(c.iso2||'')}" data-name="${esc(c.name||'')}"/>`).join('');
  document.getElementById('stateBorders').innerHTML =
   states.map(s=>`<path d="${s.path}"/>`).join('');
 }catch(e){}
}

/* ---- popup position: convert an SVG-space point (px,py, in the map's
   1000x500 coordinate space) to a fixed on-screen {left,top} that sits the
   popup ABOVE the point, horizontally centered on it, clamped to the
   viewport. Pure -- rect/vb/viewport size all passed in rather than read
   from window/DOM globals, so this is Node-harness testable. ---- */
function popupScreenPos(rect, vb, px, py, popupW, popupH, gap, viewportW, viewportH){
 const fracX=(px-vb.x)/vb.w, fracY=(py-vb.y)/vb.h;
 const anchorX=rect.left+fracX*rect.width, anchorY=rect.top+fracY*rect.height;
 let left=anchorX-popupW/2, top=anchorY-popupH-gap;
 left=Math.max(4,Math.min(viewportW-popupW-4,left));
 top=Math.max(4,top);
 return {left,top,anchorX,anchorY};
}
function closeCountryCard(){
 document.getElementById('countryCard').style.display='none';
}
/* ---- country info card: a small popup (not a dashboard-wide modal),
   opened by clicking any contact dot (map) or Logbook row, anchored just
   above that station's specific point on the map (its grid if known, else
   its country's bbox center). Shows flag/name/pop immediately (all
   offline data, already loaded via loadBorders()); locks the map zoom
   onto the country's bbox (falls back to leaving the map as-is if the
   call's DXCC name doesn't resolve to a Natural Earth country -- see
   resolveCountryIso2); fetches the QRZ photo asynchronously afterward so
   the rest of the card isn't blocked on a network round-trip. ---- */
async function openCountryCard(call, grid){
 if(!call) return;
 const iso2=resolveCountryIso2(callCountry(call), borderCountries);
 const country=iso2?countriesByIso2[iso2]:null;
 document.getElementById('ccTitle').textContent=country?country.name:(callCountry(call)||call);
 document.getElementById('ccCall').textContent=call;
 // real flag SVGs (bin/flags/, downloaded once from lipis/flag-icons) --
 // Unicode flag emoji don't reliably render on Linux/Chrome (missing
 // color-emoji font support shows boxes/letters instead), so this uses an
 // actual image with a graceful hide-on-missing fallback, not an emoji one.
 document.getElementById('ccFlag').innerHTML=iso2
  ?`<img src="/flags/${iso2.toLowerCase()}.svg" alt="${esc(iso2)}" onerror="this.style.display='none'">`
  :'';
 document.getElementById('ccPop').textContent=(country&&country.pop)
  ?country.pop.toLocaleString()+(country.pop_year?` (${country.pop_year})`:''):'—';
 const df=iso2?dishFlowerByIso2[iso2]:null;
 const dishRow=document.getElementById('ccDishRow'), flowerRow=document.getElementById('ccFlowerRow');
 if(df&&df.dish){ dishRow.style.display='flex'; document.getElementById('ccDish').textContent=df.dish; }
 else dishRow.style.display='none';
 if(df&&df.flower){ flowerRow.style.display='flex'; document.getElementById('ccFlower').textContent=df.flower; }
 else flowerRow.style.display='none';
 const photo=document.getElementById('ccPhoto'), photoStatus=document.getElementById('ccPhotoStatus');
 photo.style.display='none'; photo.removeAttribute('src'); photoStatus.textContent='loading photo…';
 const callBtn=document.getElementById('ccCallBtn');
 const isMe=CFG&&call===CFG.mycall;
 callBtn.style.display=isMe?'none':'inline-block';
 callBtn.dataset.call=call;
 const card=document.getElementById('countryCard');
 card.style.display='block';
 // lock the map: same manual-mode mechanism as drag/wheel (stops
 // updateMapZoom()'s auto-fit from immediately overriding this).
 mapMode='manual';
 document.getElementById('mapAuto').classList.remove('active');
 document.getElementById('mapWorld').classList.remove('active');
 if(country&&country.bbox){
  const [x0,y0,x1,y1]=country.bbox;
  animateViewBoxTo(computeBBox([[x0,y0],[x1,y1]]));
 }
 // anchor the popup above the specific point: the station's own grid if
 // known, else the country's bbox center, else leave the popup wherever
 // it last was (better than vanishing).
 const anchorLL=grid?grid2ll(grid):null;
 const anchorPt=anchorLL?ll2xy(anchorLL):(country&&country.bbox
  ?[(country.bbox[0]+country.bbox[2])/2,(country.bbox[1]+country.bbox[3])/2]:null);
 if(anchorPt){
  const rect=document.getElementById('map').getBoundingClientRect();
  const box=document.getElementById('countryCardBox');
  const bw=box.getBoundingClientRect().width||300, bh=box.getBoundingClientRect().height||140;
  const pos=popupScreenPos(rect, vbTarget, anchorPt[0], anchorPt[1], bw, bh, 14, window.innerWidth, window.innerHeight);
  card.style.left=pos.left+'px';
  card.style.top=pos.top+'px';
 }
 try{
  const r=await fetch('/qrz/lookup?call='+encodeURIComponent(call));
  const j=await r.json();
  if(j.ok&&j.fields&&j.fields.image){
   photo.src=j.fields.image; photo.style.display='block'; photoStatus.textContent='';
  }else if(!j.configured){
   photoStatus.textContent='QRZ XML lookup not configured';
  }else if(!j.ok){
   photoStatus.textContent='photo lookup failed: '+(j.error||'unknown error');
  }else{
   photoStatus.textContent='no photo on file';
  }
 }catch(e){ photoStatus.textContent=''; }
}

async function loadCfg(){
 try{
  const r=await fetch('/config'); if(!r.ok) return; CFG=await r.json();
  const ll=grid2ll(CFG.mygrid); if(ll) HOME=ll2xy(ll);
  if(HOME) document.getElementById('home').innerHTML=
   `<circle class=dot-home cx="${HOME[0]}" cy="${HOME[1]}" fill="#e3b341" stroke="#0d1117" stroke-width="1" vector-effect="non-scaling-stroke"><title>${esc(CFG.mycall)} — home</title></circle>`+
   `<text x="${HOME[0]+7}" y="${HOME[1]+4}" class=mlabel fill="#e3b341">${esc(CFG.mycall)}</text>`;
  updateMapZoom();
  document.getElementById('cpBand').textContent=CFG.band||'—';
  const mhz=CFG.dial_hz?(CFG.dial_hz/1e6).toFixed(3)+' MHz':'—';
  const items=[['CALL',CFG.mycall],['GRID',CFG.mygrid],['BAND',CFG.band||'—'],
               ['DIAL',mhz],['POWER',(CFG.tx_pwr||'—')+' W'],['MODE',CFG.mode]];
  document.getElementById('info').innerHTML=items.map(i=>
   `<span class=it><span class=k>${i[0]}</span><span class=v>${esc(String(i[1]))}</span></span>`).join('');
  if(!snrFloorInitialized && CFG.snr_floor_default!=null){
   document.getElementById('snrFloorSlider').value=CFG.snr_floor_default;
   updateSnrRiskUI(CFG.snr_floor_default);
   snrFloorInitialized=true;
  }
 }catch(e){}
}

/* ---- Station config widget: antenna CRUD + band/wattage selection, LOCKED
   to the server's BANDS table and each antenna's own max_watts — this page
   never lets the operator type a raw Hz or an unbounded watt value. Saving
   only writes station.conf; it never touches the CAT port (no retune, no
   TX) — see /action/station/set's docstring in dashboard.py. ---- */
let ANTENNAS=[], BANDS_CACHE=[];
function bandLabel(b){
 return `${b.name} — ${(b.freq_hz/1e6).toFixed(3)} MHz (FT8)`+(b.cap_w?` [legal cap ${b.cap_w} W]`:'');
}
async function loadBands(){
 try{ const r=await fetch('/bands'); if(r.ok) BANDS_CACHE=await r.json(); }catch(e){}
}
function currentAntenna(){
 return ANTENNAS.find(a=>a.id===document.getElementById('antSelect').value);
}
function refreshBandOptions(){
 const a=currentAntenna(), sel=document.getElementById('bandSelect');
 const want=CFG.band||sel.value;
 const opts=BANDS_CACHE.filter(b=>!a||a.bands.includes(b.name));
 sel.innerHTML=opts.map(b=>`<option value="${b.name}">${bandLabel(b)}</option>`).join('');
 if(want && opts.some(b=>b.name===want)) sel.value=want;
}
function refreshPwrOptions(){
 const a=currentAntenna();
 const band=BANDS_CACHE.find(b=>b.name===document.getElementById('bandSelect').value);
 let cap=(a&&a.max_watts)?a.max_watts:__DEFAULT_MAX_W__;
 if(band&&band.cap_w) cap=Math.min(cap,band.cap_w);
 const steps=[1,2,5,10,15,20,25,30,50,75,100,150,200,300,500,1000,1500].filter(w=>w<=cap);
 if(!steps.length) steps.push(cap);
 const sel=document.getElementById('pwrSelect');
 const want=parseFloat(CFG.tx_pwr)||parseFloat(sel.value);
 sel.innerHTML=steps.map(w=>`<option value="${w}">${w} W</option>`).join('');
 if(steps.includes(want)) sel.value=want; else sel.value=steps[steps.length-1];
}
function onAntennaChange(){
 refreshBandOptions(); refreshPwrOptions();
 const a=currentAntenna();
 document.getElementById('antName').value=a?a.name:'';
 document.getElementById('antMaxW').value=(a&&a.max_watts)?a.max_watts:'';
 document.getElementById('antNotes').value=(a&&a.notes)?a.notes:'';
 document.querySelectorAll('#antBandsRow input[type=checkbox]').forEach(cb=>{
  cb.checked=!!(a&&a.bands.includes(cb.value));
 });
}
function buildAntBandsRow(){
 document.getElementById('antBandsRow').innerHTML=BANDS_CACHE.map(b=>
  `<label class=dim style="margin-right:8px"><input type=checkbox value="${b.name}"> ${b.name}</label>`).join('');
}
async function loadAntennas(preserveSel){
 try{
  const r=await fetch('/antennas'); if(!r.ok) return;
  ANTENNAS=await r.json();
  const sel=document.getElementById('antSelect');
  const want=preserveSel||CFG.antenna||sel.value||(ANTENNAS[0]&&ANTENNAS[0].id);
  sel.innerHTML=ANTENNAS.map(a=>
   `<option value="${a.id}">${esc(a.name)}${a.max_watts?` (max ${a.max_watts} W)`:' (max W unconfirmed)'}</option>`).join('');
  if(want && ANTENNAS.some(a=>a.id===want)) sel.value=want;
  onAntennaChange();
 }catch(e){}
}
function wireStationCfg(){
 document.getElementById('antSelect').addEventListener('change',onAntennaChange);
 document.getElementById('bandSelect').addEventListener('change',refreshPwrOptions);
 document.getElementById('stationSaveBtn').addEventListener('click',async()=>{
  const antenna_id=document.getElementById('antSelect').value;
  const band=document.getElementById('bandSelect').value;
  const tx_pwr=parseFloat(document.getElementById('pwrSelect').value);
  const msg=document.getElementById('stationMsg');
  if(!antenna_id||!band){ msg.textContent='pick an antenna and band first'; return; }
  msg.textContent='saving…';
  const r=await postAction('/action/station/set',{antenna_id,band,tx_pwr});
  msg.textContent=r.ok?(r.body.note||'saved'):('save failed: '+(r.body.error||r.error||r.status));
  if(r.ok){ CFG.antenna=antenna_id; CFG.band=band; CFG.tx_pwr=String(tx_pwr); CFG.dial_hz=r.body.dial_hz; loadCfg(); }
 });
 function antFields(){
  return {
   name: document.getElementById('antName').value.trim(),
   bands: [...document.querySelectorAll('#antBandsRow input[type=checkbox]:checked')].map(c=>c.value),
   max_watts: document.getElementById('antMaxW').value===''?null:parseFloat(document.getElementById('antMaxW').value),
   notes: document.getElementById('antNotes').value.trim(),
  };
 }
 document.getElementById('antAddBtn').addEventListener('click',async()=>{
  const f=antFields(), msg=document.getElementById('antMsg');
  if(!f.name||!f.bands.length){ msg.textContent='name and at least one band required'; return; }
  const r=await postAction('/action/antenna/add',f);
  msg.textContent=r.ok?'added':'add failed: '+(r.body.error||r.error||r.status);
  if(r.ok) loadAntennas(r.body.antenna.id);
 });
 document.getElementById('antUpdateBtn').addEventListener('click',async()=>{
  const id=document.getElementById('antSelect').value, f=antFields(), msg=document.getElementById('antMsg');
  if(!id){ msg.textContent='select an antenna first'; return; }
  if(!f.name||!f.bands.length){ msg.textContent='name and at least one band required'; return; }
  const r=await postAction('/action/antenna/update',{id,...f});
  msg.textContent=r.ok?'updated':'update failed: '+(r.body.error||r.error||r.status);
  if(r.ok) loadAntennas(id);
 });
 document.getElementById('antRemoveBtn').addEventListener('click',async()=>{
  const id=document.getElementById('antSelect').value, msg=document.getElementById('antMsg');
  if(!id){ msg.textContent='select an antenna first'; return; }
  const r=await postAction('/action/antenna/remove',{id});
  msg.textContent=r.ok?('removed'+(r.body.was_active?' (was the active antenna — pick a new one and Save)':'')):
   'remove failed: '+(r.body.error||r.error||r.status);
  if(r.ok) loadAntennas();
 });
}

/* ---- QRZ Logbook widget: status is read-only/local (never shows the key
   itself), the actual sync runs as a detached background process (spawned
   server-side) since this server handles one request at a time and a real
   sync is a sequence of blocking HTTPS calls to QRZ -- kicking it off just
   starts the process; polling picks up progress via the same /qrz/status
   endpoint everything else here already uses that pattern for. ---- */
/* ---- QRZ auto sync & upload scheduling math -- pure, unit tested in
   tools/test_dashboard_js.py (same Node-subprocess technique as
   callCountry()). A single heartbeat (not two independent setInterval
   timers) re-evaluates "is this job due" against wall-clock elapsed time
   every QRZ_AUTO_HEARTBEAT_MS, so a backgrounded/throttled tab can't drift
   the two jobs out of their documented 1-min-apart, 2-min-repeat cadence
   the way two independent long-period setIntervals could. Client-side only
   -- runs while this browser tab stays open, no server-side cron. ---- */
const QRZ_AUTO_PERIOD_MS=120000, QRZ_AUTO_STAGGER_MS=60000, QRZ_AUTO_HEARTBEAT_MS=5000;
function qrzJobDue(elapsedMs, periodMs, offsetMs, lastFireMs){
 if(elapsedMs<offsetMs) return false;
 if(lastFireMs===null) return true;
 return (elapsedMs-lastFireMs)>=periodMs;
}
let qrzAutoArmedAt=null, qrzAutoHeartbeat=null, qrzAutoLastSync=null, qrzAutoLastRefresh=null, qrzAutoInitPending=true;
function qrzAutoSetStatus(t){ const el=document.getElementById('qrzAutoStatus'); if(el) el.textContent=t; }
async function qrzAutoTick(){
 if(qrzAutoArmedAt===null) return;
 const elapsed=Date.now()-qrzAutoArmedAt;
 if(qrzJobDue(elapsed,QRZ_AUTO_PERIOD_MS,0,qrzAutoLastSync)){
  qrzAutoLastSync=elapsed;
  const r=await postAction('/action/qrz/sync',{});
  qrzAutoSetStatus((r.ok?'auto sync started':(r.status===409?'auto sync skipped (already running)':
   'auto sync failed: '+(r.body.error||r.error||r.status)))+' — '+new Date().toLocaleTimeString());
  loadQrzStatus();
 }
 if(qrzJobDue(elapsed,QRZ_AUTO_PERIOD_MS,QRZ_AUTO_STAGGER_MS,qrzAutoLastRefresh)){
  qrzAutoLastRefresh=elapsed;
  const r=await postAction('/action/qrz/refresh',{});
  qrzAutoSetStatus((r.ok?'auto refresh started':(r.status===409?'auto refresh skipped (already running)':
   'auto refresh failed: '+(r.body.error||r.error||r.status)))+' — '+new Date().toLocaleTimeString());
 }
}
function qrzAutoArm(){
 qrzAutoArmedAt=Date.now(); qrzAutoLastSync=null; qrzAutoLastRefresh=null;
 if(!qrzAutoHeartbeat) qrzAutoHeartbeat=setInterval(qrzAutoTick,QRZ_AUTO_HEARTBEAT_MS);
 qrzAutoTick();
 try{localStorage.setItem('coa-qrz-auto','1');}catch(e){}
}
function qrzAutoDisarm(){
 qrzAutoArmedAt=null;
 if(qrzAutoHeartbeat){ clearInterval(qrzAutoHeartbeat); qrzAutoHeartbeat=null; }
 qrzAutoSetStatus('');
 try{localStorage.removeItem('coa-qrz-auto');}catch(e){}
}
let qrzSyncPolling=null;
async function loadQrzStatus(){
 try{
  const r=await fetch('/qrz/status?t='+Date.now()); if(!r.ok) return;
  const s=await r.json();
  document.getElementById('qrzConfigured').textContent=s.configured?'key on file':'no key yet';
  document.getElementById('qrzSetupMsg').style.display=s.configured?'none':'block';
  document.getElementById('qrzPending').textContent=s.pending;
  document.getElementById('qrzSyncing').textContent=s.syncing?'running…':'idle';
  const log=document.getElementById('qrzLog');
  log.textContent=(s.log_tail&&s.log_tail.length)?s.log_tail.join('\\n'):'no syncs yet';
  const btn=document.getElementById('qrzSyncBtn');
  btn.disabled=s.syncing||!s.configured;
  // Auto sync & upload toggle: disabled without a key on file; force-disarm
  // if a key that WAS configured disappears mid-session. On the first status
  // load that confirms a key is on file, restore an armed state left over
  // from a previous page load (see qrzAutoArm()'s localStorage write).
  const autoCk=document.getElementById('qrzAutoToggle');
  autoCk.disabled=!s.configured;
  document.getElementById('qrzAutoLabel').title=s.configured
   ? 'every 2 min, alternating sync then refresh, staggered 1 min apart -- runs only while this dashboard tab stays open, no server-side schedule'
   : 'requires a QRZ API key on file first -- see the setup note above';
  if(!s.configured && qrzAutoArmedAt!==null){ autoCk.checked=false; qrzAutoDisarm(); }
  if(qrzAutoInitPending && s.configured){
   qrzAutoInitPending=false;
   try{
    if(localStorage.getItem('coa-qrz-auto')==='1'){ autoCk.checked=true; qrzAutoArm(); }
   }catch(e){}
  }
  if(s.syncing && !qrzSyncPolling){
   qrzSyncPolling=setInterval(loadQrzStatus,2000);
  }else if(!s.syncing && qrzSyncPolling){
   clearInterval(qrzSyncPolling); qrzSyncPolling=null;
  }
 }catch(e){}
}
function wireQrz(){
 document.getElementById('qrzAutoToggle').addEventListener('change',(e)=>{
  if(e.target.checked) qrzAutoArm(); else qrzAutoDisarm();
 });
 document.getElementById('qrzSyncBtn').addEventListener('click',async()=>{
  const msg=document.getElementById('qrzMsg');
  msg.textContent='starting sync…';
  const r=await postAction('/action/qrz/sync',{});
  msg.textContent=r.ok?'sync started':'sync failed: '+(r.body.error||r.error||r.status);
  loadQrzStatus();
 });
 document.getElementById('lbRefreshBtn').addEventListener('click',async()=>{
  const btn=document.getElementById('lbRefreshBtn');
  btn.disabled=true; btn.textContent='Refreshing…';
  const r=await postAction('/action/qrz/refresh',{});
  if(!r.ok){
   btn.disabled=false; btn.textContent='Refresh from QRZ';
   document.getElementById('lbSummary').textContent='refresh failed: '+(r.body.error||r.error||r.status);
   return;
  }
  // poll until the fetch process exits, then re-render the merged table
  const iv=setInterval(async()=>{
   try{
    const s=await (await fetch('/qrz/status?t='+Date.now())).json();
    if(!s.fetching){
     clearInterval(iv);
     btn.disabled=false; btn.textContent='Refresh from QRZ';
     loadLogbook(); loadQrzStatus();
    }
   }catch(e){}
  },1500);
 });
}

/* ---- Logbook widget: every local QSO with its QRZ standing, newest
   first. Server does the matching (bin/logbook.py, ±30 min tolerance --
   QRZ's own documented confirmation window); this just renders rows. ---- */
const LB_MARKS={confirmed:['✔ confirmed','lb-confirmed'],
                uploaded:['↑ uploaded','lb-uploaded'],
                'not synced':['— not synced','lb-notsynced']};
async function loadLogbook(){
 try{
  const r=await fetch('/logbook?t='+Date.now()); if(!r.ok) return;
  const d=await r.json();
  let h='<tr><th>UTC</th><th>call</th><th>country</th><th>grid</th><th>band</th><th>sent</th><th>rcvd</th><th>QRZ</th></tr>';
  for(const row of d.rows||[]){
   const t=row.time?`${row.time.slice(0,2)}:${row.time.slice(2,4)}`:'';
   const dte=row.date?`${row.date.slice(4,6)}-${row.date.slice(6,8)}`:'';
   const [label,cls]=LB_MARKS[row.qrz]||[row.qrz,''];
   // country: derived client-side from the callsign (same DXCC prefix
   // table as the map/cockpit), not a server field -- Logan asked for
   // this as a local-only column.
   const country=callCountry(row.call)||'—';
   h+=`<tr class=lbRow data-call="${esc(row.call)}" data-grid="${esc(row.grid||'')}"><td>${esc(dte)} ${esc(t)}</td><td>${esc(row.call)}</td><td>${esc(country)}</td><td>${esc(row.grid)}</td>`+
      `<td>${esc(row.band)}</td><td>${esc(row.sent)}</td><td>${esc(row.rcvd)}</td>`+
      `<td class="${cls}">${esc(label)}</td></tr>`;
  }
  document.getElementById('lbTable').innerHTML=h;
  const n=(d.rows||[]).length, c=(d.rows||[]).filter(r=>r.qrz==='confirmed').length;
  document.getElementById('lbSummary').textContent=
   `${n} QSO(s) · ${c} confirmed · QRZ book: ${d.qrz_count}`+
   (d.fetched_at?` (fetched ${d.fetched_at.slice(11,16)}Z)`:' (never fetched)');
 }catch(e){}
}

function renderRX(s){
 if(!HOME) return;
 const seen={};                            // dedupe by callsign, keep newest
 function add(call,grid,t){
  if(!call||call.length<3||call.includes('<')||call===(CFG&&CFG.mycall)) return;
  if(!isGrid(grid)) return;
  if(!(call in seen)||t>seen[call].t) seen[call]={g:grid,t:t};
 }
 for(const d of s.recent||[]){
  const tk=d.msg.trim().split(/\\s+/);
  if(tk.length>=2&&isGrid(tk[tk.length-1])) add(tk[tk.length-2],tk[tk.length-1],decodeTime(d.date,d.slot));
 }
 const today=new Date().toISOString().slice(2,10).replace(/-/g,'');
 for(const c of s.candidates||[]) if(c.grid&&c.slot) add(c.call,c.grid,decodeTime(today,c.slot));
 let h=''; const pts=[];
 for(const call in seen){
  const e=seen[call], age=(Date.now()-e.t)/1000;
  if(age>900) continue;                    // keep ~15 min
  const ll=grid2ll(e.g); if(!ll) continue;
  const [x,y]=ll2xy(ll), op=Math.max(.15,1-age/900);
  pts.push([x,y]);
  h+=`<line x1="${HOME[0]}" y1="${HOME[1]}" x2="${x}" y2="${y}" stroke="#56d4dd" stroke-width="0.6" opacity="${(op*.45).toFixed(2)}" vector-effect="non-scaling-stroke"/>`;
  h+=`<circle class=dot-rx cx="${x}" cy="${y}" fill="#56d4dd" opacity="${op.toFixed(2)}" data-call="${esc(call)}" data-grid="${esc(e.g)}"><title>${esc(call)} ${esc(e.g)} — click for details</title></circle>`;
 }
 document.getElementById('rx').innerHTML=h;
 mapPoints.rx=pts;
 const grids={};
 for(const call in seen) grids[call]=seen[call].g;
 recentGridByCall=grids;
 updateMapZoom();
}
/* ---- completed QSOs this session: persistent green lines, unlike the
   fading cyan "heard" traffic above — these are confirmed contacts, the
   actual thing being gathered, so they never fade/expire within a session. ---- */
function renderQSOs(s){
 if(!HOME) return;
 let h=''; const pts=[];
 for(const q of s.qsos||[]){
  if(!q.grid||!isGrid(q.grid)) continue;
  const ll=grid2ll(q.grid); if(!ll) continue;
  const [x,y]=ll2xy(ll);
  pts.push([x,y]);
  h+=`<line x1="${HOME[0]}" y1="${HOME[1]}" x2="${x}" y2="${y}" stroke="#3fb950" stroke-width="1.1" opacity="0.7" vector-effect="non-scaling-stroke"/>`;
  h+=`<circle class=dot-qso cx="${x}" cy="${y}" fill="#3fb950" stroke="#0d1117" stroke-width="0.6" vector-effect="non-scaling-stroke" data-call="${esc(q.call||'')}" data-grid="${esc(q.grid||'')}"><title>${esc(q.call||'')} ${esc(q.grid||'')}${q.band?' — '+esc(q.band):''} — QSO'd</title></circle>`;
  h+=`<text x="${x+6}" y="${y-6}" class=mlabel fill="#3fb950">${esc(q.call||'')}</text>`;
 }
 document.getElementById('qso').innerHTML=h;
 mapPoints.qso=pts;
 updateMapZoom();
}
/* ---- gate for the TX line: engine.json is a snapshot that's never reset
   when the chaser exits (see the STATE_LABELS comment above engTick), so a
   killed/finished run can leave a stale 'calling'/'qso' state -- and a
   stale red line -- on the map forever. Must agree with chaserRunning, not
   just engine.json's own state field. ---- */
function txLineActive(e, chaserRunning){
 return !!(chaserRunning && e && (e.state==='calling'||e.state==='qso') && e.target);
}
/* ---- grid to plot the TX line to: prefer engine.json's own grid (from the
   CQ we answered), fall back to any grid recently heard for the same call
   elsewhere (recentGridByCall, from renderRX's decode scan) rather than
   vanishing the line just because this particular CQ omitted its grid. ---- */
function resolveTargetGrid(target, engineGrid, recentGridByCall){
 if(engineGrid && isGrid(engineGrid)) return engineGrid;
 const g=(recentGridByCall||{})[target];
 return (g && isGrid(g)) ? g : (engineGrid||'');
}
function renderTX(e, chaserRunning){
 const g=document.getElementById('tx');
 if(!txLineActive(e,chaserRunning)||!HOME){g.innerHTML='';mapPoints.tx=null;updateMapZoom();return;}
 const grid=resolveTargetGrid(e.target,e.grid,recentGridByCall);
 const ll=targetLatLon(e.target,grid); if(!ll){g.innerHTML='';mapPoints.tx=null;updateMapZoom();return;}
 const [x2,y2]=ll2xy(ll), [x1,y1]=HOME;
 mapPoints.tx=[x2,y2];
 const bow=Math.min(80,Math.hypot(x2-x1,y2-y1)*0.25)+8;   // quadratic, bowed poleward
 const d=`M${x1} ${y1} Q${(x1+x2)/2} ${(y1+y2)/2-bow} ${x2} ${y2}`;
 let h='';
 if(e.tx){
  h+=`<path d="${d}" fill="none" stroke="#f85149" stroke-width="10" opacity="0.22" vector-effect="non-scaling-stroke"/>`;
  h+=`<path d="${d}" fill="none" stroke="#f85149" stroke-width="3.2" stroke-dasharray="10 7" class=txflow vector-effect="non-scaling-stroke"/>`;
 }else{
  h+=`<path d="${d}" fill="none" stroke="#f85149" stroke-width="2.4" opacity="0.5" vector-effect="non-scaling-stroke"/>`;
 }
 h+=`<circle class=dot-tx cx="${x2}" cy="${y2}" fill="#f85149" data-call="${esc(e.target||'')}" data-grid="${esc(grid)}"><title>${esc(e.target||'')} — ${e.tx?'transmitting':'calling'}</title></circle>`;
 h+=`<text x="${x2+6}" y="${y2-6}" class=mlabel fill="#f85149">${esc(e.target||'')}</text>`;
 g.innerHTML=h;
 updateMapZoom();
}
let lastEngine=null, lastTxFlag=false, sawTxContent=false;

/* ---- NEXT TX cockpit countdown: called from engTick (fresh fetch) AND from
   a fast local timer (cached lastEngine) so the countdown ticks smoothly
   between the 2 s polls without hitting the server any harder. ---- */
/* ---- rough time-to-next-call: FT8's 15 s slot cadence is fully
   clock-deterministic, so this needs no engine/server data at all -- pure
   function of wall-clock time, extracted for Node-harness testing. Used
   only as a fallback estimate (updateNextTx's final else) while Automatic
   CQ is running but no real next_tx_epoch is scheduled yet. ---- */
function secsToNextSlot(nowEpochSec){
 return 15 - (nowEpochSec % 15);
}
/* ---- rough-branch label: above 5s remaining, a dim estimate; that ends at
   5s (nothing meaningful to claim yet about an imminent TX); from 3s down,
   an urgent countdown in the same tx-soon styling as a real scheduled
   key-up. Pure, extracted for Node-harness testing. ---- */
function roughTxLabel(secs){
 if(secs>5) return {text:'~'+secs.toFixed(1)+'s to next slot', cls:'tx-rough'};
 if(secs<=3) return {text:'Transmitting in '+secs.toFixed(2)+'s', cls:'tx-soon'};
 return {text:'—', cls:''};
}
function updateNextTx(e, tx, st){
 const el=document.getElementById('cpNextTx');
 el.className='cpv';
 if(tx){
  el.textContent='ON AIR'; el.classList.add('tx-live');
 }else if(st==='tx_abort'){
  el.textContent='TX ABORT'; el.classList.add('tx-abort');
 }else if(e && e.next_tx_epoch){
  const secs=e.next_tx_epoch-(Date.now()/1000);
  if(secs>-5){                             // stale/unknown beyond a few seconds past
   el.textContent=secs>0?('TX in '+secs.toFixed(1)+'s'):'KEYING…';
   el.classList.add('tx-soon');
  }else el.textContent='—';
 }else if(chaserRunning){
  // no target locked in yet, just the next FT8 slot boundary -- rough
  // estimate while there's time to spare, urgent countdown once close
  // (see roughTxLabel).
  const secs=secsToNextSlot(Date.now()/1000);
  const r=roughTxLabel(secs);
  el.textContent=r.text;
  if(r.cls) el.classList.add(r.cls);
 }else el.textContent='—';
}
function nextTxFastTick(){ if(lastEngine) updateNextTx(lastEngine, !!lastEngine.tx, lastEngine.state||''); }

/* ---- TX transparency panel: exact message + spectrogram actually keyed.
   tx_msg/tx_offset are set BEFORE key-up (so the countdown window already
   previews what's about to go out) and stay put until the next attempt —
   deliberately separate from "msg", which doubles as the abort-reason text
   and would otherwise show stale reasons here as if they were content. The
   image reloads only the first time we see any TX content, and again each
   time a new transmission starts. No in-page audio playback -- the laptop's
   USB audio interface is already claimed by the live TX/RX chain, so
   browser playback through the same device is unreliable; the raw
   recording is still saved server-side at /tx.wav for offline debugging,
   just not wired up as an in-page <audio> control. ---- */
function updateTxPanel(e, tx, st){
 const msgEl=document.getElementById('txMsg'), subEl=document.getElementById('txPanelSub'),
       wfEl=document.getElementById('txwf'),
       abortEl=document.getElementById('txAbortMsg');
 const hasContent=!!(e && e.tx_msg);
 if(hasContent){
  msgEl.textContent=e.tx_msg+(e.tx_offset!=null?` @ ${e.tx_offset} Hz`:'');
  msgEl.className=tx?'tx-live':'';
  subEl.textContent=tx?'TRANSMITTING NOW':'last TX this session';
  wfEl.style.display='block';
  if(!sawTxContent || (tx && !lastTxFlag)){
   wfEl.src='/tx_waterfall.png?t='+Date.now();
  }
  sawTxContent=true;
 }
 lastTxFlag=tx;
 if(st==='tx_abort' && e && e.msg){
  abortEl.style.display='block'; abortEl.textContent='⚠ '+e.msg;
 }else abortEl.style.display='none';
}

/* ---- cockpit STATE labels: engine.json is a snapshot that's never reset
   when the chaser exits, so a killed/finished run can leave a stale state
   (e.g. "hunting") on disk forever. Never trust it without first checking
   the chaser process is actually alive (chaserRunning, from
   refreshActionsState's /actions/state poll) — otherwise force IDLE. ---- */
const STATE_LABELS={hunting:'AUTO-CQ',calling:'CALLING',qso:'QSO',tx_abort:'TX ABORT',
 done:'DONE',logged:'LOGGED',breather:'BREATHER'};
/* ---- QSO STEP: qso.py's own inner state machine (call -> rrpt -> b73 ->
   done), mirrored via engine.json's qso_step field -- a real 1-of-4 count
   of exactly how far the current exchange has gotten, not a guess. ---- */
const QSO_STEPS={call:{n:1,label:'calling'},rrpt:{n:2,label:'exchanging report'},
 b73:{n:3,label:'confirmed — sending 73'}};
const QSO_STEP_TOTAL=4;
async function engTick(){
 let e=null;
 try{
  const r=await fetch('/engine.json?t='+Date.now());
  e=r.ok?await r.json():null;
 }catch(err){}
 lastEngine=e;
 renderTX(e,chaserRunning);
 const st=(e&&e.state)||'';
 const cp=document.getElementById('cpState');
 if(chaserRunning){
  cp.textContent=STATE_LABELS[st]||(st?st.toUpperCase():'AUTO-CQ');
  cp.className='cpv st-'+st.toLowerCase().replace(/[^a-z]/g,'');
 }else{
  cp.textContent='IDLE';
  cp.className='cpv st-idle';
 }
 const tx=!!(e&&e.tx);
 cp.classList.toggle('tx-live',tx);
 document.getElementById('btnUnkey').classList.toggle('live',tx);
 // new country flash: gated on body.dx-armed, same source of truth the
 // blue glow layer uses; shouldFlashNewCountry does the actual edge-trigger.
 if(document.body.classList.contains('dx-armed') && shouldFlashNewCountry(e,chaserRunning,tx,lastNewCountryTx)){
  triggerNewCountryFlash(e.target, callCountry(e.target));
 }
 lastNewCountryTx=tx;
 updateNextTx(e,tx,st);
 updateTxPanel(e,tx,st);
 // CALLING cockpit item: where the current target actually is -- US state
 // (from their grid) for domestic contacts, country for everyone else.
 // Orange while pursuing, upgrades to pulsing red only when tx===true --
 // red is reserved for "actually on air right now" everywhere in this UI.
 const callingEl=document.getElementById('cpCalling');
 const pursuing=chaserRunning && (st==='calling'||st==='qso');
 if(pursuing && e&&e.target){
  callingEl.textContent=callLocation(e.target,e.grid)||e.target;
 }else{
  callingEl.textContent='—';
 }
 callingEl.classList.toggle('tx-live',tx);
 const stepEl=document.getElementById('cpQsoStep');
 const step=pursuing && e && QSO_STEPS[e.qso_step];
 if(step){
  stepEl.textContent=`${step.n} of ${QSO_STEP_TOTAL} — ${step.label} (call ${(e.msg_tx_count||0)+1} of ${(CFG&&CFG.max_repeat)||6})`;
  stepEl.classList.add('active');
 }else{
  stepEl.textContent='—';
  stepEl.classList.remove('active');
 }
 /* ---- alerts (4.3): chase ended / watchdog-abort — edge-triggered off
    engine.json's state field so a steady state never re-fires ---- */
 const stl=st.toLowerCase();
 if(stl && stl!==lastEngineState){
  if(stl==='done' || stl==='ended'){
   fireAlert('Automatic CQ ended', `state: ${st}`+(e&&e.target?` (last target ${e.target})`:''));
  }else if(stl.includes('abort') || stl.includes('watchdog')){
   fireAlert('Watchdog/abort', `engine state: ${st}`+(e&&e.msg?` — ${e.msg}`:''));
  }
 }
 lastEngineState=stl||lastEngineState;
}

/* ---- New country flash (DX Mode only): edge-triggered off an ACTUAL
   transmission toward a new-country target, sourced from engine.json (the
   live target), never from the passive candidate list -- that version
   re-flashed whatever was last shown immediately on every page reload,
   since its dedup list was in-memory only. Fires once per real TX start
   ("each call to it"); stops the moment the target leaves calling/qso
   (failed, or logged -- "QSO'd fully" is no longer new). Pure decision fn,
   factored out for Node-harness testing (tools/test_dashboard_js.py). ---- */
function shouldFlashNewCountry(e, chaserRunning, tx, lastTx){
 return !!(chaserRunning && e && e.new_country && e.target &&
           (e.state==='calling'||e.state==='qso') && tx && !lastTx);
}
let lastNewCountryTx=false, newCountryGlowTimer=null, newCountryBannerTimer=null;
function triggerNewCountryFlash(call, country){
 const glow=document.getElementById('newCountryGlow');
 glow.classList.remove('flash'); void glow.offsetWidth;
 glow.classList.add('flash');
 clearTimeout(newCountryGlowTimer);
 newCountryGlowTimer=setTimeout(()=>glow.classList.remove('flash'), 3000);
 document.getElementById('newCountryBannerBody').textContent=`${call} — ${country}`;
 document.getElementById('newCountryBanner').classList.add('show');
 clearTimeout(newCountryBannerTimer);
 newCountryBannerTimer=setTimeout(()=>document.getElementById('newCountryBanner').classList.remove('show'), 5000);
 fireAlert('New country', `${call} — ${country}`);
}
async function tick(){
 try{
  const r=await fetch('/status.json?t='+Date.now()); const s=await r.json();
  document.getElementById('wf').src='/waterfall.png?t='+Date.now();
  document.getElementById('upd').textContent=' updated '+s.updated_utc+'Z, slot '+s.slot+' ('+s.slot_decodes+' decodes)';
  const age=(Date.now()/1000)-(Date.parse(s.updated_utc+'Z')/1000);
  document.getElementById('stale').style.display=age>60?'inline':'none';
  /* ---- alerts (4.3): decode silence >3 min while rx-loop is running ---- */
  if(age>180 && lastRxRunning){
   if(!lastSilenceFlag){ fireAlert('Decode silence', `no new decodes for ${Math.round(age/60)} min — check band/audio`); lastSilenceFlag=true; }
  }else{
   lastSilenceFlag=false;
  }
  let h='<tr><th>slot</th><th>SNR</th><th>DT</th><th>Hz</th><th>message</th></tr>';
  for(const d of [...s.recent].reverse()){
   const cls=d.msg.startsWith('CQ')?'cq':(d.msg.includes('__MYCALL__')?'me':'');
   h+=`<tr class="${cls}"><td>${d.slot}</td><td class="${d.snr>=-12?'snr-good':'snr-bad'}">${d.snr}</td><td>${d.dt}</td><td>${d.freq}</td><td>${d.msg}</td></tr>`;}
  document.getElementById('dec').innerHTML=h;
  if(s.next_call){
   document.getElementById('next').innerHTML=
    `<button class="callchip callchip-main" data-call="${esc(s.next_call.call)}">${esc(s.next_call.call)} ${esc(s.next_call.grid)} (${s.next_call.snr} dB)</button>`;
   document.getElementById('cpNext').textContent=s.next_call.call;
  }else{
   document.getElementById('next').textContent='—';
   document.getElementById('cpNext').textContent='—';
  }
  document.getElementById('cand').innerHTML=s.candidates&&s.candidates.length>1
   ?'also: '+s.candidates.slice(1).map(c=>`<button class=callchip data-call="${esc(c.call)}">${esc(c.call)} ${c.snr}dB</button>`).join(' ')
   :'';
  document.getElementById('me').innerHTML=s.calling_me&&s.calling_me.length?s.calling_me.map(d=>`<span class=me>${d.msg} (${d.snr} dB)</span>`).join('<br>'):'nobody yet';
  /* ---- alerts (4.3): new QSO logged (qso_count increased). Also nudges
     the Logbook widget (now the sole QSO table -- see the removed "QSO
     log" widget) to refresh immediately instead of waiting up to 15 s for
     its own setInterval(loadLogbook,15000) tick. ---- */
  if(lastQsoCount!==null && s.qso_count>lastQsoCount && s.qsos && s.qsos.length){
   const q0=s.qsos[s.qsos.length-1];
   fireAlert('QSO logged', `${q0.call} ${q0.grid||''}`.trim());
   loadLogbook();
  }
  lastQsoCount=s.qso_count;
  renderRX(s);
  renderQSOs(s);
 }catch(e){document.getElementById('stale').style.display='inline';}
 try{
  const r=await fetch('/events?t='+Date.now()); const ej=await r.json();
  lastEventLines=ej.lines||[];
  renderEvents();
 }catch(e){}
}

/* ---- Actions widget: RX/chase control, target pick/skip, STOP+UNKEY.
   All calls are POSTs to this server's own /action/* endpoints (localhost-only,
   dry-run aware server-side). No radio control code runs in the browser. ---- */
async function postAction(path, body){
 try{
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  let j={}; try{j=await r.json();}catch(e){}
  return {ok:r.ok && j.ok!==false, status:r.status, body:j};
 }catch(e){ return {ok:false, error:String(e)}; }
}
function setActionsMsg(t){ document.getElementById('actionsMsg').textContent=t; }
async function refreshActionsState(){
 try{
  const r=await fetch('/actions/state?t='+Date.now()); const j=await r.json();
  const tx=!!j.ptt;
  // this pill was showing rx-loop's process-alive state ("running") even
  // while actively keyed, which reads as "we're receiving, not transmitting"
  // right when the opposite is true -- flip both the label and value to a
  // loud TX RUNNING the instant PTT is actually hot.
  const rxLabel=document.getElementById('stRxLabel'), rxVal=document.getElementById('stRx');
  if(tx){
   rxLabel.textContent='TX '; rxVal.textContent='RUNNING';
  }else{
   rxLabel.textContent='RX '; rxVal.textContent=j.rxloop?'running':'stopped';
  }
  rxVal.classList.toggle('tx-live',tx);
  const chEl=document.getElementById('stChaser');
  chEl.textContent=j.chaser?'running':'idle';
  chEl.classList.toggle('armed',!!j.chaser);
  document.getElementById('stPtt').textContent=tx?'TX':'RX';
  // ARMED (chaser alive -> a real key-up could happen any moment) vs LIVE
  // (engine.tx===true -> keyed this instant, upgrades to the siren pulse).
  const aw=document.getElementById('actionsWidget');
  aw.classList.toggle('armed',!!j.chaser);
  aw.classList.toggle('live',tx);
  document.body.classList.toggle('tx-live',tx);
  document.body.classList.toggle('dx-armed',!!j.dx_mode);
  lastRxRunning=!!j.rxloop;
  chaserRunning=!!j.chaser;
 }catch(e){}
}
function wireActions(){
 if(DRYRUN) document.getElementById('dryrunBanner').style.display='block';
 document.getElementById('btnRxStart').addEventListener('click',async()=>{
  setActionsMsg('starting RX…');
  const r=await postAction('/action/rx/start',{});
  setActionsMsg(r.ok?'RX start requested':'RX start failed: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 document.getElementById('btnRxStop').addEventListener('click',async()=>{
  setActionsMsg('standing down: unkey + stop Automatic CQ + stop RX…');
  const r=await postAction('/action/rx/stop',{});
  setActionsMsg(r.ok?'stood down — RX, chaser, and PTT all stopped':'stand-down failed: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 document.getElementById('btnChaseStart').addEventListener('click',()=>{
  document.getElementById('chaseConfirmMsg').style.display='block';
 });
 document.getElementById('btnChaseCancel').addEventListener('click',()=>{
  document.getElementById('chaseConfirmMsg').style.display='none';
 });
 // DX Mode toggle: checking it doesn't take effect immediately -- it opens
 // the "Arm DX Mode?" modal first (uncheck-then-show), and only actually
 // gets checked if the operator confirms. Unchecking never needs
 // confirmation. The checkbox itself is just a pre-start configuration
 // input (like chaseN/chaseMode) -- the page's blue dx-armed glow is driven
 // separately, off the RUNNING chaser's real dx_mode state (see
 // refreshActionsState()), not off this checkbox.
 document.getElementById('dxModeToggle').addEventListener('change',(e)=>{
  if(e.target.checked){
   e.target.checked=false;
   document.getElementById('dxModal').style.display='flex';
  }
 });
 document.getElementById('dxModalCancel').addEventListener('click',()=>{
  document.getElementById('dxModal').style.display='none';
  document.getElementById('dxModeToggle').checked=false;
 });
 document.getElementById('dxModalConfirm').addEventListener('click',()=>{
  document.getElementById('dxModal').style.display='none';
  document.getElementById('dxModeToggle').checked=true;
 });
 document.getElementById('btnChaseConfirm').addEventListener('click',async()=>{
  const n=parseFloat(document.getElementById('chaseN').value);
  const mode=document.getElementById('chaseMode').value;
  const dx_only=document.getElementById('dxModeToggle').checked;
  document.getElementById('chaseConfirmMsg').style.display='none';
  setActionsMsg('starting Automatic CQ…');
  const r=await postAction('/action/chase/start',{n,mode,confirm:true,dx_only});
  setActionsMsg(r.ok?('Automatic CQ start requested'+(r.body.rx_autostarted?' (RX auto-started)':'')+
   ' — watch NEXT TX up top'):('Automatic CQ start failed: '+(r.body.error||r.error||r.status)));
  refreshActionsState();
 });
 document.getElementById('btnChaseStop').addEventListener('click',async()=>{
  setActionsMsg('stopping Automatic CQ…');
  const r=await postAction('/action/chase/stop',{});
  setActionsMsg(r.ok?'Automatic CQ stop requested':'Automatic CQ stop failed: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 // SNR floor slider: live risk-meter update on every drag (input, no
 // network call), commits to the running chaser only on release (change) --
 // effective_snr_floor() in qso.py re-reads this file every hunt-loop cycle,
 // no restart needed. Applies to Automatic CQ generally, not just DX Mode:
 // reciprocity risk is sharpest on DX but the same filter runs always.
 document.getElementById('snrFloorSlider').addEventListener('input',(e)=>{
  updateSnrRiskUI(parseInt(e.target.value,10));
 });
 document.getElementById('snrFloorSlider').addEventListener('change',async(e)=>{
  const v=parseInt(e.target.value,10);
  const r=await postAction('/action/snr_floor/set',{snr_floor:v});
  setActionsMsg(r.ok?`SNR floor set to ${v} dB`:'SNR floor update failed: '+(r.body.error||r.error||r.status));
 });
 document.getElementById('snrFloorReset').addEventListener('click',async()=>{
  const def=(CFG&&CFG.snr_floor_default!=null)?CFG.snr_floor_default:-16;
  document.getElementById('snrFloorSlider').value=def;
  updateSnrRiskUI(def);
  const r=await postAction('/action/snr_floor/set',{reset:true});
  setActionsMsg(r.ok?`SNR floor reset to station default (${def} dB)`:'SNR floor reset failed: '+(r.body.error||r.error||r.status));
 });
 document.getElementById('btnUnkey').addEventListener('click',async()=>{
  const btn=document.getElementById('btnUnkey'); btn.disabled=true;
  const r=await postAction('/action/unkey',{});
  btn.disabled=false;
  setActionsMsg(r.ok?('UNKEY sent — PTT readback: '+(r.body.ptt!=null?r.body.ptt:'?')):'UNKEY FAILED: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 // TUNE 4 30s: stop Automatic CQ + unkey (same tested /action/unkey the STOP
 // button uses -- no new radio-facing code), then a visible 30 s window for
 // a manual TUNE cycle. Deliberately does NOT auto-resume the chase after
 // the window -- that would be re-starting TX without a fresh explicit go;
 // the operator clicks Automatic CQ again once actually done tuning.
 document.getElementById('btnTune30').addEventListener('click',async()=>{
  const btn=document.getElementById('btnTune30');
  if(btn.disabled) return;
  btn.disabled=true;
  const r=await postAction('/action/unkey',{});
  setActionsMsg(r.ok?'stopped for TUNE — 30s window starting':'stop failed: '+(r.body.error||r.error||r.status));
  refreshActionsState();
  let secs=30;
  btn.textContent=`TUNING… ${secs}s`;
  const iv=setInterval(()=>{
   secs--;
   if(secs<=0){
    clearInterval(iv);
    btn.textContent='TUNE';
    btn.disabled=false;
    setActionsMsg('tune window done — click Automatic CQ when ready');
   }else{
    btn.textContent=`TUNING… ${secs}s`;
   }
  },1000);
 });
 // target pick/skip: event delegation since #next/#cand are re-rendered every tick
 document.getElementById('opsBody').addEventListener('click',async e=>{
  const chip=e.target.closest('.callchip');
  if(chip){
   const call=chip.dataset.call; chip.disabled=true;
   const r=await postAction('/action/target/pick',{call});
   document.getElementById('targetStatus').textContent=r.ok?`requested ${call} @ ${new Date().toLocaleTimeString()}`:'request failed';
   return;
  }
  if(e.target.id==='btnSkip'){
   const r=await postAction('/action/target/skip',{});
   document.getElementById('targetStatus').textContent=r.ok?'skip requested @ '+new Date().toLocaleTimeString():'skip failed';
  }
 });
}

/* ---- help modal: static content, tab switching only, no server calls. ---- */
function wireHelp(){
 document.getElementById('btnInfo').addEventListener('click',()=>{
  document.getElementById('helpModal').style.display='flex';
 });
 document.getElementById('helpClose').addEventListener('click',()=>{
  document.getElementById('helpModal').style.display='none';
 });
 document.getElementById('helpModal').addEventListener('click',(e)=>{
  if(e.target.id==='helpModal') document.getElementById('helpModal').style.display='none';
 });
 document.querySelectorAll('.helpTab').forEach(tab=>{
  tab.addEventListener('click',()=>{
   document.querySelectorAll('.helpTab').forEach(t=>t.classList.remove('active'));
   document.querySelectorAll('.helpPane').forEach(p=>p.classList.remove('active'));
   tab.classList.add('active');
   document.querySelector(`.helpPane[data-pane="${tab.dataset.tab}"]`).classList.add('active');
  });
 });
}

/* ---- alerts (4.3): client-side only, derived from the existing /status.json,
   /engine.json and /actions/state polls above — no server push. Browser
   Notification API when granted; tab-title flash as fallback when denied/
   unavailable. Off by default; the bell toggle's state rides along in the
   same /layout blob the widget system already persists (server just stores
   whatever JSON it's given, so no dashboard.py endpoint changes needed). ---- */
let alertsEnabled=false, notifPermission=(window.Notification && Notification.permission) || 'default';
let lastQsoCount=null, lastEngineState='', lastRxRunning=false, lastSilenceFlag=false, chaserRunning=false;
let titleFlashTimer=null; const BASE_TITLE=document.title;
function flashTitle(text){
 if(titleFlashTimer) return;               // already flashing
 let on=false;
 const marker='★ '+text;               // "★ QSO!" etc. — fallback when Notification is denied
 const stop=()=>{ clearInterval(titleFlashTimer); titleFlashTimer=null; document.title=BASE_TITLE;
  document.removeEventListener('visibilitychange', onVis); };
 const onVis=()=>{ if(!document.hidden) stop(); };
 document.addEventListener('visibilitychange', onVis);
 titleFlashTimer=setInterval(()=>{ document.title=on?BASE_TITLE:marker; on=!on; }, 1000);
 setTimeout(stop, 30000);                   // safety cap regardless of focus
}
function doAlert(kind, text){
 console.log('[coa-alert]', kind, text);    // always logged — verifiable without a radio
 if(window.Notification && Notification.permission==='granted'){
  try{
   const n=new Notification('COTA — '+kind, {body:text});
   // the OS notification daemon's default action ("Activate" on many Linux
   // desktops) fires this click event -- without a handler here it just
   // closes the notification and does nothing else. Bring the dashboard
   // tab into focus, which is what that action is supposed to do.
   n.onclick=()=>{ window.focus(); n.close(); };
   return;
  }catch(e){}
 }
 flashTitle(text);
}
function fireAlert(kind, text){ if(alertsEnabled) doAlert(kind, text); }
function updateBellUI(){
 const b=document.getElementById('btnBell');
 b.textContent='Alerts: '+(alertsEnabled?'ON':'OFF');
 b.classList.toggle('active', alertsEnabled);
}
function wireBell(){
 document.getElementById('btnBell').addEventListener('click', async ()=>{
  if(!alertsEnabled){
   if(window.Notification && Notification.permission==='default'){
    try{ notifPermission=await Notification.requestPermission(); }catch(e){}
   }
   alertsEnabled=true;
  }else{
   alertsEnabled=false;
  }
  updateBellUI();
  scheduleSaveLayout();
 });
}
/* dev-only test hook: verify each alert path without a radio —
   coaSimulateAlert('qso'|'chase_end'|'abort'|'silence') from the console, or
   load the page with ?simulateAlert=qso (etc.) to fire one automatically. */
window.coaSimulateAlert=function(kind){
 const sims={
  qso:()=>doAlert('QSO logged', 'TEST1AA FN20 (simulated)'),
  chase_end:()=>doAlert('Automatic CQ ended', 'state: done (simulated)'),
  abort:()=>doAlert('Watchdog/abort', 'engine state: watchdog-test (simulated)'),
  silence:()=>doAlert('Decode silence', 'no new decodes for 3+ min (simulated)'),
 };
 if(sims[kind]){ sims[kind](); return true; }
 console.warn('coaSimulateAlert: unknown kind', kind, '— use one of', Object.keys(sims));
 return false;
};
(function(){
 const p=new URLSearchParams(location.search).get('simulateAlert');
 if(p) setTimeout(()=>window.coaSimulateAlert(p), 1500);
})();

/* ---- widget system (part B): resize (native CSS resize handles), collapse,
   drag-reorder (native HTML5 DnD), persisted server-side (data/ui-layout.json,
   atomic write) with localStorage as write-through cache. ---- */
let dragKey=null, layoutSaveTimer=null;
function scheduleSaveLayout(){ clearTimeout(layoutSaveTimer); layoutSaveTimer=setTimeout(saveLayout,500); }
function currentLayout(){
 const widgets={};
 document.querySelectorAll('#dash > .widget').forEach((w,i)=>{
  widgets[w.dataset.key]={order:i, collapsed:w.classList.contains('collapsed'),
   w:w.style.width||null, h:w.style.height||null};
 });
 if(widgets.map) widgets.map.mapMode=mapMode;
 return {widgets, notify:alertsEnabled};
}
function saveLayout(){
 const layout=currentLayout();
 try{localStorage.setItem('coa-layout', JSON.stringify(layout));}catch(e){}
 fetch('/layout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(layout)}).catch(()=>{});
}
function applyLayout(layout){
 if(!layout||!layout.widgets) return;
 const w=layout.widgets;
 const keys=Object.keys(w).filter(k=>document.querySelector(`.widget[data-key="${k}"]`));
 keys.sort((a,b)=>(w[a].order||0)-(w[b].order||0));
 const dash=document.getElementById('dash');
 for(const k of keys){
  const el=document.querySelector(`.widget[data-key="${k}"]`);
  dash.appendChild(el);
  if(w[k].w) el.style.width=w[k].w;
  if(w[k].h) el.style.height=w[k].h;
  if(w[k].collapsed) el.classList.add('collapsed');
 }
 if(w.map&&w.map.mapMode) setMapMode(w.map.mapMode);
 if(typeof layout.notify==='boolean'){ alertsEnabled=layout.notify; updateBellUI(); }
}
async function loadLayout(){
 let layout=null;
 try{ const r=await fetch('/layout'); if(r.ok){ const j=await r.json(); if(j&&j.widgets) layout=j; } }catch(e){}
 if(!layout){ try{ const c=localStorage.getItem('coa-layout'); if(c) layout=JSON.parse(c); }catch(e){} }
 if(layout) applyLayout(layout);
}
function resetLayout(){
 document.querySelectorAll('#dash > .widget').forEach(w=>{
  w.style.width=''; w.style.height=''; w.classList.remove('collapsed');
 });
 const order=['status','decodes','ops','actions','map','waterfall','logbook','events'];
 const dash=document.getElementById('dash');
 for(const k of order){ const el=document.querySelector(`.widget[data-key="${k}"]`); if(el) dash.appendChild(el); }
 setMapMode('auto');
 try{localStorage.removeItem('coa-layout');}catch(e){}
 saveLayout();
}
function initWidgetChrome(){
 document.querySelectorAll('.widget').forEach(w=>{
  w.querySelector('.wcollapse').addEventListener('click',()=>{
   w.classList.toggle('collapsed');
   scheduleSaveLayout();
  });
  const title=w.querySelector('.wtitle');
  title.draggable=true;
  title.addEventListener('dragstart',e=>{ dragKey=w.dataset.key; e.dataTransfer.effectAllowed='move'; });
  w.addEventListener('dragover',e=>{ if(dragKey) e.preventDefault(); });
  w.addEventListener('drop',e=>{
   e.preventDefault();
   if(!dragKey||dragKey===w.dataset.key){dragKey=null;return;}
   const src=document.querySelector(`.widget[data-key="${dragKey}"]`);
   if(src){
    const rect=w.getBoundingClientRect();
    const before=(e.clientX-rect.left)<rect.width/2;
    w.parentNode.insertBefore(src, before?w:w.nextSibling);
    scheduleSaveLayout();
   }
   dragKey=null;
  });
  new ResizeObserver(()=>scheduleSaveLayout()).observe(w);
 });
}
document.getElementById('mapAuto').addEventListener('click',()=>setMapMode('auto'));
document.getElementById('mapWorld').addEventListener('click',()=>setMapMode('world'));

/* ---- manual pan/drag + wheel/pinch-zoom: any manual interaction drops
   mapMode out of 'auto'/'world' (so updateMapZoom()'s auto-fit stops
   fighting the user -- it already early-returns for any mode other than
   'auto') and applies the new viewBox immediately, no eased animation
   (that's reserved for programmatic auto-fit jumps). Re-enter Auto/World
   any time via their buttons. ---- */
(function(){
 const svg=document.getElementById('map');
 function toManual(){
  if(mapMode!=='manual'){
   mapMode='manual';
   document.getElementById('mapAuto').classList.remove('active');
   document.getElementById('mapWorld').classList.remove('active');
  }
 }
 function applyVb(v){ vb=v; vbTarget=v; applyViewBox(v); scheduleSaveLayout(); }
 let dragging=false, lastX=0, lastY=0;
 svg.addEventListener('mousedown',(e)=>{ dragging=true; lastX=e.clientX; lastY=e.clientY; svg.style.cursor='grabbing'; });
 window.addEventListener('mousemove',(e)=>{
  if(!dragging) return;
  const rect=svg.getBoundingClientRect();
  const dx=e.clientX-lastX, dy=e.clientY-lastY;
  lastX=e.clientX; lastY=e.clientY;
  toManual();
  applyVb(panViewBox(vb,dx,dy,rect.width,rect.height));
 });
 window.addEventListener('mouseup',()=>{ if(dragging){dragging=false; svg.style.cursor='';} });
 svg.addEventListener('wheel',(e)=>{
  e.preventDefault();
  const rect=svg.getBoundingClientRect();
  const cxFrac=(e.clientX-rect.left)/rect.width, cyFrac=(e.clientY-rect.top)/rect.height;
  const factor=e.deltaY>0?1.15:1/1.15;
  toManual();
  applyVb(zoomViewBox(vb,factor,cxFrac,cyFrac));
 },{passive:false});
 let touchMode=null, lastTX=0, lastTY=0, lastPinch=0;
 function pinchDist(t){ return Math.hypot(t[0].clientX-t[1].clientX, t[0].clientY-t[1].clientY); }
 svg.addEventListener('touchstart',(e)=>{
  if(e.touches.length===1){ touchMode='pan'; lastTX=e.touches[0].clientX; lastTY=e.touches[0].clientY; }
  else if(e.touches.length===2){ touchMode='pinch'; lastPinch=pinchDist(e.touches); }
 },{passive:true});
 svg.addEventListener('touchmove',(e)=>{
  const rect=svg.getBoundingClientRect();
  if(touchMode==='pan' && e.touches.length===1){
   e.preventDefault();
   const dx=e.touches[0].clientX-lastTX, dy=e.touches[0].clientY-lastTY;
   lastTX=e.touches[0].clientX; lastTY=e.touches[0].clientY;
   toManual();
   applyVb(panViewBox(vb,dx,dy,rect.width,rect.height));
  }else if(touchMode==='pinch' && e.touches.length===2){
   e.preventDefault();
   const dist=pinchDist(e.touches);
   const factor=lastPinch/dist;
   lastPinch=dist;
   const cx=(e.touches[0].clientX+e.touches[1].clientX)/2-rect.left;
   const cy=(e.touches[0].clientY+e.touches[1].clientY)/2-rect.top;
   toManual();
   applyVb(zoomViewBox(vb,factor,cx/rect.width,cy/rect.height));
  }
 },{passive:false});
 svg.addEventListener('touchend',()=>{ touchMode=null; });
 // click any contact dot (RX/QSO/TX) to lock the map onto its country and
 // open the country info card -- see openCountryCard().
 svg.addEventListener('click',(e)=>{
  const dot=e.target.closest('.dot-rx,.dot-qso,.dot-tx');
  if(!dot) return;
  const call=dot.dataset.call;
  if(call) openCountryCard(call, dot.dataset.grid);
 });
})();
document.getElementById('countryCardClose').addEventListener('click',closeCountryCard);
// click anywhere outside the popup (but not on a dot/row that opens a new
// one -- that just repositions it) closes it; Escape does too.
document.addEventListener('click',(e)=>{
 if(document.getElementById('countryCard').style.display==='none') return;
 if(e.target.closest('#countryCardBox,.dot-rx,.dot-qso,.dot-tx,.lbRow')) return;
 closeCountryCard();
});
document.addEventListener('keydown',(e)=>{ if(e.key==='Escape') closeCountryCard(); });
// Logbook rows: same click-to-lock-zoom-and-show-card as map dots. Event
// delegation since #lbTable is fully re-rendered on every loadLogbook().
document.getElementById('lbTable').addEventListener('click',(e)=>{
 const row=e.target.closest('.lbRow');
 if(row&&row.dataset.call) openCountryCard(row.dataset.call, row.dataset.grid);
});
document.getElementById('ccCallBtn').addEventListener('click',async()=>{
 const call=document.getElementById('ccCallBtn').dataset.call;
 const r=await postAction('/action/target/pick',{call});
 setActionsMsg(r.ok?`requested ${call} @ ${new Date().toLocaleTimeString()}`:'request failed');
 closeCountryCard();
});
document.getElementById('resetLayout').addEventListener('click',resetLayout);

initWidgetChrome();
updateBellUI();
wireBell();
loadLayout();
wireActions();
wireStationCfg();
wireQrz();
wireHelp();
document.getElementById('evRaw').addEventListener('change',renderEvents);
document.getElementById('txwf').addEventListener('error',function(){this.style.display='none';});
loadCfg().then(()=>{ tick(); loadBands().then(()=>{ buildAntBandsRow(); loadAntennas(); }); });
loadBorders();
setInterval(tick,5000);
engTick(); setInterval(engTick,2000);
setInterval(nextTxFastTick,150);           // smooth NEXT TX countdown between engTick polls
refreshActionsState(); setInterval(refreshActionsState,3000);
loadQrzStatus(); setInterval(loadQrzStatus,10000);
loadLogbook(); setInterval(loadLogbook,15000);
</script></body></html>"""
PAGE = (PAGE.replace("__MYCALL__", MYCALL).replace("__MYGRID__", MYGRID)
            .replace("__EVENT_LINES__", str(EVENT_LINES))
            .replace("__WORLD__", world_map.WORLD_PATH)
            .replace("__DRYRUN__", "true" if DRYRUN else "false")
            .replace("__DEFAULT_MAX_W__", str(DEFAULT_MAX_W))
            .replace("__CALL_PREFIXES_JSON__", json.dumps(dxcc.CALL_PREFIXES)))

def chase_tail(n=EVENT_LINES):
    """Last n lines of chase.log without reading a huge file into memory."""
    try:
        with open(CHASELOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return [l.rstrip() for l in lines if l.strip()][-n:]
    except OSError:
        return []

def qrz_sync_tail(n=30):
    try:
        with open(QRZ_SYNC_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return [l.rstrip() for l in lines if l.strip()][-n:]
    except OSError:
        return []

def _read_qrz_cache():
    try:
        with open(QRZ_CACHE) as f:
            obj = json.load(f)
        if isinstance(obj, dict) and isinstance(obj.get("records"), list):
            return obj
    except (OSError, ValueError):
        pass
    return {"fetched_at": None, "count": 0, "records": []}


def _qrz_status():
    """Read-only: never touches the network, never returns the key itself —
    just whether one's on file, how many ADIF records are past the last
    synced offset, and whether a sync/fetch is currently running."""
    offset = logsync.read_offset()
    pending = len(logsync.new_records(logsync.DEFAULT_ADIF, offset))
    cache = _read_qrz_cache()
    confirmed = sum(1 for r in cache["records"]
                    if (r.get("app_qrzlog_status") or "").upper() == "C")
    return {
        "configured": logsync.read_key() is not None,
        "offset": offset,
        "pending": pending,
        "adif": logsync.DEFAULT_ADIF,
        "syncing": _proc_running(LOGSYNC_PY),
        "fetching": _proc_running(QRZ_FETCH_PY),
        "qrz_count": cache["count"],
        "qrz_confirmed": confirmed,
        "fetched_at": cache["fetched_at"],
        "log_tail": qrz_sync_tail(30),
    }


_qrz_xml_session = {"key": None, "at": 0}
QRZ_XML_SESSION_TTL = 20 * 3600   # QRZ documents the session key as valid "for the rest of the day"


def _qrz_xml_lookup(call):
    """Bio/photo lookup for the country info card. Never raises -- every
    failure mode (not configured, bad creds, transport error, session
    expiry, callsign not found) comes back as a plain dict the caller can
    render around, not an exception. Session key is cached in-memory
    (cleared on dashboard restart, which is fine -- a fresh login on first
    use is cheap and this isn't safety-relevant); one login+retry on a
    session-expired lookup, not an unbounded retry loop."""
    user, pw = logsync.read_xml_credentials()
    if not user or not pw:
        return {"configured": False, "ok": False, "error": "no QRZ XML credentials on file"}

    def do_lookup():
        return qrz_xml_api.lookup(_qrz_xml_session["key"], call)

    if not _qrz_xml_session["key"] or (time.time() - _qrz_xml_session["at"]) > QRZ_XML_SESSION_TTL:
        ok, key_or_err = qrz_xml_api.login(user, pw)
        if not ok:
            return {"configured": True, "ok": False, "error": key_or_err}
        _qrz_xml_session["key"], _qrz_xml_session["at"] = key_or_err, time.time()
    ok, fields_or_err = do_lookup()
    if not ok:
        # session key might have just expired server-side -- one fresh
        # login + single retry, not an unbounded loop
        ok2, key_or_err = qrz_xml_api.login(user, pw)
        if not ok2:
            return {"configured": True, "ok": False, "error": key_or_err}
        _qrz_xml_session["key"], _qrz_xml_session["at"] = key_or_err, time.time()
        ok, fields_or_err = do_lookup()
        if not ok:
            return {"configured": True, "ok": False, "error": fields_or_err}
    return {"configured": True, "ok": True, "fields": fields_or_err}


def _logbook_payload():
    """Local ADIF cross-matched against the QRZ fetch cache — the Logbook
    widget's data. Newest first. Pure merge logic lives in logbook.py."""
    try:
        with open(logsync.DEFAULT_ADIF, "rb") as f:
            local = adif.records_from_bytes(f.read())
    except OSError:
        local = []
    cache = _read_qrz_cache()
    rows = logbook.merge(local, cache["records"],
                         synced_through=logsync.read_offset())
    rows.reverse()
    return {"rows": rows, "qrz_count": cache["count"],
            "fetched_at": cache["fetched_at"]}

def atomic_write_json(path, obj):
    """tmp + os.replace so a reader never sees a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

def log_action(line):
    """Append one audit-trail line to data/actions.log. Never raises."""
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(ACTIONS_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}Z {line}\n")
    except OSError:
        pass

def _proc_running(pattern):
    """True if some process's full command line contains `pattern` (an absolute
    path) — never a bare name, so this can't match an unrelated process."""
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False

def _spawn_detached(cmd, log_path):
    """Spawn cmd fully detached (new session, own pgid) so this HTTP server can
    never accidentally signal it; stdout+stderr appended to log_path."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    lf = open(log_path, "a")
    subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                      cwd=_ROOT, start_new_session=True, close_fds=True)

def _pkill(pattern):
    """Kill by exact absolute-path pattern match only — never a broad pattern,
    and never this server's own pid/pgid (dashboard.py's own path never matches
    qso.py's or rx-loop.sh's absolute paths)."""
    try:
        r = subprocess.run(["pkill", "-f", pattern], timeout=5)
        return r.returncode == 0
    except Exception:
        return False

# ---- antenna profiles: operator-editable, band/wattage selection is locked
# to this data + the BANDS table above (no free-form Hz entry, no wattage
# above a per-antenna confirmed-safe max). Never touched by qso.py/rx-loop —
# only /action/station/set below writes the *active* choice into station.conf,
# and only when the chaser is stopped (see that handler).
def _default_antennas():
    """Seed from skills/antenna-atu.md (Logan's 3 physical antennas, 2026-07-03).
    Only the EFHW has a number on record — the RFI-interim 5 W limit measured
    that day (10 W blacks out CAT/USB serial). The two dipoles' RF-exposure-
    verified max watts is a still-open TODO in that file; left unset (None)
    here rather than guessed, so the UI shows them as unconfirmed until Logan
    fills them in himself via Add/Edit."""
    return [
        {"id": "efhw-40m", "name": "40 m EFHW", "bands": ["40m"], "max_watts": 5,
         "notes": "RFI-interim limit (not RF-exposure): 10 W blacks out CAT/USB serial; "
                  "clean at 5 W. Raise only after installing a feedline common-mode choke."},
        {"id": "dipole-40m", "name": "40 m dipole", "bands": ["40m"], "max_watts": None,
         "notes": "TODO: confirm RF-exposure-verified max watts for this antenna's siting."},
        {"id": "dipole-20m", "name": "20 m dipole", "bands": ["20m"], "max_watts": None,
         "notes": "TODO: confirm RF-exposure-verified max watts for this antenna's siting."},
    ]

def _slugify(name):
    s = "".join(c.lower() if c.isalnum() else "-" for c in name.strip())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "antenna"

def _load_antennas():
    try:
        with open(ANTENNAS_JSON) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, ValueError):
        pass
    seed = _default_antennas()
    atomic_write_json(ANTENNAS_JSON, seed)
    return seed

def _save_antennas(lst):
    atomic_write_json(ANTENNAS_JSON, lst)

def _find_antenna(lst, aid):
    for a in lst:
        if a["id"] == aid:
            return a
    return None

def _validate_bands(bands):
    return isinstance(bands, list) and bool(bands) and all(b in BANDS for b in bands)

def _validate_max_watts(mw):
    """Returns (ok, value_or_errmsg). None is always valid (unconfirmed)."""
    if mw is None:
        return True, None
    try:
        mw = float(mw)
    except (TypeError, ValueError):
        return False, "max_watts must be numeric or null"
    if not (0 < mw <= ABS_MAX_W):
        return False, f"max_watts out of range (0-{ABS_MAX_W})"
    return True, mw

def _validate_snr_floor(v):
    """Returns (ok, value_or_errmsg) for /action/snr_floor/set's POST body.
    Range matches FT8/JT9's practical decode floor (~-24 dB) with headroom
    on both sides for future rigs/antennas -- not a TX-safety bound, just a
    sanity check against a fat-fingered value."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return False, "snr_floor must be numeric"
    if not (-30 <= v <= 10):
        return False, "snr_floor out of range (-30 to 10 dB)"
    return True, v

def _build_chase_args(body):
    """Pure validation for /action/chase/start's POST body. Returns
    (args_list, desc_str, None) on success, or (None, None, error_msg) on
    validation failure. No I/O, no subprocess — unit-testable without an
    HTTP server. dx_only (optional bool, default False) appends --dx-only
    to args_list and a note to desc; all other validation is unchanged from
    before this refactor."""
    if not body.get("confirm"):
        return None, None, "confirm required"
    mode = body.get("mode")
    if mode not in ("qsos", "minutes"):
        return None, None, "mode must be 'qsos' or 'minutes'"
    try:
        n = float(body.get("n"))
    except (TypeError, ValueError):
        return None, None, "n must be numeric"
    dx_only = bool(body.get("dx_only"))
    if mode == "qsos":
        n = int(n)
        if not (1 <= n <= 20):
            return None, None, "n out of range (1-20 QSOs)"
        args = ["python3", QSO_PY, "--max-qsos", str(n)]
        desc = f"{n} QSO(s)"
    else:
        if not (1 <= n <= 180):
            return None, None, "n out of range (1-180 minutes)"
        args = ["python3", QSO_PY, "--minutes", str(n)]
        desc = f"{n:g} min budget"
    if dx_only:
        args.append("--dx-only")
        desc += " [DX Mode]"
    return args, desc, None

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DATA, **kw)

    def send_body(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, obj):
        self.send_body(json.dumps(dict(ok=True, **obj)).encode(), "application/json")

    def _err(self, code, msg):
        self.send_body(json.dumps({"ok": False, "error": msg}).encode(), "application/json", code)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path.startswith("/index"):
            self.send_body(PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/events":
            body = json.dumps({"lines": chase_tail()}).encode()
            self.send_body(body, "application/json")
        elif path == "/config":
            self.send_body(json.dumps(CONFIG).encode(), "application/json")
        elif path == "/antennas":
            self.send_body(json.dumps(_load_antennas()).encode(), "application/json")
        elif path == "/bands":
            body = json.dumps([{"name": n, **v} for n, v in BANDS.items()]).encode()
            self.send_body(body, "application/json")
        elif path == "/qrz/status":
            self.send_body(json.dumps(_qrz_status()).encode(), "application/json")
        elif path == "/logbook":
            self.send_body(json.dumps(_logbook_payload()).encode(), "application/json")
        elif path == "/layout":
            try:
                with open(LAYOUT_JSON, "rb") as f:
                    self.send_body(f.read(), "application/json")
            except OSError:
                self.send_body(b"{}", "application/json")
        elif path == "/borders/countries":
            self.send_body(COUNTRY_BORDERS_JSON, "application/json")
        elif path == "/borders/states":
            self.send_body(STATE_BORDERS_JSON, "application/json")
        elif path == "/borders/adjacency":
            self.send_body(COUNTRY_ADJACENCY_JSON, "application/json")
        elif path == "/borders/dish_flower":
            self.send_body(DISH_FLOWER_JSON, "application/json")
        elif path.startswith("/flags/") and path.endswith(".svg"):
            # strict [a-z]{2} check before touching the filesystem -- this
            # segment comes straight from the URL, never trust it as a path
            code = path[len("/flags/"):-len(".svg")]
            if not _FLAG_CODE_RE.match(code):
                return self._err(404, "no such flag")
            try:
                with open(os.path.join(FLAGS_DIR, code + ".svg"), "rb") as f:
                    self.send_body(f.read(), "image/svg+xml")
            except OSError:
                self._err(404, "no such flag")
        elif path == "/qrz/lookup":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            call = (qs.get("call", [""])[0] or "").strip().upper()
            if not call:
                return self._err(400, "call required")
            self.send_body(json.dumps(_qrz_xml_lookup(call)).encode(), "application/json")
        elif path == "/actions/state":
            engine = {}
            try:
                with open(os.path.join(DATA, "engine.json")) as f:
                    engine = json.load(f)
            except Exception:
                pass
            state = {"chaser": _proc_running(QSO_PY), "rxloop": _proc_running(RXLOOP_SH),
                      "ptt": bool(engine.get("tx")), "engine_state": engine.get("state"),
                      "dx_mode": bool(engine.get("dx_mode")), "dryrun": DRYRUN}
            self.send_body(json.dumps(state).encode(), "application/json")
        else:
            self.path = path
            super().do_GET()

    def do_POST(self):
        # Local-only, belt and suspenders (server already binds 127.0.0.1 only).
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_body(b'{"ok":false,"error":"local only"}', "application/json", 403)
            return
        path = self.path.split("?")[0]
        parse_ok = True
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                body = {}
                parse_ok = False
        except Exception:
            body = {}
            parse_ok = False
        try:
            if path == "/layout":
                if not parse_ok or "widgets" not in body or not isinstance(body["widgets"], dict):
                    return self._err(400, "malformed layout body")
                atomic_write_json(LAYOUT_JSON, body)
                self._ok({})
            elif path == "/action/rx/start":
                self._action_rx_start()
            elif path == "/action/rx/stop":
                self._action_rx_stop()
            elif path == "/action/chase/start":
                self._action_chase_start(body)
            elif path == "/action/chase/stop":
                self._action_chase_stop()
            elif path == "/action/unkey":
                self._action_unkey()
            elif path == "/action/target/pick":
                self._action_target_write(body, TARGET_REQ, "pick", need_call=True)
            elif path == "/action/target/skip":
                self._action_target_write(body, SKIP_REQ, "skip", need_call=False)
            elif path == "/action/snr_floor/set":
                self._action_snr_floor_set(body)
            elif path == "/action/antenna/add":
                self._action_antenna_add(body)
            elif path == "/action/antenna/update":
                self._action_antenna_update(body)
            elif path == "/action/antenna/remove":
                self._action_antenna_remove(body)
            elif path == "/action/station/set":
                self._action_station_set(body)
            elif path == "/action/qrz/sync":
                self._action_qrz_sync()
            elif path == "/action/qrz/refresh":
                self._action_qrz_refresh()
            else:
                self._err(404, "no such endpoint")
        except Exception as e:
            log_action(f"ERROR handling POST {path}: {e!r}")
            self._err(500, "internal error")

    # ---- action handlers ----
    def _action_rx_start(self):
        if DRYRUN:
            log_action(f"[DRYRUN] would start rx-loop: bash {RXLOOP_SH} >> {DATA}/rx-loop.log 2>&1 &")
            return self._ok({"started": True, "dryrun": True})
        if _proc_running(RXLOOP_SH):
            log_action("rx/start: already running, no-op")
            return self._ok({"started": False, "already": True})
        _spawn_detached(["bash", RXLOOP_SH], os.path.join(DATA, "rx-loop.log"))
        log_action(f"rx/start: spawned bash {RXLOOP_SH}")
        self._ok({"started": True})

    def _action_rx_stop(self):
        """Full stand-down, not just "stop decoding": without RX there's
        nothing for a live chaser to answer, so leaving it running would just
        spin uselessly forever — pull it down too. Order matches _action_unkey:
        rigctl T 0 first and unconditionally (independent of chaser health),
        then kill the chaser, then stop rx-loop last."""
        if DRYRUN:
            log_action(f"[DRYRUN] would stand down: rigctl T 0; pkill -f {QSO_PY}; pkill -f {RXLOOP_SH}")
            return self._ok({"stopped": True, "dryrun": True})
        try:
            subprocess.run(["rigctl", "-m", RIG_MODEL, "-r", CAT_PORT, "-s", CAT_BAUD, "T", "0"],
                           capture_output=True, text=True, timeout=10)
        except Exception as e:
            log_action(f"rx/stop: rigctl T 0 error: {e!r}")
        killed_chaser = _pkill(QSO_PY)
        ok = _pkill(RXLOOP_SH)
        log_action(f"rx/stop: rigctl T 0 (sent first); pkill -f {QSO_PY} -> {killed_chaser}; "
                   f"pkill -f {RXLOOP_SH} -> {ok}")
        self._ok({"stopped": ok, "chaser_killed": killed_chaser})

    def _action_chase_start(self, body):
        args, desc, err = _build_chase_args(body)
        if err:
            return self._err(400, err)
        if DRYRUN:
            log_action(f"[DRYRUN] would start chaser: {' '.join(args)} (>> {CHASELOG})")
            return self._ok({"started": True, "dryrun": True})
        if _proc_running(QSO_PY):
            log_action("chase/start: refused, chaser already running")
            return self._err(409, "chaser already running")
        rx_autostarted = False
        if not _proc_running(RXLOOP_SH):
            _spawn_detached(["bash", RXLOOP_SH], os.path.join(DATA, "rx-loop.log"))
            log_action(f"chase/start: rx-loop wasn't running, auto-started bash {RXLOOP_SH}")
            rx_autostarted = True
        _spawn_detached(args, CHASELOG)
        log_action(f"chase/start: spawned {' '.join(args)} ({desc})")
        self._ok({"started": True, "rx_autostarted": rx_autostarted})

    def _action_chase_stop(self):
        if DRYRUN:
            log_action(f"[DRYRUN] would stop chaser: pkill -f {QSO_PY}")
            return self._ok({"stopped": True, "dryrun": True})
        ok = _pkill(QSO_PY)
        log_action(f"chase/stop: pkill -f {QSO_PY} -> {ok}")
        self._ok({"stopped": ok})

    def _action_unkey(self):
        """STOP + UNKEY: zero confirmation, one click, works regardless of
        chaser/app health. Order matters: rigctl T 0 fires FIRST and
        unconditionally — this is a direct, independent call to the rig, not
        routed through qso.py's own state machine, so it still works even if
        the chaser is hung/buggy. Killing the chaser and reading PTT back are
        secondary cleanup and never gate or delay the T 0 call. Never sends T 1."""
        if DRYRUN:
            log_action(f"[DRYRUN] would UNKEY: rigctl -m {RIG_MODEL} -r {CAT_PORT} -s {CAT_BAUD} T 0; "
                       f"pkill -f {QSO_PY}")
            return self._ok({"unkeyed": True, "dryrun": True, "ptt": None})
        try:
            subprocess.run(["rigctl", "-m", RIG_MODEL, "-r", CAT_PORT, "-s", CAT_BAUD, "T", "0"],
                           capture_output=True, text=True, timeout=10)
        except Exception as e:
            log_action(f"UNKEY: rigctl T 0 error: {e!r}")
        killed = _pkill(QSO_PY)
        ptt = None
        try:
            r2 = subprocess.run(["rigctl", "-m", RIG_MODEL, "-r", CAT_PORT, "-s", CAT_BAUD, "t"],
                               capture_output=True, text=True, timeout=10)
            ptt = r2.stdout.strip()
        except Exception as e:
            log_action(f"UNKEY: PTT readback error: {e!r}")
        log_action(f"UNKEY: rigctl T 0 (sent first); pkill -f {QSO_PY} (killed={killed}); PTT readback={ptt}")
        self._ok({"unkeyed": True, "killed": killed, "ptt": ptt})

    def _action_target_write(self, body, path, kind, need_call):
        call = str(body.get("call", "")).strip().upper()
        if need_call and not call:
            return self._err(400, "call required")
        obj = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if call:
            obj["call"] = call
        atomic_write_json(path, obj)
        log_action(f"target/{kind}: {obj}")
        self._ok({"written": os.path.basename(path)})

    def _action_snr_floor_set(self, body):
        """Live-override the running chaser's SNR floor (station.conf's
        SNR_FLOOR otherwise). Same file-drop IPC as target/pick and
        target/skip: qso.py's hunt loop re-reads SNR_FLOOR_REQ every cycle
        (see effective_snr_floor()), no restart needed. 'reset' clears the
        override back to station.conf's value."""
        if body.get("reset"):
            try:
                os.remove(SNR_FLOOR_REQ)
            except FileNotFoundError:
                pass
            log_action("snr_floor/set: reset to station.conf default")
            return self._ok({"reset": True})
        ok, val = _validate_snr_floor(body.get("snr_floor"))
        if not ok:
            return self._err(400, val)
        atomic_write_json(SNR_FLOOR_REQ, {"snr_floor": val,
                                           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        log_action(f"snr_floor/set: {val} dB")
        self._ok({"snr_floor": val})

    def _action_antenna_add(self, body):
        name = str(body.get("name", "")).strip()
        if not name:
            return self._err(400, "name required")
        bands = body.get("bands")
        if not _validate_bands(bands):
            return self._err(400, "bands must be a non-empty list of valid band names")
        mw_ok, mw = _validate_max_watts(body.get("max_watts"))
        if not mw_ok:
            return self._err(400, mw)
        notes = str(body.get("notes", "")).strip()
        lst = _load_antennas()
        base = _slugify(name)
        aid, i, existing = base, 2, {a["id"] for a in lst}
        while aid in existing:
            aid = f"{base}-{i}"; i += 1
        entry = {"id": aid, "name": name, "bands": bands, "max_watts": mw, "notes": notes}
        lst.append(entry)
        _save_antennas(lst)
        log_action(f"antenna/add: {entry}")
        self._ok({"antenna": entry, "antennas": lst})

    def _action_antenna_update(self, body):
        aid = str(body.get("id", "")).strip()
        lst = _load_antennas()
        entry = _find_antenna(lst, aid)
        if not entry:
            return self._err(404, "no such antenna")
        if "name" in body:
            name = str(body["name"]).strip()
            if not name:
                return self._err(400, "name cannot be empty")
            entry["name"] = name
        if "bands" in body:
            if not _validate_bands(body["bands"]):
                return self._err(400, "bands must be a non-empty list of valid band names")
            entry["bands"] = body["bands"]
        if "max_watts" in body:
            mw_ok, mw = _validate_max_watts(body["max_watts"])
            if not mw_ok:
                return self._err(400, mw)
            entry["max_watts"] = mw
        if "notes" in body:
            entry["notes"] = str(body["notes"]).strip()
        _save_antennas(lst)
        log_action(f"antenna/update: {entry}")
        self._ok({"antenna": entry, "antennas": lst})

    def _action_antenna_remove(self, body):
        aid = str(body.get("id", "")).strip()
        lst = _load_antennas()
        entry = _find_antenna(lst, aid)
        if not entry:
            return self._err(404, "no such antenna")
        lst = [a for a in lst if a["id"] != aid]
        _save_antennas(lst)
        was_active = (_C.get("ANTENNA", "") == aid)
        log_action(f"antenna/remove: {aid} (was_active={was_active})")
        self._ok({"removed": aid, "antennas": lst, "was_active": was_active})

    def _action_station_set(self, body):
        """Config-only: writes ANTENNA/BAND/DIAL_HZ/TX_PWR to station.conf.
        Never touches the CAT port — qso.py/rx-loop only ever READ these keys
        (at their own process start) and verify the operator has manually
        retuned the radio to match before every key-up; this endpoint can't
        retune the rig itself, by design (see BANDS' comment above)."""
        if _proc_running(QSO_PY):
            return self._err(409, "stop the chaser before changing station config")
        aid = str(body.get("antenna_id", "")).strip()
        band = str(body.get("band", "")).strip()
        lst = _load_antennas()
        entry = _find_antenna(lst, aid)
        if not entry:
            return self._err(400, "no such antenna")
        if band not in BANDS:
            return self._err(400, "unknown band")
        if band not in entry["bands"]:
            return self._err(400, f"{entry['name']} is not built for {band}")
        try:
            tx_pwr = float(body.get("tx_pwr"))
        except (TypeError, ValueError):
            return self._err(400, "tx_pwr must be numeric")
        if tx_pwr <= 0:
            return self._err(400, "tx_pwr must be positive")
        cap = entry.get("max_watts") or DEFAULT_MAX_W
        band_cap = BANDS[band]["cap_w"]
        if band_cap:
            cap = min(cap, band_cap)
        cap = min(cap, ABS_MAX_W)
        if tx_pwr > cap:
            return self._err(400, f"{tx_pwr:g} W exceeds the safe cap for this antenna/band ({cap:g} W)")
        freq_hz = BANDS[band]["freq_hz"]
        tx_pwr_out = int(tx_pwr) if tx_pwr == int(tx_pwr) else tx_pwr
        station_config.save_keys({"ANTENNA": aid, "BAND": band, "DIAL_HZ": freq_hz, "TX_PWR": tx_pwr_out})
        # dashboard's own CONFIG is live in-memory (no restart needed — /config
        # reflects this immediately). rx-loop.sh dot-sources station.conf ONCE
        # at its own process start and never re-reads it, so its BAND value
        # (waterfall image title only — it doesn't gate anything safety-related)
        # goes stale until that process is replaced; auto-restart it here so
        # the operator never has to remember a manual step. qso.py needs no
        # restart either: chase/start always spawns a brand-new process, which
        # reads station.conf fresh at that moment.
        CONFIG.update(antenna=aid, band=band, dial_hz=freq_hz, tx_pwr=str(tx_pwr_out))
        rx_restarted = False
        if _proc_running(RXLOOP_SH) and not DRYRUN:
            _pkill(RXLOOP_SH)
            _spawn_detached(["bash", RXLOOP_SH], os.path.join(DATA, "rx-loop.log"))
            rx_restarted = True
        log_action(f"station/set: antenna={aid} band={band} dial_hz={freq_hz} tx_pwr={tx_pwr_out} "
                   f"rx_restarted={rx_restarted}")
        self._ok({
            "antenna": aid, "band": band, "dial_hz": freq_hz, "tx_pwr": tx_pwr_out,
            "rx_restarted": rx_restarted,
            "note": f"Saved and applied. Retune the radio to {freq_hz/1e6:.3f} MHz before chasing — "
                    f"config takes effect immediately; nothing else to restart."
        })

    def _action_qrz_sync(self):
        """Spawns logsync.py detached (real upload, not --dry-run) -- this
        server is single-threaded, so a real sync (sequential HTTPS POSTs to
        QRZ, one per record) must run out-of-process or it would freeze the
        whole dashboard for everyone until it finished. Never touches the
        rig/CAT port; safe to run regardless of chaser state."""
        if not logsync.read_key():
            return self._err(400, "no QRZ API key configured yet — see the QRZ Logbook widget")
        if _proc_running(LOGSYNC_PY):
            log_action("qrz/sync: refused, already syncing")
            return self._err(409, "a sync is already in progress")
        if DRYRUN:
            log_action(f"[DRYRUN] would sync to QRZ: python3 {LOGSYNC_PY}")
            return self._ok({"started": True, "dryrun": True})
        _spawn_detached(["python3", LOGSYNC_PY], QRZ_SYNC_LOG)
        log_action(f"qrz/sync: spawned python3 {LOGSYNC_PY}")
        self._ok({"started": True})

    def _action_qrz_refresh(self):
        """Spawns qrz_fetch.py detached — pages the whole QRZ logbook into
        data/qrz-logbook.json for the Logbook widget's confirmation view.
        Same out-of-process rationale as _action_qrz_sync; never touches
        the rig."""
        if not logsync.read_key():
            return self._err(400, "no QRZ API key configured yet — see the QRZ Logbook widget")
        if _proc_running(QRZ_FETCH_PY):
            log_action("qrz/refresh: refused, already fetching")
            return self._err(409, "a fetch is already in progress")
        if DRYRUN:
            log_action(f"[DRYRUN] would fetch QRZ logbook: python3 {QRZ_FETCH_PY}")
            return self._ok({"started": True, "dryrun": True})
        _spawn_detached(["python3", QRZ_FETCH_PY], QRZ_SYNC_LOG)
        log_action(f"qrz/refresh: spawned python3 {QRZ_FETCH_PY}")
        self._ok({"started": True})

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), H) as srv:
        print(f"COTA dashboard: http://localhost:{PORT}"
              + (" [COA_DRYRUN]" if DRYRUN else ""))
        srv.serve_forever()
