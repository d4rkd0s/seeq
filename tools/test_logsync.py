#!/usr/bin/env python3
"""Unit tests for bin/logsync.py's pure upload-enrichment helpers: adding a
priority-ordered, size-capped ADIF COMMENT field to the record before it's
POSTed to QRZ (never touches the local wsjtx_log.adi file itself -- qso.py
owns that, unmodified). Run: python3 tools/test_logsync.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import logsync


class TestSeeqVersion(unittest.TestCase):
    """seeq_version(): `git describe --tags` via an injectable `run`, never
    raises -- a non-git deploy or no-tags-yet repo just omits the version
    piece from the comment rather than crashing the sync."""

    class _Result:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout

    def test_returns_stripped_tag_on_success(self):
        def fake_run(cmd, **kw):
            return self._Result(0, "v2.0.0\n")
        self.assertEqual(logsync.seeq_version(run=fake_run), "v2.0.0")

    def test_nonzero_exit_returns_none(self):
        def fake_run(cmd, **kw):
            return self._Result(128, "")
        self.assertIsNone(logsync.seeq_version(run=fake_run))

    def test_empty_stdout_returns_none(self):
        def fake_run(cmd, **kw):
            return self._Result(0, "\n")
        self.assertIsNone(logsync.seeq_version(run=fake_run))

    def test_run_raising_returns_none(self):
        def fake_run(cmd, **kw):
            raise OSError("git not found")
        self.assertIsNone(logsync.seeq_version(run=fake_run))


class TestBuildComment(unittest.TestCase):
    """build_comment(pieces, max_len): joins priority-ordered pieces with
    ' | ', dropping lowest-priority pieces from the end until it fits --
    never drops the highest-priority piece, hard-truncating it instead if
    even it alone doesn't fit."""

    def test_all_pieces_fit(self):
        self.assertEqual(logsync.build_comment(["SeeQ v2.0.0", "Germany"], 200),
                          "SeeQ v2.0.0 | Germany")

    def test_falsy_pieces_skipped(self):
        self.assertEqual(logsync.build_comment(["SeeQ v2.0.0", None, "", "Germany"], 200),
                          "SeeQ v2.0.0 | Germany")

    def test_low_priority_piece_dropped_to_fit(self):
        pieces = ["SeeQ v2.0.0", "Germany"]
        cap = len("SeeQ v2.0.0")  # only the first piece fits
        self.assertEqual(logsync.build_comment(pieces, cap), "SeeQ v2.0.0")

    def test_order_is_priority_first_kept_first(self):
        # Even though "Germany" would fit alone, priority order means it's
        # evaluated second and only kept if the combined string still fits.
        result = logsync.build_comment(["SeeQ v2.0.0", "Germany"], 15)
        self.assertEqual(result, "SeeQ v2.0.0")

    def test_single_piece_too_long_is_hard_truncated_not_dropped(self):
        result = logsync.build_comment(["a very long single piece of text"], 10)
        self.assertEqual(result, "a very lon")
        self.assertEqual(len(result), 10)

    def test_no_pieces_returns_empty(self):
        self.assertEqual(logsync.build_comment([], 200), "")

    def test_all_falsy_returns_empty(self):
        self.assertEqual(logsync.build_comment([None, ""], 200), "")

    def test_max_len_zero_returns_empty(self):
        self.assertEqual(logsync.build_comment(["SeeQ v2.0.0"], 0), "")


class TestInjectComment(unittest.TestCase):
    """inject_comment(rec_str, comment): adds an ADIF <comment:len>value
    field just before <eor> -- pure string op, never re-parses/reorders the
    rest of the record."""

    REC = ("<call:5>W1ABC<gridsquare:4>EN61<mode:3>FT8<band:3>20m <eor>\n")

    def test_adds_comment_field_before_eor(self):
        out = logsync.inject_comment(self.REC, "SeeQ v2.0.0")
        self.assertIn("<comment:11>SeeQ v2.0.0", out)
        self.assertLess(out.index("<comment:"), out.index("<eor>"))

    def test_empty_comment_leaves_record_untouched(self):
        self.assertEqual(logsync.inject_comment(self.REC, ""), self.REC)

    def test_none_comment_leaves_record_untouched(self):
        self.assertEqual(logsync.inject_comment(self.REC, None), self.REC)

    def test_existing_comment_field_never_duplicated(self):
        rec = "<call:5>W1ABC<comment:5>hello<eor>\n"
        out = logsync.inject_comment(rec, "SeeQ v2.0.0")
        self.assertEqual(out, rec)

    def test_existing_comment_field_case_insensitive_detection(self):
        rec = "<call:5>W1ABC<COMMENT:5>hello<eor>\n"
        out = logsync.inject_comment(rec, "SeeQ v2.0.0")
        self.assertEqual(out, rec)

    def test_call_field_still_extractable_after_injection(self):
        out = logsync.inject_comment(self.REC, "SeeQ v2.0.0")
        self.assertEqual(logsync.extract_call(out), "W1ABC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
