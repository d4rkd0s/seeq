#!/usr/bin/env python3
"""CI smoke test: has the committed JS8Call-improved fallback rotted?

The ~59 MB AppImage in vendor/js8call-improved/ exists so JS8 mode keeps
working when GitHub is unreachable or the upstream project has moved. That
guarantee is worthless if the binary quietly decays -- corruption, a truncated
commit, or (most likely of all) a host library it links against disappearing
from future distro releases. So CI checks it rather than trusting it.

Three assertions, in increasing order of what they catch:

  1. sha256 matches the pin           -> corruption, or a pin changed without
                                         replacing the binary
  2. it extracts and every dynamic    -> the realistic long-term rot: an
     dependency resolves                 ABI/library dependency that no longer
                                         ships on modern systems
  3. it launches and stays alive      -> gross breakage

What is deliberately NOT asserted: that the TCP API comes up. JS8Call-improved
is a Qt GUI that expects an audio device, and a bare CI container has no
PulseAudio (`pa_context_connect() failed`) and no real display. It boots and
keeps running there, but does not open its API socket. Making that
load-bearing in CI would mean either a permanently red build or a flaky one,
and a flaky test is worse than an honest gap. The API *is* still probed and
reported here as information, and it is verified for real on the operating
machine -- that's part of the JS8 mode walkthrough, where there is an actual
radio, sound card and display.

Everything runs in a throwaway XDG_CONFIG_HOME with the rig left unconfigured,
so no serial port is opened and nothing can transmit.

  python3 tools/js8_fallback_smoke.py --verify-only   # checksum alone
  xvfb-run -a python3 tools/js8_fallback_smoke.py     # full check (CI)
  xvfb-run -a python3 tools/js8_fallback_smoke.py --require-api
                                                      # also demand the API,
                                                      # for a machine with audio
"""
import argparse
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_TIMEOUT_S = 60
ALIVE_S = 20
POLL_S = 1.0


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vendor = _load("js8_vendor", "bin/modes/js8/vendor.py")
api = _load("js8_api", "bin/modes/js8/api.py")


def check_checksum():
    path = vendor.repo_fallback_path()
    ok, detail = vendor.verify(path)
    print(f"[1/3] checksum: {detail}  ({path})")
    if not ok:
        print("FAIL: the committed fallback does not match its pinned checksum. Either "
              "it was corrupted, or the pin in bin/modes/js8/vendor.py changed without "
              "the binary being replaced.", file=sys.stderr)
        return None
    return path


def check_dependencies(appimage):
    """Extract and confirm every shared library the binary needs resolves.

    This is the assertion most likely to catch real rot years from now: the
    AppImage bundles Qt but deliberately not host graphics drivers, so it
    depends on libEGL/libGL/xcb/xkbcommon still existing on the host.
    """
    tmp = tempfile.mkdtemp(prefix="js8-ldd-")
    try:
        r = subprocess.run([appimage, "--appimage-extract"], cwd=tmp,
                           capture_output=True, timeout=300, text=True)
        binary = os.path.join(tmp, "squashfs-root", "usr", "bin", "JS8Call")
        if r.returncode != 0 or not os.path.exists(binary):
            print(f"[2/3] FAIL: AppImage did not extract (rc={r.returncode})\n"
                  f"{r.stderr[:800]}", file=sys.stderr)
            return False
        env = dict(os.environ,
                   LD_LIBRARY_PATH=os.path.join(tmp, "squashfs-root", "usr", "lib"))
        r = subprocess.run(["ldd", binary], capture_output=True, text=True,
                           timeout=120, env=env)
        missing = [ln.strip() for ln in r.stdout.splitlines() if "not found" in ln]
        total = len([ln for ln in r.stdout.splitlines() if "=>" in ln])
        if missing:
            print(f"[2/3] FAIL: {len(missing)} unresolved shared libraries:", file=sys.stderr)
            for m in missing:
                print(f"        {m}", file=sys.stderr)
            print("      Install the matching system packages (see this job's apt step).",
                  file=sys.stderr)
            return False
        print(f"[2/3] dependencies: all {total} shared libraries resolve")
        return True
    except Exception as e:
        print(f"[2/3] FAIL: could not inspect dependencies: {e}", file=sys.stderr)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_launch(appimage, port=None, require_api=False):
    """Launch it for real and confirm it doesn't fall over.

    The API probe is reported either way; it only fails the run under
    --require-api, for the reasons in the module docstring.
    """
    port = port or api.DEFAULT_PORT
    tmp = tempfile.mkdtemp(prefix="js8-smoke-")
    cfg_home = os.path.join(tmp, "config")
    os.makedirs(cfg_home, exist_ok=True)
    # Minimal settings: API on, rig left unset so no serial port is opened.
    # Same keys pipeline.write_settings() produces.
    with open(os.path.join(cfg_home, "JS8Call - SeeQSmoke.ini"), "w") as f:
        f.write("[General]\n"
                "AcceptTCPRequests=true\n"
                f"TCPServerPort={port}\n"
                "AcceptUDPRequests=false\n"
                "MyCall=N0CALL\n"
                "MyGrid=AA00\n")

    env = dict(os.environ, XDG_CONFIG_HOME=cfg_home,
               XDG_DATA_HOME=os.path.join(tmp, "data"),
               XDG_CACHE_HOME=os.path.join(tmp, "cache"))
    log = open(os.path.join(tmp, "js8call.log"), "w+")
    proc = subprocess.Popen([appimage, "--rig-name", "SeeQSmoke"],
                            stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env,
                            start_new_session=True)
    try:
        deadline = time.monotonic() + (API_TIMEOUT_S if require_api else ALIVE_S)
        api_up = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log.seek(0)
                print(f"[3/3] FAIL: JS8Call exited on its own (rc={proc.returncode})\n"
                      f"--- app output ---\n{log.read()[:3000]}", file=sys.stderr)
                return False
            if api.is_reachable(port=port, timeout=1.0):
                api_up = True
                break
            time.sleep(POLL_S)

        if proc.poll() is not None:
            print(f"[3/3] FAIL: JS8Call exited (rc={proc.returncode})", file=sys.stderr)
            return False

        if api_up:
            with api.Js8Client(port=port, timeout=10.0) as c:
                version = c.version()
                ptt, _ = c.get_ptt()
            print(f"[3/3] launch: alive, API up on {port}, "
                  f"STATION.VERSION={version!r}, PTT={ptt}")
            if ptt:
                print("FAIL: a freshly booted instance with no rig reports PTT on",
                      file=sys.stderr)
                return False
            return True

        log.seek(0)
        out = log.read()
        msg = (f"[3/3] launch: alive and stable for {ALIVE_S}s; API did not open "
               f"on port {port}")
        if "pa_context_connect() failed" in out:
            msg += " (no PulseAudio in this environment -- expected in CI)"
        print(msg)
        if require_api:
            print(f"FAIL: --require-api was set but the API never answered\n"
                  f"--- app output ---\n{out[:3000]}", file=sys.stderr)
            return False
        return True
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="JS8 fallback AppImage smoke test")
    ap.add_argument("--verify-only", action="store_true",
                    help="checksum only; launch nothing")
    ap.add_argument("--require-api", action="store_true",
                    help="also require the TCP API to answer (needs an audio device)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args(argv)

    path = check_checksum()
    if path is None:
        return 1
    if args.verify_only:
        print("PASS: checksum verified")
        return 0
    if not check_dependencies(path):
        return 1
    if not check_launch(path, port=args.port, require_api=args.require_api):
        return 1
    print("PASS: the committed fallback AppImage is intact, resolvable and runnable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
