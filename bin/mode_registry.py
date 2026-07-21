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
