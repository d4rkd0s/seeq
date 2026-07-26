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

PINNED_VERSION = "3.0.3"
ASSET_NAME = f"JS8Call-v{PINNED_VERSION}-x86_64.AppImage"
DOWNLOAD_URL = (
    "https://github.com/JS8Call-improved/JS8Call-improved/releases/download/"
    f"v{PINNED_VERSION}/{ASSET_NAME}"
)
# Computed from the release asset on 2026-07-26. GitHub publishes no checksums
# file for this release, so this is the pin -- if it ever stops matching, the
# release was re-cut (or something is wrong) and it needs deliberate review,
# not a silently-updated constant.
SHA256 = "3f89bd821f281c59a9384c08a3ad783ea3b9ac6abf319ce6c0d881c2ecc6e6cd"
EXPECTED_BYTES = 59116024

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
