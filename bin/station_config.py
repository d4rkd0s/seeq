"""Tiny shell-style KEY=VALUE parser for station.conf (stdlib only).

Every value comes back as a string; callers cast as needed and supply their
own defaults so a missing file or key can never break the station.
"""
import os

CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "station.conf")


def load(path=None):
    """Return {KEY: value} from a shell-style conf file. Missing file -> {}."""
    cfg = {}
    try:
        with open(path or CONF_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                val = val.split("#", 1)[0].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                cfg[key.strip()] = val
    except OSError:
        pass
    return cfg


def save_keys(updates, path=None):
    """Rewrite station.conf in place, replacing each existing KEY=... line's
    value with str(updates[KEY]) while preserving everything else about that
    line (inline comment, spacing before '#'). Keys with no existing line are
    appended. Stays valid shell (bin/seeq dot-sources this same file), so
    values must not contain '#' or a newline — callers pass plain numbers/
    short tokens (band names, Hz, watts), never free text."""
    p = path or CONF_PATH
    try:
        with open(p) as f:
            lines = f.readlines()
    except OSError:
        lines = []
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        key = None
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
        if key is not None and key in remaining:
            value = str(remaining.pop(key))
            rest = line.split("=", 1)[1]
            comment = ""
            if "#" in rest:
                comment = " #" + rest.split("#", 1)[1].rstrip("\n")
            nl = "\n" if line.endswith("\n") else ""
            out.append(f"{key}={value}{comment}{nl}")
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}\n")
    with open(p, "w") as f:
        f.writelines(out)
