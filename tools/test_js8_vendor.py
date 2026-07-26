#!/usr/bin/env python3
"""Tests for bin/modes/js8/vendor.py -- locating the JS8Call-improved binary.

Never touches the network: the real downloader is injected, so every path
through here (cache hit, fresh download, corrupt download, GitHub down,
nothing available at all) is exercised with tiny local files instead of a
59 MB AppImage.
Run: python3 tools/test_js8_vendor.py
"""
import hashlib
import importlib.util
import os
import shutil
import stat
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_PY = os.path.join(ROOT, "bin", "modes", "js8", "vendor.py")


def _vendor_module():
    spec = importlib.util.spec_from_file_location("js8_vendor", VENDOR_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vendor = _vendor_module()

PAYLOAD = b"not-really-an-appimage, but it hashes just the same"
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


class VendorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "cache", "JS8Call.AppImage")
        self.fallback = os.path.join(self.tmp, "repo-vendor", "JS8Call.AppImage")
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, path, data=PAYLOAD):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _ensure(self, download_fn, **kw):
        return vendor.ensure_installed(
            cache_path=self.cache, fallback_path=self.fallback,
            sha256=PAYLOAD_SHA, size=len(PAYLOAD),
            download_fn=download_fn, log_fn=self.logs.append, **kw)


class TestChecksum(VendorTestCase):
    def test_sha256_file_matches_hashlib(self):
        p = self._write(os.path.join(self.tmp, "x.bin"))
        self.assertEqual(vendor.sha256_file(p), PAYLOAD_SHA)

    def test_verify_accepts_a_good_file(self):
        p = self._write(os.path.join(self.tmp, "x.bin"))
        ok, detail = vendor.verify(p, sha256=PAYLOAD_SHA, size=len(PAYLOAD))
        self.assertTrue(ok, detail)

    def test_verify_rejects_a_wrong_checksum(self):
        p = self._write(os.path.join(self.tmp, "x.bin"), b"tampered")
        ok, detail = vendor.verify(p, sha256=PAYLOAD_SHA, size=len(b"tampered"))
        self.assertFalse(ok)
        self.assertIn("sha256", detail.lower())

    def test_verify_rejects_a_truncated_file_by_size_before_hashing(self):
        # A half-finished download is the common case; catching it on size is
        # cheaper than hashing 59 MB to reach the same conclusion.
        p = self._write(os.path.join(self.tmp, "x.bin"), PAYLOAD[:10])
        ok, detail = vendor.verify(p, sha256=PAYLOAD_SHA, size=len(PAYLOAD))
        self.assertFalse(ok)
        self.assertIn("size", detail.lower())

    def test_verify_rejects_a_missing_file(self):
        ok, detail = vendor.verify(os.path.join(self.tmp, "nope.bin"),
                                    sha256=PAYLOAD_SHA, size=len(PAYLOAD))
        self.assertFalse(ok)
        self.assertIn("missing", detail.lower())


class TestEnsureInstalled(VendorTestCase):
    def test_cache_hit_does_not_download(self):
        self._write(self.cache)
        calls = []
        path, source = self._ensure(lambda url, dest: calls.append(url))
        self.assertEqual(path, self.cache)
        self.assertEqual(source, "cache")
        self.assertEqual(calls, [])

    def test_downloads_when_cache_is_empty(self):
        def fake_download(url, dest):
            self._write(dest)
        path, source = self._ensure(fake_download)
        self.assertEqual(path, self.cache)
        self.assertEqual(source, "download")
        self.assertTrue(os.path.exists(self.cache))

    def test_falls_back_to_the_repo_copy_when_the_download_fails(self):
        # The whole point of committing a copy: GitHub being unreachable (or
        # the project disappearing) must not take JS8 mode down with it.
        self._write(self.fallback)

        def boom(url, dest):
            raise OSError("network unreachable")
        path, source = self._ensure(boom)
        self.assertEqual(path, self.fallback)
        self.assertEqual(source, "fallback")
        self.assertTrue(any("fallback" in m.lower() for m in self.logs), self.logs)

    def test_falls_back_when_the_download_is_corrupt(self):
        # A download that "succeeds" but fails verification is exactly as
        # unusable as one that never arrived -- treat it the same way.
        self._write(self.fallback)

        def corrupt(url, dest):
            self._write(dest, b"truncated garbage")
        path, source = self._ensure(corrupt)
        self.assertEqual(path, self.fallback)
        self.assertEqual(source, "fallback")

    def test_corrupt_download_is_not_left_behind_in_the_cache(self):
        # Otherwise the next run would find it, and (without verification)
        # could try to execute it.
        self._write(self.fallback)
        self._ensure(lambda url, dest: self._write(dest, b"garbage"))
        self.assertFalse(os.path.exists(self.cache))

    def test_raises_when_neither_download_nor_fallback_works(self):
        def boom(url, dest):
            raise OSError("network unreachable")
        with self.assertRaises(vendor.Js8VendorError) as cm:
            self._ensure(boom)
        self.assertIn("network unreachable", str(cm.exception))

    def test_corrupt_fallback_is_rejected_not_silently_used(self):
        self._write(self.fallback, b"rotted")

        def boom(url, dest):
            raise OSError("network unreachable")
        with self.assertRaises(vendor.Js8VendorError):
            self._ensure(boom)

    def test_result_is_executable(self):
        # An AppImage that isn't chmod +x fails at exec time with a confusing
        # error, so set the bit as part of installing it.
        self._write(self.cache)
        path, _ = self._ensure(lambda url, dest: None)
        self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR)

    def test_find_installed_returns_none_when_nothing_is_present(self):
        self.assertIsNone(vendor.find_installed(
            cache_path=self.cache, fallback_path=self.fallback,
            sha256=PAYLOAD_SHA, size=len(PAYLOAD)))

    def test_find_installed_prefers_cache_then_fallback(self):
        self._write(self.fallback)
        self.assertEqual(
            vendor.find_installed(cache_path=self.cache, fallback_path=self.fallback,
                                   sha256=PAYLOAD_SHA, size=len(PAYLOAD)),
            self.fallback)
        self._write(self.cache)
        self.assertEqual(
            vendor.find_installed(cache_path=self.cache, fallback_path=self.fallback,
                                   sha256=PAYLOAD_SHA, size=len(PAYLOAD)),
            self.cache)


