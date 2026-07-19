#!/usr/bin/env python3
"""Claude on AIR QSO chaser — answers CQs, sequences to 73, logs ADIF.

ATTENDED OPERATION ONLY: you are the licensed control operator, present at the
station. Safety rails: every frame ~13.5 s keyed under an independent unkey
watchdog; repeat cap on identical transmissions; frequency read-back verified
before EVERY key-up; abort on any mismatch; PTT release verified after every frame.

Usage: qso.py --max-qsos 1
Events print to stdout (one line each) for live monitoring.
"""
import sys, os, time, json, re, subprocess, argparse, datetime, collections, calendar

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ft8synth
import station_config
import decode_store
import dxcc

_C = station_config.load()          # station.conf; every key optional
DATA    = os.path.expanduser(_C.get("DATA", os.path.join(_ROOT, "data")))
ADIF    = os.path.expanduser(_C.get("ADIF", "~/.local/share/WSJT-X/wsjtx_log.adi"))
QSOLOG  = os.path.join(DATA, "qso-attempts.jsonl")
TARGET_REQ = os.path.join(DATA, "target-request.json")   # written by dashboard.py's "pick" chip
SKIP_REQ   = os.path.join(DATA, "skip-request.json")     # written by dashboard.py's "Skip current target"
CAT     = _C.get("CAT_PORT", "/dev/ttyUSB0")
RIGCTL  = ["rigctl", "-m", _C.get("RIG_MODEL", "3060"), "-r", CAT,
           "-s", _C.get("CAT_BAUD", "19200")]
SINK    = _C.get("PA_SINK",
                 "alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo")
DIAL    = int(_C.get("DIAL_HZ", 14074000))
MYCALL, MYGRID = _C.get("MYCALL", "N0CALL"), _C.get("MYGRID", "AA00")
BAND    = _C.get("BAND", "20m")
MAX_REPEAT = int(_C.get("MAX_REPEAT", 6))    # hard cap: no more than 6 similar transmits
STATE_RETRY = int(_C.get("STATE_RETRY", 5))  # per-state retries before giving up on a target (<= MAX_REPEAT)
WATCHDOG_S = float(_C.get("WATCHDOG_S", 14))
SNR_FLOOR = int(_C.get("SNR_FLOOR", -16))    # don't call CQs weaker than this (dB) — reciprocity at 5-10 W
CQ_MODIFIERS_OK = {m.strip().upper() for m in
                   _C.get("CQ_MODIFIERS_OK", "NA,USA,US,WI,4,POTA,SOTA,IOTA,QRP").split(",")
                   if m.strip()}              # directed-CQ modifiers we may answer

def ev(msg):
    print(f"{datetime.datetime.utcnow().strftime('%H:%M:%S')} {msg}", flush=True)

# ---- engine state observer (display only — no engine logic reads this) ----
ENGINE_JSON = os.path.join(DATA, "engine.json")
_engine = {"utc": "", "state": "init", "target": None, "grid": None,
           "tx": False, "dx_mode": False, "msg": None, "offset": None, "next_tx_epoch": None,
           # tx_msg/tx_offset: the actual FT8 content of the last real (or
           # about-to-happen) transmission, for the dashboard's TX-transparency
           # panel. Deliberately separate from "msg" above, which doubles as a
           # transient status/reason string (also used for tx_abort reasons) —
           # tx_msg must never show a stale abort reason as if it were content.
           "tx_msg": None, "tx_offset": None,
           # qso_step: the inner state machine's own "call"/"rrpt"/"b73"
           # value, mirrored for the dashboard's step-of-4 progress display.
           # Display only — nothing reads this back into control flow.
           "qso_step": None}

def write_engine_state(**kw):
    """Publish engine state for the dashboard map. Atomic (tmp + os.replace);
    never raises — a display glitch must not disturb a QSO."""
    _engine.update(kw)
    _engine["utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    try:
        tmp = ENGINE_JSON + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_engine, f)
        os.replace(tmp, ENGINE_JSON)
    except OSError:
        pass

def rig(*args):
    r = subprocess.run(RIGCTL + list(args), capture_output=True, text=True, timeout=10)
    return r.stdout.strip()

