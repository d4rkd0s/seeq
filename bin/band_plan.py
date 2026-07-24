"""bin/band_plan.py -- strict FCC Part 97 compliance checker.

Pure, stdlib-only. Checks three things before SeeQ is allowed to key up:
callsign format, frequency-vs-license-class privileges, and power caps.
Wired in as a hard gate by bin/doctor.py and bin/seeq's preflight() -- a
FAIL here blocks `seeq start`/`seeq chase`, not just a warning.

KNOWN LIMITATION: check_callsign() is format-only validation (regex shape
matching a real US amateur callsign), not a live FCC ULS or QRZ lookup --
it cannot confirm the callsign is actually licensed, only that it's
well-formed and not a leftover placeholder.

Only LICENSE_CLASS "general" is supported today (matches this station's
real license). Any other value is a hard, explicit refusal rather than a
guess -- extend GENERAL_PRIVILEGES-style tables for other classes before
trusting this checker with them.

Data current as of the 2026-07-23 compliance audit (Radio/CLAUDE.md's FCC
notes, cross-checked against 47 CFR 97.301/97.303(h)/97.313 and the ARRL
band chart). 60 m reflects the segment/power rule effective 2026-02-13.
"""
import re

# band (lowercase) -> {"data": (lo_hz, hi_hz), "phone": (lo_hz, hi_hz) or None}
# General class privileges. 60 m is NOT in here -- it's channelized, handled
# separately by CHANNELS_60M / SEGMENT_60M below.
GENERAL_PRIVILEGES = {
    "160m": {"data": (1800000, 2000000), "phone": (1800000, 2000000)},
    "80m":  {"data": (3525000, 3600000), "phone": (3800000, 4000000)},
    "40m":  {"data": (7025000, 7125000), "phone": (7175000, 7300000)},
    "30m":  {"data": (10100000, 10150000), "phone": None},
    "20m":  {"data": (14025000, 14150000), "phone": (14225000, 14350000)},
    "17m":  {"data": (18068000, 18110000), "phone": (18110000, 18168000)},
    "15m":  {"data": (21025000, 21200000), "phone": (21275000, 21450000)},
    "12m":  {"data": (24890000, 24930000), "phone": (24930000, 24990000)},
    "10m":  {"data": (28000000, 28300000), "phone": (28300000, 29700000)},
}

# 60 m (47 CFR 97.303(h), effective 2026-02-13): 4 channels, all modes,
# <=2.8 kHz BW, centered +/-1.5 kHz, 100 W ERP -- plus a new continuous
# 15 kHz segment at only 9.15 W ERP. No separate data/phone split like the
# other bands: same channels/segment/power apply to any mode here.
CHANNELS_60M = [5332000, 5348000, 5373000, 5405000]
CHANNEL_60M_HALF_WIDTH_HZ = 1500
SEGMENT_60M = (5351500, 5366500)
CHANNEL_60M_POWER_LIMIT_W = 100    # ERP
SEGMENT_60M_POWER_LIMIT_W = 9.15   # ERP

BAND_POWER_CAP_W = {"30m": 200}    # PEP, all license classes, no exceptions
ABSOLUTE_CEILING_W = 1500          # PEP, General/Extra ceiling

SUPPORTED_LICENSE_CLASSES = {"general"}

# US amateur callsign shape: 1-2 letter prefix (A/K/N/W; 'A' prefix's second
# letter, if present, must be A-L -- the AAA-ALZ ITU block), one digit,
# 1-3 letter suffix. Format-level only, see module docstring.
_CALLSIGN_RE = re.compile(r'^[AKNW][A-Z]?[0-9][A-Z]{1,3}$')
_PLACEHOLDER_CALLSIGNS = {"N0CALL", ""}


def check_callsign(callsign):
    """(ok, detail). Format-only validation -- see module docstring."""
    cs = (callsign or "").strip().upper()
    if cs in _PLACEHOLDER_CALLSIGNS:
        return False, "MYCALL is unset or a placeholder (N0CALL) -- set your real callsign in station.conf"
    if not _CALLSIGN_RE.match(cs):
        return False, f"'{cs}' doesn't match a valid US amateur callsign pattern"
    if cs[0] == "A" and len(cs) > 2 and cs[1].isalpha() and not ("A" <= cs[1] <= "L"):
        return False, f"'{cs}' starts with 'A' but the second letter must be A-L for a US-allocated prefix (AAA-ALZ)"
    return True, f"'{cs}' is a well-formed US amateur callsign (format-only check, not an FCC ULS/QRZ lookup)"


def _in_range(freq_hz, rng):
    return rng is not None and rng[0] <= freq_hz <= rng[1]


def _on_60m_channel(freq_hz):
    for ch in CHANNELS_60M:
        if abs(freq_hz - ch) <= CHANNEL_60M_HALF_WIDTH_HZ:
            return ch
    return None


