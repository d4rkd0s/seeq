#!/usr/bin/env python3
"""CI smoke test: does the committed JS8Call-improved fallback actually run?

The ~59 MB AppImage in vendor/js8call-improved/ exists so JS8 mode keeps
working when GitHub is unreachable or the upstream project has moved. That
guarantee is worthless if the binary silently rots -- a bad checksum, a Qt
dependency that stopped shipping, an AppImage that no longer starts. So CI
verifies it and then genuinely launches it, headless, and waits for its TCP
API to answer.

This deliberately does NOT run as part of `make test`: it needs FUSE, a
virtual display, and ~30 s. It's a separate CI job (see
.github/workflows/test.yml).

Everything happens in a throwaway XDG_CONFIG_HOME with a minimal settings file
that leaves the rig unconfigured, so no serial port is opened and nothing can
transmit -- the app is only asked to boot and answer a question.

  python3 tools/js8_fallback_smoke.py --verify-only   # checksum only
  xvfb-run -a python3 tools/js8_fallback_smoke.py     # full launch test
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
BOOT_TIMEOUT_S = 90
POLL_S = 1.0


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vendor = _load("js8_vendor", "bin/modes/js8/vendor.py")
api = _load("js8_api", "bin/modes/js8/api.py")


def verify():
    path = vendor.repo_fallback_path()
    ok, detail = vendor.verify(path)
    print(f"fallback: {path}")
    print(f"verify:   {detail}")
    if not ok:
        print("FAIL: the committed fallback AppImage does not match its pinned "
              "checksum. Either it was corrupted in transit/storage, or the pin in "
              "bin/modes/js8/vendor.py was changed without replacing the binary.",
              file=sys.stderr)
        return None
    return path


def smoke(path, port=None):
    port = port or api.DEFAULT_PORT
    tmp = tempfile.mkdtemp(prefix="js8-smoke-")
    cfg_home = os.path.join(tmp, "config")
    os.makedirs(cfg_home, exist_ok=True)
    # A minimal settings file: API on, rig left unset so no serial port is
    # ever opened. Matches what pipeline.write_settings() produces.
    ini = os.path.join(cfg_home, "JS8Call - SeeQSmoke.ini")
    with open(ini, "w") as f:
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
    print(f"launching: {path} --rig-name SeeQSmoke  (XDG_CONFIG_HOME={cfg_home})")
    proc = subprocess.Popen([path, "--rig-name", "SeeQSmoke"],
                            stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env,
                            start_new_session=True)
    try:
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log.seek(0)
                print(f"FAIL: JS8Call exited early (rc={proc.returncode})\n"
                      f"--- log ---\n{log.read()}", file=sys.stderr)
                return 1
            if api.is_reachable(port=port, timeout=1.0):
                break
            time.sleep(POLL_S)
        else:
            log.seek(0)
            print(f"FAIL: TCP API never answered on port {port} within "
                  f"{BOOT_TIMEOUT_S}s\n--- log ---\n{log.read()}", file=sys.stderr)
            return 1

        print(f"API is up on port {port}; querying it")
        with api.Js8Client(port=port, timeout=10.0) as c:
            version = c.version()
            ptt, _msg = c.get_ptt()
        print(f"STATION.VERSION -> {version!r}")
        print(f"RIG.GET_PTT     -> {ptt}")
        if ptt:
            # Nothing asked it to transmit; if it claims to be keyed on a
            # fresh boot with no rig configured, something is very wrong.
            print("FAIL: a freshly booted instance reports PTT on", file=sys.stderr)
            return 1
        if vendor.PINNED_VERSION not in (version or ""):
            print(f"WARN: reported version {version!r} does not contain the pinned "
                  f"{vendor.PINNED_VERSION} -- not fatal, the API may format it "
                  f"differently, but worth a look.")
        print("PASS: the committed fallback AppImage boots and serves its API")
        return 0
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify-only", action="store_true",
                    help="check the checksum without launching anything")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args(argv)

    path = verify()
    if path is None:
        return 1
    if args.verify_only:
        print("PASS: checksum verified")
        return 0
    return smoke(path, port=args.port)


if __name__ == "__main__":
    sys.exit(main())