class TestPinnedRelease(unittest.TestCase):
    """The pinned constants are load-bearing -- a wrong URL or a stale
    checksum turns into a download that can never verify."""

    def test_version_and_asset_agree(self):
        self.assertIn(vendor.PINNED_VERSION, vendor.ASSET_NAME)
        self.assertIn(vendor.PINNED_VERSION, vendor.DOWNLOAD_URL)
        self.assertTrue(vendor.DOWNLOAD_URL.endswith(vendor.ASSET_NAME))

    def test_download_url_points_at_the_improved_fork(self):
        # Not upstream js8call.com, and not some mirror -- the fork this whole
        # mode was verified against.
        self.assertIn("JS8Call-improved/JS8Call-improved", vendor.DOWNLOAD_URL)

    def test_checksum_is_a_real_sha256(self):
        self.assertRegex(vendor.SHA256, r"^[0-9a-f]{64}$")

    def test_repo_fallback_is_inside_the_repo(self):
        self.assertTrue(vendor.repo_fallback_path().startswith(ROOT))

    def test_the_committed_fallback_matches_the_pin(self):
        # The whole offline guarantee rests on these agreeing.
        ok, detail = vendor.verify(vendor.repo_fallback_path())
        self.assertTrue(ok, f"committed fallback does not match the pin: {detail}")


class TestHostCompatibility(unittest.TestCase):
    """An AppImage bundles Qt but never glibc/libstdc++, so a build made on a
    newer distro cannot start on an older one -- and CI can't catch it, because
    the runner's glibc is newer than the station's. host_can_run() compares the
    pin's requirements against the actual running host.

    This is not hypothetical: v3.0.3 needs GLIBCXX_3.4.32 and dies at the
    linker on Ubuntu 22.04, which is why the pin is deliberately 3.0.2.
    """

    def test_pin_is_3_0_2_not_a_newer_build(self):
        # Guards against a well-meant "update to latest" that would silently
        # break JS8 on this station. Read vendor.py's PINNED_VERSION comment
        # before changing this.
        self.assertEqual(vendor.PINNED_VERSION, "3.0.2")

    def test_declared_requirements_are_the_older_toolchain(self):
        self.assertLessEqual(tuple(vendor.REQUIRED_GLIBC), (2, 35))
        self.assertLessEqual(tuple(vendor.REQUIRED_GLIBCXX), (3, 4, 29))

    def test_this_host_can_actually_run_the_pinned_build(self):
        ok, detail = vendor.host_can_run()
        self.assertTrue(ok, f"the pinned AppImage cannot run here: {detail}")

    def test_an_impossible_requirement_is_reported_not_ignored(self):
        ok, detail = vendor.host_can_run(required_glibc=(99, 0),
                                          required_glibcxx=(3, 4, 29))
        self.assertFalse(ok)
        self.assertIn("glibc", detail.lower())

    def test_an_impossible_libstdcxx_requirement_is_caught(self):
        ok, detail = vendor.host_can_run(required_glibc=(2, 0),
                                          required_glibcxx=(9, 9, 99))
        self.assertFalse(ok)
        self.assertIn("GLIBCXX", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
