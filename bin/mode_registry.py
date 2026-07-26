"""Mode registry: which bin/modes/<name>/ packages exist and how to load
them. The extension point M1 (JS8) and M2 (email) each add one entry to.

MODES intentionally lists just "ft8" for M0a -- adding a mode is adding an
entry here plus a bin/modes/<name>/ package, nothing else needs to change.
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODES = {
    "ft8": {"label": "FT8", "pipeline": "modes.ft8.pipeline", "engine": "modes.ft8.engine"},
    "js8": {"label": "JS8", "pipeline": "modes.js8.pipeline", "engine": "modes.js8.engine"},
}

# MODE_INFO: display-only metadata for the dashboard's boot/mode chooser.
# Unlike MODES (functional, switchable, ft8-only for now), this can list
# modes that aren't built yet -- "status": "planned" -- so the chooser can
# explain SeeQ's mode roadmap (see docs/MODES-ROADMAP.md: FT8 -> JS8 ->
# email-over-radio) without pretending they're actually switchable. A
# planned entry must never also appear in MODES (see
# tools/test_mode_registry.py) -- adding real support for one means moving
# it into MODES alongside its own bin/modes/<name>/ package, not just
# flipping this status flag.
MODE_INFO = {
    "ft8": {
        "label": "FT8",
        "status": "available",
        "description": ("Weak-signal digital mode built for marginal HF propagation: "
                         "structured 15-second exchanges (callsign, grid, signal report) "
                         "decoded well below what you can hear by ear. The mode SeeQ "
                         "drives today."),
        "protocol_url": "https://wsjt.sourceforge.io/wsjtx.html",
    },
    "ft4": {
        "label": "FT4",
        "status": "planned",
        "description": ("FT8's faster sibling -- 7.5-second exchanges instead of 15, "
                         "trading a little sensitivity for roughly double the QSO rate. "
                         "Built for contesting."),
        "protocol_url": "https://wsjt.sourceforge.io/wsjtx.html",
    },
    "js8": {
        "label": "JS8",
        "status": "available",
        "description": ("FT8-derived mode that adds free-text keyboard-to-keyboard "
                         "messaging and store-and-forward relay on top of structured "
                         "calling -- a real conversation, not just an exchange. Driven "
                         "through JS8Call-improved's TCP API; transmit is manual and "
                         "confirmed per message, never automatic."),
        "protocol_url": "https://github.com/JS8Call-improved/JS8Call-improved",
    },
    "winlink": {
        "label": "Winlink",
        "status": "planned",
        "description": ("Real email in and out over HF (ARDOP/VARA modem via the Pat "
                         "client) -- send and receive to any address with no internet "
                         "link at all."),
        "protocol_url": "https://winlink.org/",
    },
}


class UnknownModeError(ValueError):
    pass


def load_mode(name):
    """(pipeline_module, engine_module) for a registered mode name. Raises
    UnknownModeError for anything not in MODES -- never silently returns
    None, since a caller treating a missing mode as "nothing to do" could
    skip a safety-relevant stop/start step."""
    entry = MODES.get(name)
    if entry is None:
        raise UnknownModeError(f"unknown mode {name!r} (known: {sorted(MODES)})")
    pipeline = importlib.import_module(entry["pipeline"])
    engine = importlib.import_module(entry["engine"])
    return pipeline, engine
