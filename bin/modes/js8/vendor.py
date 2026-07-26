"""JS8 mode: locating the JS8Call-improved binary, with a fallback that can't
disappear.

Unlike FT8 -- where the decoder (`jt9`) comes from a distro package and
`doctor.py` just checks `shutil.which()` -- JS8Call-improved ships only as a
GUI AppImage from GitHub releases. That makes JS8 mode's usability depend on a
third party staying online, which is a bad property for a station that may be
operated portable, off-grid, or years from now after the fork has moved or
vanished.

So there are two sources, in order:

1. **Auto-download** the pinned release into a cache under the user's data dir
   (outside git), sha256-verified against the constant below.
2. **The copy committed into this repo** at `vendor/js8call-improved/`. Yes,
   it's ~59 MB in git. That is the deliberate cost of `git clone` being
   sufficient to run JS8 mode with no network at all.

A download that arrives corrupt is treated exactly like one that never
arrived: discarded, then fall through to the fallback. Nothing unverified is
ever left in the cache for a later run to find and execute.

Stdlib only -- same reasoning as api.py.
"""
import hashlib
import os
import shutil
import stat
import urllib.request

# PINNED TO 3.0.2 ON PURPOSE -- 3.0.3 exists and is newer, do not "upgrade"
# this without reading the next paragraph.
#
# v3.0.3 is built against Ubuntu 24.04-era libraries and requires GLIBC_2.38 /
# GLIBCXX_3.4.32. This station runs Ubuntu 22.04 (glibc 2.35, GLIBCXX 3.4.30),
# so 3.0.3 dies at the dynamic linker before it prints anything:
#     libstdc++.so.6: version `GLIBCXX_3.4.31' not found
# v3.0.2 needs only GLIBC_2.35 / GLIBCXX_3.4.29 and runs here.
#
# Note that CI cannot catch a regression on this: GitHub's ubuntu-latest has a
# newer glibc than the station does, so 3.0.3 launches happily there. That's
# what host_can_run() below is for -- it compares the AppImage's requirements
# against the *running* host, and doctor.py/pipeline.preflight() surface the
# answer before a mode switch fails confusingly.
PINNED_VERSION = "3.0.2"
ASSET_NAME = f"JS8Call-v{PINNED_VERSION}-x86_64.AppImage"
DOWNLOAD_URL = (
    "https://github.com/JS8Call-improved/JS8Call-improved/releases/download/"
    f"v{PINNED_VERSION}/{ASSET_NAME}"
)
# Computed from the release asset on 2026-07-26. GitHub publishes no checksums
# file for this release, so this is the pin -- if it ever stops matching, the
# release was re-cut (or something is wrong) and it needs deliberate review,
# not a silently-updated constant.
SHA256 = "930f3032ce94330018f08213f593b99c3c3496c7842e72fe664921e3ae94c4f0"
EXPECTED_BYTES = 59591160

# Highest symbol versions this build actually asks the host for. Checked
# against the running system by host_can_run().
REQUIRED_GLIBC = (2, 35)
REQUIRED_GLIBCXX = (3, 4, 29)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DOWNLOAD_TIMEOUT_S = 120
_HASH_CHUNK = 1024 * 1024


class Js8VendorError(Exception):
    """No usable JS8Call-improved binary could be found or fetched."""


def cache_dir():
    """Where downloads live: outside the repo, so a 59 MB fetch never shows up
    in `git status`. Honours XDG_DATA_HOME like the rest of the desktop."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "seeq", "vendor", "js8call-improved", f"v{PINNED_VERSION}")


def cache_path():
    return os.path.join(cache_dir(), ASSET_NAME)


def repo_fallback_path():
    return os.path.join(_ROOT, "vendor", "js8call-improved", ASSET_NAME)


def sha256_file(path, chunk=_HASH_CHUNK):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def verify(path, sha256=SHA256, size=EXPECTED_BYTES):
    """(ok, detail). Size is checked first -- a half-finished download is the
    common failure, and catching it on a stat() beats hashing 59 MB to reach
    the same answer."""
    if not os.path.exists(path):
        return False, f"missing ({path})"
    actual_size = os.path.getsize(path)
    if size is not None and actual_size != size:
        return False, f"wrong size ({actual_size} bytes, expected {size})"
    actual = sha256_file(path)
    if sha256 is not None and actual != sha256:
        return False, f"sha256 mismatch ({actual[:16]}... != {sha256[:16]}...)"
    return True, "verified"


def _make_executable(path):
    """An AppImage without +x fails at exec with a confusing error."""
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    return path


def http_download(url, dest):
    """Fetch to a .part file, then rename -- so an interrupted download can
    never be mistaken for a complete one at `dest`."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f, _HASH_CHUNK)
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            try:
                os.unlink(part)
            except OSError:
                pass


