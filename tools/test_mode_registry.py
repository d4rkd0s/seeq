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


class TestModeInfo(unittest.TestCase):
    """MODE_INFO is display-only metadata for the dashboard's mode chooser --
    separate from MODES (which stays functional/switchable-only, just ft8).
    It's allowed to list modes that aren't switchable yet (ft4/js8/winlink),
    so the chooser can show what's coming without pretending they work."""

    def test_every_functional_mode_has_info(self):
        for name in mode_registry.MODES:
            self.assertIn(name, mode_registry.MODE_INFO, name)

    def test_ft8_is_available(self):
        self.assertEqual(mode_registry.MODE_INFO["ft8"]["status"], "available")

    def test_only_available_modes_are_functional(self):
        # Anything not "available" must NOT appear in MODES, or the boot
        # chooser / mode-switch machinery would try to load a pipeline the
        # operator has not cleared for use. Covers "planned" (not built) and
        # "in-development" (built but unreleased and unverified on air).
        for name, info in mode_registry.MODE_INFO.items():
            if info["status"] != "available":
                self.assertNotIn(name, mode_registry.MODES,
                                 f"{name} is {info['status']} but still switchable")

    def test_every_entry_has_description_and_protocol_url(self):
        for name, info in mode_registry.MODE_INFO.items():
            self.assertTrue(info.get("description"), name)
            self.assertTrue(info.get("protocol_url", "").startswith("http"), name)

    def test_status_is_a_known_value(self):
        for name, info in mode_registry.MODE_INFO.items():
            self.assertIn(info["status"],
                          ("available", "in-development", "planned"), name)

    def test_js8_is_in_development_not_available(self):
        """JS8 is mid-rewrite as a native mode: the wrapper on disk drives a
        third-party app, is unverified against the protocol, and has never
        been exercised on air. It ships at v4.0.0 after the control operator
        has personally cleared it -- not before. See CLAUDE.md's JS8 section."""
        self.assertEqual(mode_registry.MODE_INFO["js8"]["status"], "in-development")


class TestLoadMode(unittest.TestCase):
    def test_ft8_is_registered(self):
        self.assertIn("ft8", mode_registry.MODES)

    def test_load_ft8_returns_pipeline_and_engine(self):
        pipeline, engine = mode_registry.load_mode("ft8")
        for fn in ("start", "stop", "is_running", "sanity_check"):
            self.assertTrue(callable(getattr(pipeline, fn)), fn)
        for fn in ("chase_start", "chase_stop"):
            self.assertTrue(callable(getattr(engine, fn)), fn)

    def test_js8_is_not_switchable_while_in_development(self):
        """Registered in MODE_INFO (so the chooser can show it), absent from
        MODES (so nothing can activate it). Flip both together at v4.0.0."""
        self.assertNotIn("js8", mode_registry.MODES)

    def test_loading_js8_raises_rather_than_starting_it(self):
        with self.assertRaises(mode_registry.UnknownModeError):
            mode_registry.load_mode("js8")

    def test_the_js8_package_itself_still_satisfies_the_contract(self):
        """The wrapper stays on disk as the P1b cross-check oracle, so its
        five-function pipeline contract must keep working even though the mode
        is not switchable -- this is what makes re-enabling it a one-line
        change rather than a re-integration."""
        import importlib
        pipeline = importlib.import_module("modes.js8.pipeline")
        engine = importlib.import_module("modes.js8.engine")
        for fn in ("start", "stop", "is_running", "sanity_check", "preflight"):
            self.assertTrue(callable(getattr(pipeline, fn)), fn)
        for fn in ("chase_start", "chase_stop"):
            self.assertTrue(callable(getattr(engine, fn)), fn)

    def test_js8_engine_exposes_its_own_send_path(self):
        # JS8 is conversational: the dashboard composes free text rather than
        # running FT8's fixed exchange, so send/halt are the real entry points.
        # Imported directly, not via load_mode: js8 is out of MODES while it is
        # in development (see test_js8_is_not_switchable_while_in_development).
        import importlib
        engine = importlib.import_module("modes.js8.engine")
        for fn in ("send", "halt", "set_speed"):
            self.assertTrue(callable(getattr(engine, fn)), fn)

    def test_unknown_mode_raises(self):
        with self.assertRaises(mode_registry.UnknownModeError):
            mode_registry.load_mode("ft4")

    def test_unknown_mode_error_message_lists_known_modes(self):
        with self.assertRaises(mode_registry.UnknownModeError) as ctx:
            mode_registry.load_mode("bogus")
        self.assertIn("ft8", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