def now(): return time.time()
def slot_start(t): return int(t // 15) * 15
def parity(t): return 0 if (int(t) % 30) < 15 else 1
def slot_parity_of(slot_hhmmss):
    s = int(slot_hhmmss[-2:]) + int(slot_hhmmss[2:4]) * 60
    return 0 if (s % 30) < 15 else 1

# ---- pure decode-classification helpers (unit tests: tools/test_sequencer.py) ----
_ARTIFACT_RE = re.compile(r"^[a-z][0-9]$|^\?+$")   # jt9 trailing markers: a1..a6, ?
GRID_RE = re.compile(r"^[A-R]{2}\d{2}$")
CALL_RE = re.compile(r"^(?:[A-Z0-9]{1,4}/)?(?:[A-Z]{1,2}|\d[A-Z])\d[A-Z0-9]{0,3}[A-Z]"
                     r"(?:/[A-Z0-9]{1,4})?$")
CONTEST_MODS = {"TEST", "RU", "FD", "WW", "VHF", "UHF"}   # contest-style CQs: never answer
BUSY_TAIL_RE = re.compile(r"^(R?[+-]\d\d|RR73|RRR|73)$")

def toks(msg):
    """Uppercased message tokens with trailing jt9 decoder artifacts stripped."""
    t = msg.split()
    while t and _ARTIFACT_RE.match(t[-1]):
        t.pop()
    return [x.upper() for x in t]

def parse_cq(msg):
    """Parse a CQ decode -> (call, grid, modifier); grid/modifier are "" when
    absent. Returns None when the message is not a structurally valid CQ."""
    p = toks(msg)
    if len(p) < 2 or p[0] != "CQ":
        return None
    grid = ""
    if len(p) >= 3 and GRID_RE.match(p[-1]) and p[-1] != "RR73":
        grid, p = p[-1], p[:-1]
    body = p[1:]
    if len(body) == 1:
        mod, call = "", body[0]
    elif len(body) == 2:
        mod, call = body
    else:
        return None
    if not CALL_RE.match(call):
        return None
    return call, grid, mod

def cq_answerable(mod, dx_mode=False):
    """Directed-CQ policy (ZL2IFB guide): plain CQ or whitelisted modifier ->
    answer. Contest CQs (TEST/RU/FD/WW, CONTEST_MODS) -> always skip,
    unaffected by DX Mode. 'CQ DX' -> skip UNLESS dx_mode is True (DX Mode
    un-skips directed DX calls; see main()'s --dx-only and dx_filter_ok() for
    the accompanying country filter). Any other directed CQ (continents,
    other states) -> skip. Case-insensitive."""
    if not mod:
        return True
    m = mod.upper()
    if m in CONTEST_MODS:
        return False
    if m == "DX":
        return dx_mode
    return m in CQ_MODIFIERS_OK

def dx_filter_ok(call, dx_mode, mycall=None):
    """DX Mode's country/DXCC-entity candidate gate. dx_mode False -> always
    True (no behavior change, existing default). dx_mode True -> only calls
    that resolve to a DIFFERENT, KNOWN DXCC entity than `mycall` (default
    MYCALL) pass — see dxcc.is_dx_call()'s fail-closed unmapped-prefix
    behavior. Applies to EVERY CQ candidate, not just directed CQ DX ones: a
    same-country plain CQ is excluded too, per the DX Mode product decision
    (country filter + DX-modifier un-skip apply together)."""
    return (not dx_mode) or dxcc.is_dx_call(call, mycall or MYCALL)

def target_busy(msg, target, mycall=None):
    """True when `target` is heard working a DIFFERENT station: report,
    R-report, or QSO-ending token addressed to a call that isn't ours."""
    p = toks(msg)
    return (len(p) == 3 and p[1] == target.upper() and p[0] != "CQ"
            and p[0] != (mycall or MYCALL).upper()
            and bool(BUSY_TAIL_RE.match(p[2])))

def target_free(msg, target):
    """True when `target` is heard calling CQ or ending a QSO (RR73/RRR/73):
    he is free (or about to be free) for us to call."""
    p = toks(msg)
    if p[:1] == ["CQ"] and target.upper() in p[1:]:
        return True
    return len(p) == 3 and p[1] == target.upper() and p[2] in ("RR73", "RRR", "73")

def is_target_cq(msg, target):
    """True when `target` is the one calling CQ in this decode."""
    p = toks(msg)
    return p[:1] == ["CQ"] and target.upper() in p[1:]

def read_decodes(from_line):
    """Same (total_line_count, new_records) contract as always — callers'
    line-cursor math is unchanged. Only the source changed: hour-bucketed
    files under data/decodes/ (today's UTC date) instead of one flat file;
    see decode_store.py. Scoped to today because a chase session never spans
    more than one UTC day in practice, which also bounds this read instead of
    re-reading the station's entire history every poll."""
    today = time.strftime("%y%m%d", time.gmtime())
    lines = decode_store.read_all(DATA, since_date_yymmdd=today)
    out = []
    for l in lines[from_line:]:
        try: out.append(json.loads(l))
        except json.JSONDecodeError: pass
    return len(lines), out

def select_target(cqs, requested_call):
    """Choose which CQ to answer this cycle. `cqs` is this cycle's list of
    (decode, call, grid) tuples, ALREADY filtered by cq_answerable()/SNR_FLOOR
    and sorted best-first by the caller's SNR/pileup ranking. If the operator
    requested a specific call (dashboard "pick" chip) and it's among this
    cycle's answerable CQs, honor that pick over the automatic ranking;
    otherwise fall back to the auto-picked best candidate (cqs[0]). Pure —
    deliberately only chooses among candidates that already passed the
    etiquette/SNR-floor filters, so a manual pick can never bypass them."""
    if requested_call:
        for c in cqs:
            if c[1] == requested_call:
                return c
    return cqs[0]


def skip_is_requested(request_ts_epoch, since_epoch):
    """True when a skip-request's timestamp is at/after `since_epoch` — i.e.
    issued during (not before) the current target pursuit. Pure; guards
    against a stale skip click (aimed at a PREVIOUS target) instantly
    aborting a brand new one that only just started."""
    return request_ts_epoch is not None and request_ts_epoch >= since_epoch


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _read_target_request():
    """Requested callsign from the dashboard's "pick" chip, or "" if none/
    unreadable. Never raises — a malformed/missing request file just means
    no manual pick this cycle, not a chaser crash."""
    obj = _read_json(TARGET_REQ)
    if not isinstance(obj, dict):
        return ""
    return str(obj.get("call", "")).strip().upper()


def _read_skip_ts_epoch():
    """Epoch seconds of the last skip-request, or None if none/unreadable."""
    obj = _read_json(SKIP_REQ)
    if not isinstance(obj, dict):
        return None
    ts = obj.get("ts")
    if not ts:
        return None
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return None


def worked_calls():
    """Callsigns already worked TODAY (UTC) — dupe rule: no same-call dupes per day."""
    w = set()
    today = time.strftime("%Y%m%d", time.gmtime())
    try:
        adi = open(ADIF, errors="ignore").read()
        for rec in adi.split("<eor>"):
            c = re.search(r"<call:(\d+)>", rec, re.I)
            d = re.search(r"<qso_date:\d+>(\d{8})", rec, re.I)
            if c and (d is None or d.group(1) == today):
                call = rec[c.end():c.end() + int(c.group(1))]
                w.add(call.upper())
    except FileNotFoundError:
        pass
    return w

def occupied_freqs(decs, our_parity):
    """Only signals transmitting in OUR tx-parity slots occupy our slot — the
    other parity transmits while we listen and cannot collide with us."""
    return [d["freq"] for d in decs if slot_parity_of(d["slot"]) == our_parity]

def pick_offset(decs, our_parity, exclude_hz=()):
    """Clear TX offset in 400-2450 Hz (split operation, ZL2IFB: never call on
    someone else's frequency). Occupancy counts only decodes in our tx parity.
    Clearance >=60 Hz from each neighbour required, >=100 Hz preferred; among
    near-equal gaps (within 20% of the best) prefer the HIGHER frequency;
    never lands within 100 Hz of anything in exclude_hz."""
    occ = sorted(set(f for f in occupied_freqs(decs, our_parity) + [int(x) for x in exclude_hz]
                     if 340 < f < 2510))
    edges = [340] + occ + [2510]
    cands = []                              # (clearance, candidate_freq, gap_width)
    for a, b in zip(edges, edges[1:]):
        if b <= a:
            continue
        c = max(400, min(2450, (a + b) // 2))
        if any(abs(c - x) <= 100 for x in exclude_hz):
            continue
        cands.append((min(c - a, b - c), c, b - a))
    if not cands:
        return 1500, 0
    pool = ([x for x in cands if x[0] >= 100]           # prefer >=100 Hz clearance
            or [x for x in cands if x[0] >= 60]         # accept >=60 Hz
            or cands)                                   # last resort: best available
    best_clear = max(x[0] for x in pool)
    clear, f0, gap = max((x for x in pool if x[0] >= 0.8 * best_clear),
                         key=lambda x: x[1])            # near-equal -> higher freq
    return f0, gap

def synth_wav(msg, f0, path):
    symbols = ft8synth.symbols_from_ft8code(msg)
    sig = ft8synth.synth(symbols, f0) * 0.9
    import numpy as np, wave
    total = int(13.4 * ft8synth.RATE)          # 0.5 lead + 12.64 frame + 0.26 tail
    audio = np.zeros(total)
    st = int(ft8synth.LEAD * ft8synth.RATE)
    audio[st:st + len(sig)] = sig
    w = wave.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(ft8synth.RATE)
    w.writeframes((audio * 32767).astype("<i2").tobytes()); w.close()

def compute_tx_boundary(t, our_parity):
    """Next slot-start epoch (multiple of 15) with parity == our_parity —
    joins the current slot immediately if we're within the first 1.8 s of a
    same-parity slot (decoders still accept a slightly-late start), otherwise
    schedules the next matching-parity slot. Pure extraction of transmit()'s
    scheduling math, no behavior change; see tools/test_sequencer.py."""
    sl = slot_start(t)
    if parity(sl) == our_parity and (t - sl) < 1.8:
        return sl                           # join the current slot immediately
    boundary = sl + 15
    while parity(boundary) != our_parity:
        boundary += 15
    return boundary


def _tx_waterfall(wav):
    """Best-effort spectrogram of the TX audio we're about to send, for the
    dashboard's TX-transparency panel. Display-only: never raises, never
    blocks TX meaningfully (small file, tight timeout) — a glitch here must
    never touch the TX safety chain below."""
    try:
        subprocess.run(["sox", wav, "-n", "spectrogram", "-x", "900", "-y", "257",
                         "-z", "70", "-l", "-o", os.path.join(DATA, "tx_waterfall.png")],
                        capture_output=True, timeout=3)
    except Exception:
        pass


def transmit(msg, f0, tx_count, our_parity):
    """One frame in OUR parity slot only. Late start allowed up to 1.8 s into the
    slot (decoders accept it); otherwise waits for our next parity slot."""
    if tx_count[msg] >= MAX_REPEAT:
        ev(f"ABORT: '{msg}' hit the {MAX_REPEAT}-repeat cap")
        write_engine_state(state="tx_abort", msg=f"repeat cap ({MAX_REPEAT}x): {msg}", next_tx_epoch=None)
        return False
    wav = os.path.join(DATA, "tx.wav")
    synth_wav(msg, f0, wav)
    _tx_waterfall(wav)
    f = rig("f")
    if f != str(DIAL):
        ev(f"ABORT TX: dial reads {f}, expected {DIAL} — NOT keying")
        write_engine_state(state="tx_abort", msg=f"dial mismatch: reads {f}, expected {DIAL}", next_tx_epoch=None)
        return False
    # choose the key-up moment: our-parity slot, on time or <=1.8 s late
    boundary = compute_tx_boundary(now(), our_parity)
    if parity(boundary) != our_parity:      # belt and suspenders
        ev("ABORT TX: could not schedule a slot with our parity")
        write_engine_state(state="tx_abort", msg="scheduling failed", next_tx_epoch=None)
        return False
    write_engine_state(next_tx_epoch=boundary, msg=msg, offset=f0, tx_msg=msg, tx_offset=f0)
    wd = subprocess.Popen(["bash", "-c",
        f"sleep {WATCHDOG_S + max(0, boundary - now()):.1f}; " +
        " ".join(RIGCTL) + " T 0 >/dev/null 2>&1"])
    time.sleep(max(0, boundary - 0.7 - now()))
    rig("T", "1")
    ev(f"TX #{sum(tx_count.values())+1} '{msg}' @ {f0} Hz ({tx_count[msg]+1}x this msg, ~13.5 s keyed)")
    write_engine_state(tx=True, msg=msg, offset=f0, next_tx_epoch=None, tx_msg=msg, tx_offset=f0)
    subprocess.run(["paplay", f"--device={SINK}", wav])
    rig("T", "0")
    wd.terminate()
    ptt = rig("t")
    ev(f"unkeyed, PTT verify: {ptt}")
    write_engine_state(tx=False)
    tx_count[msg] += 1
    if ptt != "0":
        ev("ABORT: PTT did not release!")
        write_engine_state(state="tx_abort", msg="PTT did not release!")
        return False
    return True

def log_qso(call, grid, sent, rcvd, f0, t_on):
    t = time.gmtime()
    d = time.strftime("%Y%m%d", t); tm = time.strftime("%H%M%S", t)
    d_on = time.strftime("%Y%m%d", time.gmtime(t_on)); tm_on = time.strftime("%H%M%S", time.gmtime(t_on))
    freq = (DIAL + f0) / 1e6
    def fld(k, v): return f"<{k}:{len(str(v))}>{v}" if str(v) else ""
    rec = (fld("call", call) + fld("gridsquare", grid) + fld("mode", "FT8") +
           fld("rst_sent", sent) + fld("rst_rcvd", rcvd) +
           fld("qso_date", d_on) + fld("time_on", tm_on) +
           fld("qso_date_off", d) + fld("time_off", tm) +
           fld("band", BAND) + fld("freq", f"{freq:.6f}") +
           fld("station_callsign", MYCALL) + fld("my_gridsquare", MYGRID) +
           fld("tx_pwr", _C.get("TX_PWR", "5")) + " <eor>\n")
    if not os.path.exists(ADIF):
        os.makedirs(os.path.dirname(ADIF), exist_ok=True)
        open(ADIF, "a").write("WSJT-X ADIF Export<eoh>\n")
    open(ADIF, "a").write(rec)
    open(QSOLOG, "a").write(json.dumps({"call": call, "grid": grid, "sent": sent,
        "rcvd": rcvd, "utc": tm, "date": d, "freq_hz": DIAL + f0}) + "\n")
    ev(f"LOGGED QSO: {call} {grid} sent {sent} rcvd {rcvd} -> wsjtx_log.adi")
    write_engine_state(state="logged")

def write_session_report(t_start, t_end, args, completed, session_qsos, session_attempts):
    """End-of-run summary for one chase session -> data/session-report.txt
    (roadmap 4.4). Display-only, like write_engine_state: a report-writing
    glitch must never mask a completed session, so this never raises.
    Called once, at the single run-end point in main() shared by both the
    --max-qsos and --minutes paths — it does not touch the TX safety chain
    (watchdog/frequency read-back/PTT/transmit()/boundary timing), it only
    summarizes data already produced by main()'s orchestration loop."""
    try:
        outcomes = collections.Counter(a.get("outcome", "?") for a in session_attempts)
        total_frames = sum(a.get("frames_sent", 0) or 0 for a in session_attempts)
        goal = (f"{args.minutes:g} min budget" if args.minutes
                else f"{args.max_qsos} QSO(s)")
        lines = []
        lines.append("=== COTA chase session report ===")
        lines.append(f"start: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t_start))}Z")
        lines.append(f"end:   {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t_end))}Z"
                     f"  ({(t_end - t_start) / 60:.1f} min elapsed)")
        lines.append(f"target: {goal}")
        lines.append("")
        lines.append(f"QSOs completed: {completed}")
        for q in session_qsos:
            lines.append(f"  {q['call']:<10} {q['grid'] or '----':<6} "
                         f"sent {q['sent']:<4} rcvd {q['rcvd']}")
        lines.append("")
        lines.append(f"attempts: {len(session_attempts)}"
                     + (f"  ({', '.join(f'{k}={v}' for k, v in sorted(outcomes.items()))})"
                        if outcomes else ""))
        lines.append(f"TX frames sent: {total_frames}")
        lines.append("")
        lines.append("per-target summary:")
        if session_attempts:
            for a in session_attempts:
                lines.append(f"  {a['call']:<10} {a['grid'] or '----':<6} "
                             f"{a['outcome']:<5} frames={a.get('frames_sent', 0):<3} "
                             f"rcvd={a.get('rcvd_report') or '-'}")
        else:
            lines.append("  (no targets attempted)")
        path = os.path.join(DATA, "session-report.txt")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
        ev(f"session report written: {path}")
    except OSError as e:
        ev(f"WARN: could not write session report: {e}")


def wait_slot_decodes(target_parity, line_pos, deadline_margin=2.0):
    """Wait for rx-loop to publish decodes of the next target-parity slot.
    Uses the slot's ABSOLUTE start time for the deadline (the old version compared
    against t%15, which can never exceed the margin — it hung on empty slots).
    Returns (new_line_pos, slot_decodes, all_new_decodes) — slot_decodes is []
    if the slot truly had none; all_new_decodes covers every slot since line_pos
    (lets the caller track parity flips and QSOs on other slots)."""
    while True:                              # find the current/next target-parity slot
        t = now(); sl = slot_start(t)
        if parity(sl) == target_parity:
            break
        time.sleep(0.2)
    slot_id = time.strftime("%H%M%S", time.gmtime(sl))
    deadline = sl + 14.2 + deadline_margin   # absolute wall-clock deadline
    while now() < deadline:
        line_pos2, decs = read_decodes(line_pos)
        mine = [d for d in decs if d["slot"] == slot_id]
        if mine:
            return line_pos2, mine, decs
        time.sleep(0.2)
    line_pos2, decs = read_decodes(line_pos)
    return line_pos2, [d for d in decs if d["slot"] == slot_id], decs

def build_argparser():
    """Pure ArgumentParser construction for main() — separated out so the
    --dx-only flag (and the existing ones) are unit-testable without
    invoking main()'s hunting loop / radio I/O. No behavior change to
    main()."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-qsos", type=int, default=1)
    ap.add_argument("--minutes", type=float, default=0,
                    help="time budget: hunt until this many minutes pass (finishes any in-progress QSO first); implies unlimited QSO count unless --max-qsos also given")
    ap.add_argument("--dx-only", action="store_true",
                    help="DX Mode: chase only stations outside your own DXCC "
                         "entity/country (and allow directed 'CQ DX'); all "
                         "existing etiquette rails (SNR floor, busy-hold, "
                         "repeat cap, split-calling, watchdog) are unchanged")
    return ap

def main():
    ap = build_argparser()
    args = ap.parse_args()
    dx_mode = args.dx_only
    if args.minutes and args.max_qsos == 1:
        args.max_qsos = 999
    stop_at = now() + args.minutes * 60 if args.minutes else None
    ev(f"chaser start: target {args.max_qsos} QSO(s)"
       + (f" / {args.minutes:g} min budget" if stop_at else "")
       + (" [DX MODE]" if dx_mode else "")
       + f", dial {DIAL}, watchdog {WATCHDOG_S}s, repeat cap {MAX_REPEAT}")
    write_engine_state(state="hunting", target=None, grid=None, dx_mode=dx_mode)
    if rig("t") != "0":
        ev("ABORT: PTT not idle at start"); return
    completed = 0
    t_run_start = now()                     # session bounds for the 4.4 end-of-run report
    session_qsos = []       # completed-QSO summaries (call/grid/reports) this run
    session_attempts = []   # per-target attempt outcomes this run
    line_pos, _ = read_decodes(0)          # skip history; hunt fresh CQs only
    tried = set()
    keyed_s, breather_mark = 0.0, 360.0    # ~13.5 s keyed per frame; breather each 6 min
    max_targets = max(4, args.max_qsos * 3) if not stop_at else 10**6
    while completed < args.max_qsos:
        if stop_at and now() >= stop_at:
            ev(f"time budget reached: {completed} QSO(s) in {args.minutes:g} min")
            break
        if len(tried) >= max_targets:
            ev(f"stopping: {len(tried)} targets tried, {completed} completed")
            break
        worked = worked_calls()
        # ---- hunt: watch fresh decodes for a CQ we can chase ----
        line_pos, decs = read_decodes(line_pos)
        cqs = []
        for d in decs:
            pc = parse_cq(d["msg"])          # grid optional; compound calls OK
            if not pc:
                continue
            c_call, c_grid, c_mod = pc
            if c_call == MYCALL or c_call in worked or c_call in tried:
                continue
            if not cq_answerable(c_mod, dx_mode):
                ev(f"skip CQ {c_mod} {c_call} — directed CQ not for us")
                continue
            if not dx_filter_ok(c_call, dx_mode):
                ev(f"skip {c_call} — DX Mode: not confirmed DX (same/unknown country)")
                continue
            if d["snr"] < SNR_FLOOR:
                ev(f"skip {c_call} at {d['snr']} dB — below SNR floor {SNR_FLOOR} (reciprocity)")
                continue
            cqs.append((d, c_call, c_grid))
        if not cqs:
            time.sleep(1); continue
        # pileup penalty: each other station heard calling them recently costs 6 dB
        _, recent_all = read_decodes(max(0, line_pos - 120))
        def competitors(c):
            return len({r["msg"].split()[1] for r in recent_all
                        if r["msg"].startswith(c + " ") and len(r["msg"].split()) >= 2
                        and r["msg"].split()[1] != MYCALL})
        cqs.sort(key=lambda x: -(x[0]["snr"] - 6 * competitors(x[1])))
        requested = _read_target_request()
        d, call, grid = select_target(cqs, requested)
        their_parity = slot_parity_of(d["slot"])
        our_parity = 1 - their_parity
        _, recent = read_decodes(max(0, line_pos - 80))
        f0, gap = pick_offset(recent[-60:], our_parity)
        ev(f"TARGET {call} {grid} (CQ {d['snr']} dB @ {d['freq']} Hz, their parity {'even' if their_parity==0 else 'odd'}) -> our offset {f0} Hz (gap {gap} Hz)")
        write_engine_state(state="calling", target=call, grid=grid, offset=f0)
        tried.add(call)
        target_start_ts = now()
        tx_count = __import__("collections").defaultdict(int)
        report_to_them = f"{d['snr']:+03d}"

        # ---- state machine ----
        state, sent_rpt, rcvd_rpt, t_on = "call", None, None, now()
        fails = 0                 # unanswered listen cycles in an answered state
        offset_tries = 0          # calls sent at the current clear offset ("call" state)
        offsets_used = [f0]       # every offset we've called on (excluded ±100 on re-pick)
        busy_cycles = 0           # tx cycles skipped while target works someone else
        skip_tx = False           # busy-hold: hold fire for one cycle
        their_freq = d["freq"]
        answered_f0 = None        # offset that actually got through (sticky once answered)
        while state not in ("done", "fail"):
            write_engine_state(qso_step=state)
            if skip_is_requested(_read_skip_ts_epoch(), target_start_ts):
                ev(f"skip requested for {call} — abandoning target")
                state = "fail"
                break
            # wait until we are inside their slot (they talk), then TX on ours
            if state == "call":  msg = f"{call} {MYCALL} {MYGRID}"
            elif state == "rrpt": msg = f"{call} {MYCALL} R{report_to_them}"
            elif state == "b73": msg = f"{call} {MYCALL} 73"
            # split operation (ZL2IFB): ALWAYS call on our own clear offset, never
            # on the target's frequency; once answered, stay where they heard us
            use_f0 = answered_f0 if answered_f0 is not None else f0
            # wait for their-parity slot to finish so our boundary is next
            while not (parity(slot_start(now())) == their_parity and (now() - slot_start(now())) > 13.6):
                time.sleep(0.1)
            if skip_tx:
                skip_tx = False
                ev(f"busy-hold: {call} working someone else — skipping our tx cycle ({busy_cycles}/4)")
            else:
                if not transmit(msg, use_f0, tx_count, our_parity):
                    state = "fail"; break
                keyed_s += 13.5
                if state == "call":
                    offset_tries += 1
                if state == "b73":
                    state = "done"; break
            # listen on their next slot
            line_pos, mine, fresh = wait_slot_decodes(their_parity, line_pos)
            # track the target: freq + slot parity from their most recent decode
            heard = None
            for dd in fresh:
                pp = toks(dd["msg"])
                if (len(pp) >= 2 and pp[0] != "CQ" and pp[1] == call) or is_target_cq(dd["msg"], call):
                    heard = dd
            if heard is not None:
                their_freq = heard["freq"]
                hp = slot_parity_of(heard["slot"])
                if hp != their_parity:
                    their_parity, our_parity = hp, 1 - hp
                    ev(f"{call} flipped slot parity ({their_freq} Hz) — we now tx on "
                       f"{'even' if our_parity == 0 else 'odd'} slots")
                if state == "call" and rcvd_rpt is None:
                    report_to_them = f"{heard['snr']:+03d}"   # freshest SNR before first use
            # did they answer US?
            answered = False
            for dd in fresh:
                mm = toks(dd["msg"])
                if len(mm) >= 3 and mm[0] == MYCALL and mm[1] == call:
                    tok = mm[2]
                    if state == "call" and re.match(r"^[R]?[+-]\d\d$", tok):
                        rcvd_rpt = tok.lstrip("R")
                        sent_rpt = f"R{report_to_them}"
                        answered_f0 = use_f0
                        ev(f"ANSWERED: {call} gives us {rcvd_rpt} -> sending R{report_to_them}")
                        write_engine_state(state="qso")
                        state = "rrpt"; fails = 0; busy_cycles = 0; skip_tx = False
                        answered = True
                        break
                    if tok in ("RR73", "RRR", "73") and state in ("rrpt", "call"):
                        if rcvd_rpt is None: rcvd_rpt = ""    # never log a fake -99
                        ev(f"{call} sends {tok} — QSO complete, sending courtesy 73")
                        log_qso(call, grid, report_to_them, rcvd_rpt,
                                answered_f0 if answered_f0 is not None else use_f0, t_on)
                        completed += 1
                        session_qsos.append({"call": call, "grid": grid,
                            "sent": report_to_them, "rcvd": rcvd_rpt})
                        state = "b73"
                        answered = True
                        break
            if answered:
                continue
            # no answer this cycle — classify what the target is doing
            if state == "rrpt" and heard is not None and is_target_cq(heard["msg"], call):
                # he lost our R-report and went back to CQ: re-pick a fresh clear
                # offset (exclude the old one ±100) and keep sending the R-report
                old = answered_f0 if answered_f0 is not None else f0
                if old not in offsets_used:
                    offsets_used.append(old)
                _, recent = read_decodes(max(0, line_pos - 80))
                nf0, ngap = pick_offset(recent[-60:], our_parity, exclude_hz=offsets_used)
                ev(f"{call} is CQing again — he lost our R-report; moving {old} -> {nf0} Hz "
                   f"(gap {ngap} Hz), still sending R{report_to_them}")
                answered_f0 = nf0
                offsets_used.append(nf0)
                write_engine_state(offset=nf0)
                fails += 1
                if fails >= MAX_REPEAT:
                    ev(f"{call}: R-report never acknowledged after {MAX_REPEAT} cycles — giving up")
                    state = "fail"
                continue
            if heard is not None and target_busy(heard["msg"], call) and not target_free(heard["msg"], call):
                if state == "rrpt":
                    # he's reporting to a DIFFERENT call mid-QSO with us: abort now
                    ev(f"{call} is reporting to someone else mid-QSO — aborting target")
                    state = "fail"
                    continue
                busy_cycles += 1
                if busy_cycles > 4:
                    ev(f"{call} still busy after 4 skipped cycles — moving on")
                    state = "fail"
                else:
                    skip_tx = True      # hold fire; NOT counted as a failed call
                continue
            # plain no-copy
            if state == "call":
                if offset_tries >= 3:
                    if len(offsets_used) == 1:
                        _, recent = read_decodes(max(0, line_pos - 80))
                        nf0, ngap = pick_offset(recent[-60:], our_parity, exclude_hz=offsets_used)
                        ev(f"no answer at {f0} Hz after 3 calls — new clear offset {nf0} Hz (gap {ngap} Hz)")
                        f0 = nf0
                        offsets_used.append(nf0)
                        offset_tries = 0
                        write_engine_state(offset=nf0)
                    else:
                        ev(f"no response from {call} after 6 calls on 2 offsets — moving on")
                        state = "fail"
            else:
                fails += 1
                if fails >= MAX_REPEAT:
                    ev(f"no response from {call} after {MAX_REPEAT} tries in state '{state}' — moving on")
                    state = "fail"
        ev(f"target {call}: {state} (completed {completed}/{args.max_qsos})")
        if state == "fail":
            write_engine_state(state="hunting", target=None, grid=None)
        attempt_rec = {"utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "call": call, "grid": grid, "their_cq_snr": d["snr"],
            "our_offset_hz": answered_f0 if answered_f0 is not None else f0,
            "frames_sent": sum(tx_count.values()),
            "rcvd_report": rcvd_rpt, "outcome": state}
        with open(os.path.join(DATA, "attempts.jsonl"), "a") as f:
            f.write(json.dumps(attempt_rec) + "\n")
        session_attempts.append(attempt_rec)
        # breather (ZL2IFB): after a failed target, and after every 6 min of
        # cumulative keyed time, sit out one full 15 s cycle before re-acquiring
        take_breather = state == "fail"
        if keyed_s >= breather_mark:
            breather_mark += 360.0
            take_breather = True
        if take_breather and completed < args.max_qsos:
            ev(f"breather: sitting out one 15 s cycle ({keyed_s:.0f} s keyed this session)")
            write_engine_state(state="breather", tx=False)
            time.sleep(15)
            write_engine_state(state="hunting")
    ev(f"DONE: {completed} QSO(s) completed and logged. PTT: {rig('t')}")
    write_engine_state(state="done", tx=False)
    write_session_report(t_run_start, now(), args, completed, session_qsos, session_attempts)

if __name__ == "__main__":
    main()
