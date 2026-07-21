#!/usr/bin/env python3
"""Tests for bin/mode_registry.py -- the M0 mode registry/loader. Pure
import-and-lookup logic, no subprocess, no radio.
Run: python3 tools/test_mode_registry.py
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODE_REGISTRY = os.path.join(ROOT, "bin", "mode_registry.py")


def _mode_registry_module():
    spec = importlib.util.spec_from_file_location("mode_registry", MODE_REGISTRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mode_registry = _mode_registry_module()


class TestLoadMode(unittest.TestCase):
    def test_ft8_is_registered(self):
        self.assertIn("ft8", mode_registry.MODES)

    def test_load_ft8_returns_pipeline_and_engine(self):
        pipeline, engine = mode_registry.load_mode("ft8")
        for fn in ("start", "stop", "is_running", "sanity_check"):
            self.assertTrue(callable(getattr(pipeline, fn)), fn)
        for fn in ("chase_start", "chase_stop"):
            self.assertTrue(callable(getattr(engine, fn)), fn)

    def test_unknown_mode_raises(self):
        with self.assertRaises(mode_registry.UnknownModeError):
            mode_registry.load_mode("js8")

    def test_unknown_mode_error_message_lists_known_modes(self):
        with self.assertRaises(mode_registry.UnknownModeError) as ctx:
            mode_registry.load_mode("bogus")
        self.assertIn("ft8", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