def _max_symbol_version(lib_path, prefix):
    """Highest `prefix`N.N[.N] version symbol a library exports, or None."""
    import re
    import subprocess as sp
    try:
        out = sp.run(["strings", lib_path], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    versions = set()
    for m in re.finditer(rf"{re.escape(prefix)}(\d+(?:\.\d+)+)", out):
        versions.add(tuple(int(x) for x in m.group(1).split(".")))
    return max(versions) if versions else None


def host_can_run(required_glibc=REQUIRED_GLIBC, required_glibcxx=REQUIRED_GLIBCXX):
    """(ok, detail) -- will the pinned AppImage's dynamic linking resolve here?

    An AppImage bundles Qt and friends but never bundles glibc/libstdc++, so a
    build made on a newer distro simply cannot start on an older one. That
    failure is opaque when you hit it cold ("version `GLIBCXX_3.4.31' not
    found" with no context), and CI can't warn about it because the runner's
    glibc is newer than the station's -- so check the actual running host.

    Unknown/unreadable system libraries return ok=True: refusing to start JS8
    because a probe was inconclusive would be worse than letting the real
    launch produce the real error.
    """
    libc_v = None
    try:
        import subprocess as sp
        out = sp.run(["ldd", "--version"], capture_output=True, text=True, timeout=10).stdout
        import re
        m = re.search(r"(\d+)\.(\d+)\s*$", out.splitlines()[0]) or re.search(r"(\d+)\.(\d+)", out)
        if m:
            libc_v = (int(m.group(1)), int(m.group(2)))
    except Exception:
        pass
    if libc_v and libc_v < tuple(required_glibc):
        return False, (f"host glibc {libc_v[0]}.{libc_v[1]} is older than the "
                        f"{required_glibc[0]}.{required_glibc[1]} JS8Call-improved "
                        f"{PINNED_VERSION} needs")
    for candidate in ("/lib/x86_64-linux-gnu/libstdc++.so.6",
                       "/usr/lib/x86_64-linux-gnu/libstdc++.so.6"):
        if os.path.exists(candidate):
            have = _max_symbol_version(candidate, "GLIBCXX_")
            if have and have < tuple(required_glibcxx):
                dotted = ".".join(str(x) for x in required_glibcxx)
                return False, (f"host libstdc++ provides up to GLIBCXX_"
                                f"{'.'.join(str(x) for x in have)}, but JS8Call-improved "
                                f"{PINNED_VERSION} needs GLIBCXX_{dotted}")
            break
    return True, "host libraries are new enough"


def find_installed(cache_path=None, fallback_path=None, sha256=SHA256, size=EXPECTED_BYTES):
    """An already-present, verified binary (cache first, then the repo copy),
    or None. Never downloads -- this is what doctor.py calls, and doctor is
    read-only by contract."""
    for candidate in (cache_path or globals()["cache_path"](),
                       fallback_path or repo_fallback_path()):
        ok, _ = verify(candidate, sha256=sha256, size=size)
        if ok:
            return candidate
    return None


def ensure_installed(cache_path=None, fallback_path=None, sha256=SHA256,
                     size=EXPECTED_BYTES, download_fn=http_download, log_fn=None):
    """(path, source) where source is 'cache', 'download', or 'fallback'.

    Raises Js8VendorError only when every source has been tried and none
    produced a verified binary. Which source was used is always logged --
    running off the committed fallback is fine, but it shouldn't be silent,
    since it usually means the network (or the upstream project) is gone.
    """
    cpath = cache_path or globals()["cache_path"]()
    fpath = fallback_path or repo_fallback_path()

    def log(msg):
        if log_fn:
            log_fn(msg)

    ok, detail = verify(cpath, sha256=sha256, size=size)
    if ok:
        return _make_executable(cpath), "cache"

    download_error = None
    try:
        log(f"js8/vendor: fetching {ASSET_NAME} from GitHub releases")
        download_fn(DOWNLOAD_URL, cpath)
        ok, detail = verify(cpath, sha256=sha256, size=size)
        if ok:
            log(f"js8/vendor: downloaded and verified -> {cpath}")
            return _make_executable(cpath), "download"
        download_error = f"downloaded file failed verification: {detail}"
        # Don't leave an unverified binary where the next run would trust it.
        try:
            os.unlink(cpath)
        except OSError:
            pass
    except Exception as e:  # network down, DNS, 404, permissions, disk full
        download_error = f"{e}"

    log(f"js8/vendor: download unavailable ({download_error}) -- trying committed fallback")
    ok, fdetail = verify(fpath, sha256=sha256, size=size)
    if ok:
        log(f"js8/vendor: using repo fallback copy -> {fpath}")
        return _make_executable(fpath), "fallback"

    raise Js8VendorError(
        f"no usable JS8Call-improved {PINNED_VERSION} binary: download failed "
        f"({download_error}); committed fallback at {fpath} also unusable ({fdetail})")
