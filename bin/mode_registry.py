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
    # "js8" is deliberately ABSENT. The bin/modes/js8/ package exists and its
    # pipeline/engine satisfy the contract, but the mode is mid-rewrite: what
    # is on disk drives a third-party app, has never been verified against the
    # protocol, and has never transmitted. Listing it here is what makes a mode
    # switchable, so it stays out until the control operator has exercised the
    # native implementation and cut v4.0.0 -- then this one line comes back
    # alongside flipping MODE_INFO's status. See CLAUDE.md's JS8 section.
}

# MODE_INFO: display-only metadata for the dashboard's boot/mode chooser.
# Unlike MODES (functional, switchable, ft8-only for now), this can list modes
# that aren't usable yet, so the chooser can explain SeeQ's mode roadmap (see
# docs/MODES-ROADMAP.md: FT8 -> JS8 -> email-over-radio) without pretending
# they're switchable. Three statuses:
#
#   "available"      -- switchable now; must also be in MODES.
#   "in-development" -- being built right now; code may exist on disk but is
#                       unreleased and unverified on air. Shown as
#                       "In Development" so it's clear it's close, NOT as
#                       "coming soon".
#   "planned"        -- roadmap only, nothing built.
#
# Anything that is not "available" must NOT appear in MODES (enforced by
# tools/test_mode_registry.py) -- otherwise the chooser and mode-switch
# machinery would happily activate a mode the operator hasn't cleared.
# Promoting a mode means adding it to MODES *and* flipping this flag, never
# just one of the two.
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
        "status": "in-development",
        "description": ("FT8-derived mode that adds free-text keyboard-to-keyboard "
                         "messaging and store-and-forward relay on top of structured "
                         "calling -- a real conversation, not just an exchange. Being "
                         "built as a fully native SeeQ mode: our own protocol "
                         "implementation, tone generation, decoder and UI, no external "
                         "application. The encoder is done and bit-checked against the "
                         "spec; the decoder and on-air testing are still ahead."),
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