def check_frequency(license_class, band, freq_hz, mode):
    """(ok, detail). mode is 'data' or 'phone'. band is looked up
    case-insensitively (e.g. '20m', '20M')."""
    license_class = (license_class or "").strip().lower()
    if license_class not in SUPPORTED_LICENSE_CLASSES:
        return False, (f"LICENSE_CLASS '{license_class or '(unset)'}' is not supported by this "
                        f"checker (only {sorted(SUPPORTED_LICENSE_CLASSES)} verified) -- refusing "
                        f"to guess; extend band_plan.py's tables first")
    band = (band or "").strip().lower()
    if band == "60m":
        ch = _on_60m_channel(freq_hz)
        if ch is not None:
            return True, f"{freq_hz} Hz is on the {ch} Hz 60 m channel (100 W ERP cap)"
        if _in_range(freq_hz, SEGMENT_60M):
            return True, f"{freq_hz} Hz is in the 60 m {SEGMENT_60M[0]}-{SEGMENT_60M[1]} Hz segment (9.15 W ERP cap)"
        return False, f"{freq_hz} Hz is not on a 60 m channel ({CHANNELS_60M}) or in the {SEGMENT_60M} Hz segment"
    priv = GENERAL_PRIVILEGES.get(band)
    if priv is None:
        return False, f"unknown or unsupported band '{band}'"
    rng = priv.get(mode)
    if _in_range(freq_hz, rng):
        return True, f"{freq_hz} Hz is within {band} {mode} privileges ({rng[0]}-{rng[1]} Hz) for license class '{license_class}'"
    return False, f"{freq_hz} Hz is outside {band} {mode} privileges for license class '{license_class}'"


def check_power(band, freq_hz, power_w):
    """(ok, detail)."""
    try:
        power_w = float(power_w)
    except (TypeError, ValueError):
        return False, f"TX_PWR '{power_w}' is not a number"
    if power_w <= 0:
        return False, f"TX_PWR must be > 0 (got {power_w})"
    band = (band or "").strip().lower()
    if band == "60m":
        ch = _on_60m_channel(freq_hz)
        if ch is not None:
            if power_w > CHANNEL_60M_POWER_LIMIT_W:
                return False, f"TX_PWR {power_w} W exceeds the 60 m channel cap of {CHANNEL_60M_POWER_LIMIT_W} W ERP"
            return True, f"{power_w} W is within the 60 m channel cap ({CHANNEL_60M_POWER_LIMIT_W} W ERP)"
        if _in_range(freq_hz, SEGMENT_60M):
            if power_w > SEGMENT_60M_POWER_LIMIT_W:
                return False, f"TX_PWR {power_w} W exceeds the 60 m segment cap of {SEGMENT_60M_POWER_LIMIT_W} W ERP"
            return True, f"{power_w} W is within the 60 m segment cap ({SEGMENT_60M_POWER_LIMIT_W} W ERP)"
        return False, f"{freq_hz} Hz is not a valid 60 m channel/segment -- cannot verify a power cap"
    if power_w > ABSOLUTE_CEILING_W:
        return False, f"TX_PWR {power_w} W exceeds the absolute {ABSOLUTE_CEILING_W} W PEP ceiling"
    cap = BAND_POWER_CAP_W.get(band)
    if cap is not None and power_w > cap:
        return False, f"TX_PWR {power_w} W exceeds the {band} cap of {cap} W PEP"
    return True, f"{power_w} W is within limits for {band}" + (f" (cap {cap} W)" if cap else "")


def verify(cfg):
    """cfg: dict shaped like station_config.load()'s return (MYCALL,
    LICENSE_CLASS, BAND, DIAL_HZ, TX_PWR). SeeQ only ever transmits data
    emissions (FT8/JS8/Winlink), so frequency privilege is always checked
    against the 'data' column. Returns (ok, [(ok, detail), ...])."""
    results = []

    cs_ok, cs_detail = check_callsign(cfg.get("MYCALL", ""))
    results.append((cs_ok, cs_detail))

    band = cfg.get("BAND", "")
    license_class = cfg.get("LICENSE_CLASS", "")

    try:
        freq_hz = int(str(cfg.get("DIAL_HZ", "")).strip())
    except (TypeError, ValueError):
        results.append((False, f"DIAL_HZ '{cfg.get('DIAL_HZ')}' is missing or not a number"))
        freq_hz = None

    if freq_hz is not None:
        freq_ok, freq_detail = check_frequency(license_class, band, freq_hz, "data")
        results.append((freq_ok, freq_detail))

        power_ok, power_detail = check_power(band, freq_hz, cfg.get("TX_PWR", ""))
        results.append((power_ok, power_detail))

    ok = all(r_ok for r_ok, _ in results)
    return ok, results


def main():
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import station_config
    cfg = station_config.load()
    ok, results = verify(cfg)
    for r_ok, detail in results:
        print(f"  {'OK  ' if r_ok else 'FAIL'} {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
