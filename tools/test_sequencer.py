#!/usr/bin/env python3
"""Unit tests for the pure (no-radio, no-TX) parts of bin/qso.py.

Covers: pick_offset parity filtering / ceiling / exclusion / higher-freq
preference, directed-CQ parsing + filter policy, busy-detection, and
stalled-CQ detection. Run: python3 tools/test_sequencer.py
"""
import os, sys, tempfile, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import qso

EVEN, ODD = "000000", "000015"          # slot ids with even/odd parity

def dec(freq, slot=EVEN, snr=-10, msg="CQ K0XXX EN10"):
    return {"freq": freq, "slot": slot, "snr": snr, "msg": msg}


class TestPickOffset(unittest.TestCase):
    def test_other_parity_ignored(self):
        # even-slot signals at 400/800/2400 leave 800-2400 as the big gap;
        # the odd-slot signal at 1600 sits mid-gap and must NOT count for an
        # even-parity transmitter -> we land right on it
        decs = [dec(400, EVEN), dec(800, EVEN), dec(2400, EVEN), dec(1600, ODD)]
        f0, gap = qso.pick_offset(decs, our_parity=0)
        self.assertEqual(f0, 1600)

    def test_same_parity_blocks(self):
        # the same signal in OUR parity slot must push us off 1600
        decs = [dec(400, EVEN), dec(800, EVEN), dec(2400, EVEN), dec(1600, EVEN)]
        f0, gap = qso.pick_offset(decs, our_parity=0)
        self.assertNotEqual(f0, 1600)
        self.assertGreaterEqual(min(abs(f0 - x) for x in (400, 800, 1600, 2400)), 60)

    def test_ceiling_and_floor(self):
        for decs in ([], [dec(1425)], [dec(500), dec(700), dec(900)],
                     [dec(f) for f in range(450, 2450, 73)]):
            f0, _ = qso.pick_offset(decs, 0)
            self.assertGreaterEqual(f0, 400)
            self.assertLessEqual(f0, 2450)

    def test_exclusion_zone(self):
        # empty band would pick 1425; excluding it must move >100 Hz away
        f0_free, _ = qso.pick_offset([], 0)
        f0, _ = qso.pick_offset([], 0, exclude_hz=[f0_free])
        self.assertGreater(abs(f0 - f0_free), 100)

    def test_higher_freq_preferred_among_near_equal_gaps(self):
        # 340-1400 and 1400-2510 are within 20% of each other -> take the
        # higher-frequency candidate
        f0, _ = qso.pick_offset([dec(1400, EVEN)], 0)
        self.assertGreater(f0, 1400)

    def test_clearance_at_least_60(self):
        decs = [dec(1000, EVEN), dec(1300, EVEN)]
        f0, _ = qso.pick_offset(decs, 0)
        self.assertGreaterEqual(min(abs(f0 - 1000), abs(f0 - 1300)), 60)


class TestCQFilter(unittest.TestCase):
    def decision(self, msg, dx_mode=False):
        """None = not answerable; else (call, grid)."""
        pc = qso.parse_cq(msg)
        if not pc:
            return None
        call, grid, mod = pc
        return (call, grid) if qso.cq_answerable(mod, dx_mode) else None

    def test_plain_cq_answer(self):
        self.assertEqual(self.decision("CQ K1ABC FN42"), ("K1ABC", "FN42"))

    def test_cq_dx_skip(self):
        self.assertIsNone(self.decision("CQ DX K1ABC"))

    def test_cq_dx_still_skipped_dx_mode_off(self):
        self.assertIsNone(self.decision("CQ DX K1ABC", dx_mode=False))

    def test_cq_dx_answered_when_dx_mode_on(self):
        self.assertEqual(self.decision("CQ DX K1ABC", dx_mode=True), ("K1ABC", ""))

    def test_cq_test_still_skipped_in_dx_mode(self):
        # contest CQs are never answerable, DX Mode or not
        self.assertIsNone(self.decision("CQ TEST K1ABC", dx_mode=True))

    def test_cq_pota_answer(self):
        self.assertEqual(self.decision("CQ POTA W9AV EN53"), ("W9AV", "EN53"))

    def test_cq_eu_skip(self):
        self.assertIsNone(self.decision("CQ EU K1ABC"))

    def test_cq_test_skip(self):
        self.assertIsNone(self.decision("CQ TEST K1ABC"))

    def test_cq_qrp_answer(self):
        self.assertEqual(self.decision("CQ QRP K1ABC"), ("K1ABC", ""))

    def test_short_call_with_grid(self):
        # K2A is a call (grid follows), not a modifier
        self.assertEqual(self.decision("CQ K2A FN13"), ("K2A", "FN13"))

    def test_no_grid_answer_empty_grid(self):
        self.assertEqual(self.decision("CQ N0XYZ"), ("N0XYZ", ""))

    def test_case_insensitive_modifier(self):
        self.assertIsNone(self.decision("CQ dx K1ABC"))
        self.assertEqual(self.decision("CQ pota W9AV EN53"), ("W9AV", "EN53"))

    def test_compound_call(self):
        self.assertEqual(self.decision("CQ VE3ABC/W8"), ("VE3ABC/W8", ""))

    def test_jt9_artifact_stripped(self):
        # rx-loop decodes carry trailing decoder markers like 'a1'
        self.assertEqual(self.decision("CQ AD9DU EN52                         a1"),
                         ("AD9DU", "EN52"))

    def test_not_a_cq(self):
        self.assertIsNone(self.decision("W9XYZ K1ABC -05"))


class TestBusyDetection(unittest.TestCase):
    def test_report_to_other_is_busy(self):
        self.assertTrue(qso.target_busy("W9XYZ K1ABC -05", "K1ABC"))
        self.assertTrue(qso.target_busy("W9XYZ K1ABC R-12", "K1ABC"))

    def test_report_to_us_is_not_busy(self):
        self.assertFalse(qso.target_busy("N0CALL K1ABC -05", "K1ABC", mycall="N0CALL"))

    def test_cq_is_not_busy_and_free(self):
        self.assertFalse(qso.target_busy("CQ K1ABC FN42", "K1ABC"))
        self.assertTrue(qso.target_free("CQ K1ABC FN42", "K1ABC"))

    def test_rr73_to_other_reads_as_free(self):
        # RR73 ends his QSO -> we may resume calling next cycle
        self.assertTrue(qso.target_free("W9XYZ K1ABC RR73", "K1ABC"))

    def test_other_station_message_not_busy(self):
        self.assertFalse(qso.target_busy("W9XYZ N4QQQ -05", "K1ABC"))


class TestTxBoundary(unittest.TestCase):
    """compute_tx_boundary(t, our_parity) — pure extraction of transmit()'s
    slot-scheduling math (dashboard "time to next TX" instrumentation).
    Must stay behaviorally identical to the inline version it replaced."""

    def test_join_current_slot_within_late_window(self):
        # our_parity=0 slot starts at 30 (30%30=0 -> parity 0); 1.0 s in,
        # inside the <1.8 s late-join window -> join immediately
        self.assertEqual(qso.compute_tx_boundary(31.0, 0), 30)

    def test_at_the_window_edge_does_not_join(self):
        # exactly 1.8 s in: condition is strict "< 1.8", so this must NOT
        # join the current slot — falls through to the next same-parity one
        self.assertEqual(qso.compute_tx_boundary(31.8, 0), 60)

    def test_just_inside_the_window_joins(self):
        self.assertEqual(qso.compute_tx_boundary(31.79, 0), 30)

    def test_late_in_slot_skips_to_next_same_parity(self):
        # 2 s into a parity-0 slot (30) -> too late, next parity-0 slot is 30 s later
        self.assertEqual(qso.compute_tx_boundary(32.0, 0), 60)

    def test_wrong_parity_advances_to_next_matching_slot(self):
        # t=47 falls in the slot starting at 45 (45%30=15 -> parity 1);
        # our_parity=0 -> next parity-0 slot is 60
        self.assertEqual(qso.compute_tx_boundary(47.0, 0), 60)

    def test_odd_parity_symmetry(self):
        # mirror of test_join_current_slot_within_late_window for parity 1
        # (slot 45 has parity 1)
        self.assertEqual(qso.compute_tx_boundary(46.0, 1), 45)

    def test_boundary_always_matches_requested_parity(self):
        # property check across a spread of times/parities: the result must
        # always (a) be a 15 s slot boundary, (b) carry the requested parity,
        # (c) be >= the current slot start (never scheduled in the past)
        for t in [0, 1, 14, 14.9, 15, 16, 29, 30, 30.5, 44, 44.9, 100.3, 12345.6]:
            for p in (0, 1):
                b = qso.compute_tx_boundary(t, p)
                self.assertEqual(b % 15, 0, f"t={t} p={p} -> {b} not a slot boundary")
                self.assertEqual(qso.parity(b), p, f"t={t} p={p} -> {b} has wrong parity")
                self.assertGreaterEqual(b, qso.slot_start(t))


class TestDialOk(unittest.TestCase):
    """dial_ok(cat_freq, dial_hz, tolerance_hz) — pure extraction of
    transmit()'s frequency read-back safety gate (CLAUDE.md rule #2:
    "Frequency read-back before every key-up"). A small tolerance absorbs
    harmless CAT read-back jitter/rounding (real rigs, incl. this station's
    G90, can read back a few Hz/tens of Hz off a commanded frequency) without
    weakening what the check actually guards against: a genuinely wrong
    dial/band, which is always off by kHz, not Hz."""

    def test_exact_match_ok(self):
        self.assertTrue(qso.dial_ok("14074000", 14074000, 100))

    def test_within_tolerance_ok(self):
        self.assertTrue(qso.dial_ok("14074010", 14074000, 100))

    def test_at_tolerance_edge_ok(self):
        self.assertTrue(qso.dial_ok("14074100", 14074000, 100))

    def test_just_outside_tolerance_rejected(self):
        self.assertFalse(qso.dial_ok("14074101", 14074000, 100))

    def test_below_tolerance_also_checked(self):
        self.assertTrue(qso.dial_ok("14073990", 14074000, 100))
        self.assertFalse(qso.dial_ok("14073800", 14074000, 100))

    def test_real_mistune_still_rejected(self):
        # the actual incident this check exists for: whole band segments
        # off, not a rounding artifact.
        self.assertFalse(qso.dial_ok("14070000", 14074000, 100))

    def test_zero_tolerance_requires_exact_match(self):
        self.assertFalse(qso.dial_ok("14074010", 14074000, 0))
        self.assertTrue(qso.dial_ok("14074000", 14074000, 0))

    def test_unparseable_cat_freq_fails_closed(self):
        self.assertFalse(qso.dial_ok("", 14074000, 100))
        self.assertFalse(qso.dial_ok(None, 14074000, 100))
        self.assertFalse(qso.dial_ok("not-a-number", 14074000, 100))


class TestTargetSelection(unittest.TestCase):
    """select_target(cqs, requested_call) — honors a dashboard "request this
    call" click over the automatic SNR/pileup ranking, WITHOUT bypassing the
    SNR-floor/directed-CQ etiquette filters (those already ran before a
    candidate ever reaches `cqs`, so select_target only ever chooses among
    already-answerable candidates — this is what makes honoring a manual
    pick safe). skip_is_requested(ts, since) gates a skip-request's
    timestamp against when the current target pursuit began, so a stale
    skip from a previous target can't instantly abort a brand new one."""

    def cq(self, call, grid="FN20"):
        return ({"msg": f"CQ {call} {grid}", "snr": -10}, call, grid)

    def test_no_request_falls_back_to_first(self):
        cqs = [self.cq("K1ABC"), self.cq("W4VBK")]
        self.assertEqual(qso.select_target(cqs, None)[1], "K1ABC")

    def test_empty_string_request_falls_back_to_first(self):
        cqs = [self.cq("K1ABC"), self.cq("W4VBK")]
        self.assertEqual(qso.select_target(cqs, "")[1], "K1ABC")

    def test_request_matching_a_non_first_candidate_wins(self):
        cqs = [self.cq("K1ABC"), self.cq("W4VBK"), self.cq("N0XYZ")]
        self.assertEqual(qso.select_target(cqs, "W4VBK")[1], "W4VBK")

    def test_request_not_present_falls_back_to_first(self):
        # requested call isn't (or is no longer) among this cycle's
        # answerable CQs — never invent a target out of thin air
        cqs = [self.cq("K1ABC"), self.cq("W4VBK")]
        self.assertEqual(qso.select_target(cqs, "ZZ9ZZZ")[1], "K1ABC")

    def test_request_matching_first_is_a_noop(self):
        cqs = [self.cq("K1ABC"), self.cq("W4VBK")]
        self.assertEqual(qso.select_target(cqs, "K1ABC")[1], "K1ABC")


class TestSkipRequest(unittest.TestCase):
    def test_no_request_never_skips(self):
        self.assertFalse(qso.skip_is_requested(None, 1000.0))

    def test_stale_request_before_target_start_ignored(self):
        # skip was clicked for a PREVIOUS target — must not instantly
        # abort a brand new one that started after the click
        self.assertFalse(qso.skip_is_requested(999.0, 1000.0))

    def test_request_at_exact_start_honored(self):
        self.assertTrue(qso.skip_is_requested(1000.0, 1000.0))

    def test_request_after_start_honored(self):
        self.assertTrue(qso.skip_is_requested(1005.0, 1000.0))


class TestStalledCQDetection(unittest.TestCase):
    def test_target_cq_detected(self):
        self.assertTrue(qso.is_target_cq("CQ K1ABC FN42", "K1ABC"))
        self.assertTrue(qso.is_target_cq("CQ DX K1ABC", "K1ABC"))
        self.assertTrue(qso.is_target_cq("CQ K1ABC", "K1ABC"))

    def test_non_cq_not_detected(self):
        self.assertFalse(qso.is_target_cq("W9XYZ K1ABC RR73", "K1ABC"))
        self.assertFalse(qso.is_target_cq("K1ABC N0CALL AA00", "K1ABC"))

    def test_other_cq_not_detected(self):
        self.assertFalse(qso.is_target_cq("CQ W9XYZ EN53", "K1ABC"))


class TestDXFilter(unittest.TestCase):
    """dx_filter_ok(call, dx_mode, mycall) — DX Mode's country/DXCC-entity
    candidate gate, applied to every CQ candidate (not just directed CQ DX
    ones). dx_mode=False must be a total no-op (existing default behavior)."""

    def test_off_always_true(self):
        self.assertTrue(qso.dx_filter_ok("K1ABC", dx_mode=False, mycall="W1AW"))

    def test_on_true_for_foreign_known_country(self):
        self.assertTrue(qso.dx_filter_ok("DL1ABC", dx_mode=True, mycall="W1AW"))

    def test_on_false_for_domestic(self):
        self.assertFalse(qso.dx_filter_ok("K5XYZ", dx_mode=True, mycall="W1AW"))

    def test_on_false_for_unknown_country(self):
        self.assertFalse(qso.dx_filter_ok("QQ9ZZZ", dx_mode=True, mycall="W1AW"))


class TestArgParsing(unittest.TestCase):
    """build_argparser() — pure ArgumentParser construction, extracted out of
    main() so CLI flags are unit-testable without invoking main()'s hunting
    loop / radio I/O."""

    def test_dx_only_defaults_false(self):
        self.assertFalse(qso.build_argparser().parse_args([]).dx_only)

    def test_dx_only_flag_parses_true(self):
        self.assertTrue(qso.build_argparser().parse_args(["--dx-only"]).dx_only)

    def test_existing_flags_unchanged(self):
        a = qso.build_argparser().parse_args(["--max-qsos", "3", "--minutes", "5"])
        self.assertEqual(a.max_qsos, 3)
        self.assertEqual(a.minutes, 5.0)


class TestCandidateTier(unittest.TestCase):
    """candidate_tier(call, logged_countries, home_call) — the 4-tier
    priority non-DX-mode ranking uses: 0 = country never logged (new DX,
    any distance), 1 = worked DX that's NOT a near neighbor of home
    (genuine long-distance DX), 2 = worked DX that IS a near neighbor of
    home (e.g. Canada/Mexico for a US station -- still DX, still ranked
    above home), 3 = home country (or unresolved/unmapped -- fails CLOSED,
    same convention as dxcc.is_new_country/is_dx_call/is_neighbor_call: an
    unmapped call can never claim a tier it can't prove)."""

    def test_new_country_is_tier_0(self):
        self.assertEqual(qso.candidate_tier("DL1ABC", set(), "K5XYZ"), 0)

    def test_new_neighbor_country_is_still_tier_0(self):
        # unworked always wins, even a neighbor -- matches the "unless the
        # user doesn't have Canada/Mexico yet" requirement.
        self.assertEqual(qso.candidate_tier("VE3ABC", set(), "W1AW"), 0)

    def test_worked_long_distance_dx_is_tier_1(self):
        self.assertEqual(qso.candidate_tier("DL1ABC", {"Germany"}, "W1AW"), 1)

    def test_worked_neighbor_dx_is_tier_2(self):
        self.assertEqual(qso.candidate_tier("VE3ABC", {"Canada"}, "W1AW"), 2)
        self.assertEqual(qso.candidate_tier("XE1ABC", {"Mexico"}, "W1AW"), 2)

    def test_home_country_is_tier_3(self):
        self.assertEqual(qso.candidate_tier("W1AW", {"United States"}, "K5XYZ"), 3)

    def test_unmapped_call_fails_closed_to_tier_3(self):
        self.assertEqual(qso.candidate_tier("QQ9ZZZ", set(), "K5XYZ"), 3)

    def test_unmapped_home_call_fails_closed_to_tier_3_for_known_country(self):
        # home country can't be resolved -> is_dx_call fails closed False,
        # so even a clearly-foreign call that's already logged can't prove
        # it's "DX" relative to an unknown home -- falls to tier 3, not 1/2.
        self.assertEqual(qso.candidate_tier("DL1ABC", {"Germany"}, "QQ9ZZZ"), 3)


class TestRankCqs(unittest.TestCase):
    """rank_cqs(cqs, dx_mode, logged_countries, pileup_penalty, home_call) —
    DX Mode's hard-priority candidate ranking, extracted out of main()'s
    hunt loop so it's unit-testable without radio I/O. Pure: synthetic
    (decode, call, grid) tuples, no ADIF files, no decode history."""

    def cq(self, call, snr):
        return ({"snr": snr}, call, "")

    def test_dx_mode_off_new_country_beats_worked_dx_and_home(self):
        # priority order requested: new-country DX first, then already-
        # worked DX, then home country -- regardless of raw SNR.
        cqs = [self.cq("W1AW", -3), self.cq("F5AAA", -10), self.cq("DL1ABC", -20)]
        logged = {"United States", "France"}  # Germany not logged -> DL1ABC new
        ranked = qso.rank_cqs(cqs, False, logged, {}, home_call="K5XYZ")
        self.assertEqual([c for _, c, _ in ranked], ["DL1ABC", "F5AAA", "W1AW"])

    def test_dx_mode_off_worked_dx_beats_home_even_if_weaker(self):
        cqs = [self.cq("W1AW", -3), self.cq("F5AAA", -18)]
        logged = {"United States", "France"}
        ranked = qso.rank_cqs(cqs, False, logged, {}, home_call="K5XYZ")
        self.assertEqual([c for _, c, _ in ranked], ["F5AAA", "W1AW"])

    def test_dx_mode_off_long_distance_dx_beats_neighbor_dx_even_if_weaker(self):
        # the LD-vs-neighbor request: EU/Africa DX outranks Canada/Mexico
        # DX once both are already worked, regardless of raw SNR.
        cqs = [self.cq("VE3ABC", -3), self.cq("DL1ABC", -18)]
        logged = {"United States", "Canada", "Germany"}
        ranked = qso.rank_cqs(cqs, False, logged, {}, home_call="W1AW")
        self.assertEqual([c for _, c, _ in ranked], ["DL1ABC", "VE3ABC"])

    def test_dx_mode_off_neighbor_dx_still_beats_home_even_if_weaker(self):
        cqs = [self.cq("K5XYZ", -3), self.cq("VE3ABC", -18)]
        logged = {"United States", "Canada"}
        ranked = qso.rank_cqs(cqs, False, logged, {}, home_call="W1AW")
        self.assertEqual([c for _, c, _ in ranked], ["VE3ABC", "K5XYZ"])

    def test_dx_mode_off_unworked_neighbor_still_beats_worked_long_distance(self):
        # tier 0 (new) always wins, even a neighbor, over tier 1 (worked LD).
        cqs = [self.cq("DL1ABC", -3), self.cq("VE3ABC", -18)]
        logged = {"United States", "Germany"}  # Canada not logged -> VE3ABC new
        ranked = qso.rank_cqs(cqs, False, logged, {}, home_call="W1AW")
        self.assertEqual([c for _, c, _ in ranked], ["VE3ABC", "DL1ABC"])

    def test_dx_mode_off_ties_broken_by_snr_within_tier(self):
        cqs = [self.cq("F5AAA", -18), self.cq("DL1ABC", -5)]  # both worked-DX
        logged = {"France", "Germany"}
        ranked = qso.rank_cqs(cqs, False, logged, {}, home_call="K5XYZ")
        self.assertEqual([c for _, c, _ in ranked], ["DL1ABC", "F5AAA"])

    def test_dx_mode_off_all_home_country_is_pure_snr(self):
        # regression proof: when nothing is DX, behavior matches the old
        # pure-SNR ranking exactly (the common single-country case).
        cqs = [self.cq("W1AW", -18), self.cq("K5XYZ", -3)]
        logged = {"United States"}
        ranked = qso.rank_cqs(cqs, False, logged, {}, home_call="K5XYZ")
        self.assertEqual([c for _, c, _ in ranked], ["K5XYZ", "W1AW"])

    def test_dx_mode_off_no_home_call_degrades_to_new_vs_not(self):
        # home_call omitted/unresolvable -> is_dx_call fails closed, so this
        # degrades gracefully to "new country first, everything else by SNR"
        # rather than mis-ranking anything as home when it can't prove it.
        cqs = [self.cq("W1AW", -3), self.cq("DL1ABC", -18)]
        logged = {"United States"}
        ranked = qso.rank_cqs(cqs, False, logged, {})
        self.assertEqual([c for _, c, _ in ranked], ["DL1ABC", "W1AW"])

    def test_dx_mode_on_new_country_beats_higher_snr_old_country(self):
        # the key hard-priority proof: a WEAK new-country candidate outranks
        # a STRONG already-logged-country candidate.
        cqs = [self.cq("K5XYZ", -3), self.cq("DL1ABC", -18)]
        logged = {"United States"}  # Germany not logged -> DL1ABC is new
        ranked = qso.rank_cqs(cqs, True, logged, {})
        self.assertEqual([c for _, c, _ in ranked], ["DL1ABC", "K5XYZ"])

    def test_dx_mode_on_ties_broken_by_snr_within_group(self):
        cqs = [self.cq("DL1ABC", -18), self.cq("F5AAA", -5),
               self.cq("K5XYZ", -3), self.cq("W1AW", -20)]
        logged = {"United States"}  # DL1ABC, F5AAA both new; K5XYZ, W1AW both old
        ranked = qso.rank_cqs(cqs, True, logged, {})
        self.assertEqual([c for _, c, _ in ranked], ["F5AAA", "DL1ABC", "K5XYZ", "W1AW"])

    def test_dx_mode_on_no_new_countries_falls_back_to_snr_order(self):
        cqs = [self.cq("K5XYZ", -3), self.cq("W1AW", -18)]
        logged = {"United States"}
        ranked = qso.rank_cqs(cqs, True, logged, {})
        self.assertEqual([c for _, c, _ in ranked], ["K5XYZ", "W1AW"])

    def test_pileup_penalty_applied_within_group(self):
        cqs = [self.cq("DL1ABC", -10), self.cq("F5AAA", -8)]
        logged = set()  # both new
        # F5AAA has a heavier pileup penalty, should drop below DL1ABC
        ranked = qso.rank_cqs(cqs, True, logged, {"F5AAA": 6, "DL1ABC": 0})
        self.assertEqual([c for _, c, _ in ranked], ["DL1ABC", "F5AAA"])


class TestDxPriorityBump(unittest.TestCase):
    """dx_priority_bump(cqs, dx_mode, logged_countries, pileup_penalty) —
    detects whether rank_cqs()'s hard-priority reorder actually changed this
    cycle's top pick, so the hunt loop can log it (only when it mattered,
    not every cycle). Pure, same synthetic-tuple style as TestRankCqs."""

    def cq(self, call, snr):
        return ({"snr": snr}, call, "")

    def test_dx_mode_off_returns_none(self):
        # even though DL1ABC would out-prioritize K5XYZ if dx_mode were on,
        # dx_mode False must never report a bump.
        cqs = [self.cq("K5XYZ", -3), self.cq("DL1ABC", -18)]
        logged = {"United States"}
        self.assertIsNone(qso.dx_priority_bump(cqs, False, logged, {}))

    def test_no_candidates_returns_none(self):
        self.assertIsNone(qso.dx_priority_bump([], True, set(), {}))

    def test_new_country_promoted_over_one_stronger_returns_bump(self):
        cqs = [self.cq("K5XYZ", -3), self.cq("DL1ABC", -18)]
        logged = {"United States"}  # Germany not logged -> DL1ABC is new, weaker
        self.assertEqual(qso.dx_priority_bump(cqs, True, logged, {}),
                          ("DL1ABC", 1))

    def test_new_country_promoted_over_two_stronger_returns_correct_count(self):
        cqs = [self.cq("K5XYZ", -3), self.cq("W1AW", -5), self.cq("DL1ABC", -18)]
        logged = {"United States"}  # both US calls outrank DL1ABC on plain SNR
        self.assertEqual(qso.dx_priority_bump(cqs, True, logged, {}),
                          ("DL1ABC", 2))

    def test_new_country_already_top_pick_returns_none(self):
        # DL1ABC is both the strongest signal AND the new country -- rank_cqs
        # doesn't actually move anything, so nothing is worth logging.
        cqs = [self.cq("DL1ABC", -3), self.cq("K5XYZ", -18)]
        logged = {"United States"}
        self.assertIsNone(qso.dx_priority_bump(cqs, True, logged, {}))

    def test_no_new_countries_returns_none(self):
        cqs = [self.cq("K5XYZ", -3), self.cq("W1AW", -18)]
        logged = {"United States"}
        self.assertIsNone(qso.dx_priority_bump(cqs, True, logged, {}))


class TestCallsignNovelty(unittest.TestCase):
    """novelty_rank(call, worked_alltime) — 0 for a callsign never worked,
    1 for one already in the log all-time. Used only as a TIE-BREAKER
    inside candidate_tier's tiers, never across them: the DX priority
    (new country > long-distance DX > neighbour DX > home) still decides
    first, and novelty only orders candidates that tier has already put on
    equal footing. Fails OPEN to 0 (treat as new) when the worked set is
    unavailable, so a missing/unreadable ADIF degrades to the previous
    pure-SNR-within-tier behaviour rather than deprioritizing everyone."""

    def test_never_worked_call_ranks_ahead(self):
        self.assertEqual(qso.novelty_rank("W1AW", {"K5XYZ"}), 0)

    def test_already_worked_call_ranks_behind(self):
        self.assertEqual(qso.novelty_rank("K5XYZ", {"K5XYZ"}), 1)

    def test_comparison_is_case_insensitive(self):
        self.assertEqual(qso.novelty_rank("k5xyz", {"K5XYZ"}), 1)
        self.assertEqual(qso.novelty_rank("K5XYZ", {"k5xyz"}), 1)

    def test_none_worked_set_fails_open_to_new(self):
        self.assertEqual(qso.novelty_rank("K5XYZ", None), 0)

    def test_empty_worked_set_treats_everything_as_new(self):
        self.assertEqual(qso.novelty_rank("K5XYZ", set()), 0)


class TestEffectiveTier(unittest.TestCase):
    """effective_tier(call, logged_countries, home_call, worked_alltime) —
    candidate_tier, then ONE tier of demotion (floored at the home tier, 3)
    if the callsign has already been worked all-time. This is the structural
    half of the worked-call cost: it is what lets an already-worked
    neighbour-DX station fall level with never-worked home-country stations
    instead of permanently outranking them."""

    def test_never_worked_call_keeps_its_tier(self):
        self.assertEqual(
            qso.effective_tier("VE3ABC", {"United States", "Canada"}, "W1AW", set()), 2)

    def test_worked_neighbour_dx_drops_to_the_home_tier(self):
        self.assertEqual(
            qso.effective_tier("VE3ABC", {"United States", "Canada"}, "W1AW",
                               {"VE3ABC"}), 3)

    def test_worked_long_distance_dx_drops_to_the_neighbour_tier(self):
        self.assertEqual(
            qso.effective_tier("DL1ABC", {"United States", "Germany"}, "W1AW",
                               {"DL1ABC"}), 2)

    def test_worked_home_country_call_is_floored_not_pushed_past_3(self):
        self.assertEqual(
            qso.effective_tier("W1AW", {"United States"}, "K5XYZ", {"W1AW"}), 3)

    def test_no_worked_set_means_no_demotion(self):
        self.assertEqual(
            qso.effective_tier("VE3ABC", {"United States", "Canada"}, "W1AW", None), 2)


class TestWorkedCallCost(unittest.TestCase):
    """The combined worked-call cost, through rank_cqs(..., worked_alltime=).

    Two mechanisms working together, as agreed: the dominant one is the
    one-tier demotion (effective_tier); the finer one is a dB penalty
    (NOVELTY_PENALTY_DB) that orders candidates once demotion has put them in
    the same tier. The dB penalty is deliberately a WEIGHT, not a veto -- a
    sufficiently stronger already-worked station still wins."""

    def cq(self, call, snr):
        return ({"snr": snr}, call, "")

    def test_worked_neighbour_dx_no_longer_beats_a_new_home_station(self):
        """The VE2OPC case from the log review -- the reason for this change.

        Before: a Canadian worked four times, at -16 dB, outranked every
        never-worked US station because Canada is tier 2 and the US is tier 3.
        """
        cqs = [self.cq("VE2OLD", -16), self.cq("W1NEW", -7)]
        ranked = qso.rank_cqs(cqs, False, {"United States", "Canada"}, {},
                              home_call="K5XYZ", worked_alltime={"VE2OLD"})
        self.assertEqual([c for _, c, _ in ranked], ["W1NEW", "VE2OLD"])

    def test_a_never_worked_neighbour_keeps_full_dx_priority(self):
        """DX priority itself is NOT weakened -- only re-working is penalised."""
        cqs = [self.cq("VE3NEW", -20), self.cq("W1NEW", -7)]
        ranked = qso.rank_cqs(cqs, False, {"United States", "Canada"}, {},
                              home_call="K5XYZ", worked_alltime=set())
        self.assertEqual([c for _, c, _ in ranked], ["VE3NEW", "W1NEW"])

    def test_new_country_still_outranks_everything(self):
        cqs = [self.cq("W1NEW", -3), self.cq("DL1ABC", -22)]
        ranked = qso.rank_cqs(cqs, False, {"United States"}, {},
                              home_call="K5XYZ", worked_alltime=set())
        self.assertEqual([c for _, c, _ in ranked], ["DL1ABC", "W1NEW"])

    def test_db_penalty_flips_a_marginally_stronger_worked_call(self):
        # Same tier. W1OLD is 3 dB stronger but already worked; the 4 dB
        # penalty is enough to put the fresh station first.
        cqs = [self.cq("W1OLD", -10), self.cq("W1NEW", -13)]
        ranked = qso.rank_cqs(cqs, False, {"United States"}, {},
                              home_call="K5XYZ", worked_alltime={"W1OLD"})
        self.assertEqual([c for _, c, _ in ranked], ["W1NEW", "W1OLD"])

    def test_db_penalty_is_a_weight_not_a_veto(self):
        """A clearly stronger worked station still wins -- this is what makes
        it a weighted cost rather than an absolute novelty priority."""
        cqs = [self.cq("W1OLD", -2), self.cq("W1NEW", -18)]
        ranked = qso.rank_cqs(cqs, False, {"United States"}, {},
                              home_call="K5XYZ", worked_alltime={"W1OLD"})
        self.assertEqual([c for _, c, _ in ranked], ["W1OLD", "W1NEW"])

    def test_penalty_size_is_configurable(self):
        cqs = [self.cq("W1OLD", -2), self.cq("W1NEW", -18)]
        ranked = qso.rank_cqs(cqs, False, {"United States"}, {},
                              home_call="K5XYZ", worked_alltime={"W1OLD"},
                              novelty_penalty_db=30)
        self.assertEqual([c for _, c, _ in ranked], ["W1NEW", "W1OLD"])

    def test_zero_penalty_leaves_only_the_tier_demotion(self):
        cqs = [self.cq("W1OLD", -10), self.cq("W1NEW", -13)]
        ranked = qso.rank_cqs(cqs, False, {"United States"}, {},
                              home_call="K5XYZ", worked_alltime={"W1OLD"},
                              novelty_penalty_db=0)
        self.assertEqual([c for _, c, _ in ranked], ["W1OLD", "W1NEW"])

    def test_pileup_penalty_and_novelty_penalty_both_apply(self):
        # W1OLD -6 dB, worked (-4), 1 competitor (-6) => -16 effective.
        cqs = [self.cq("W1OLD", -6), self.cq("W1NEW", -14)]
        ranked = qso.rank_cqs(cqs, False, {"United States"}, {"W1OLD": 6},
                              home_call="K5XYZ", worked_alltime={"W1OLD"})
        self.assertEqual([c for _, c, _ in ranked], ["W1NEW", "W1OLD"])

    def test_omitting_worked_alltime_preserves_previous_ranking_exactly(self):
        """Backwards compatibility: the old signature must behave as before."""
        cqs = [self.cq("K5XYZ", -3), self.cq("W1AW", -18)]
        old = qso.rank_cqs(cqs, False, {"United States"}, {}, home_call="N0AAA")
        self.assertEqual([c for _, c, _ in old], ["K5XYZ", "W1AW"])

    def test_dx_mode_penalises_worked_calls_within_its_groups(self):
        cqs = [self.cq("F5OLD", -10), self.cq("F9NEW", -13)]
        ranked = qso.rank_cqs(cqs, True, {"France"}, {}, worked_alltime={"F5OLD"})
        self.assertEqual([c for _, c, _ in ranked], ["F9NEW", "F5OLD"])

    def test_dx_mode_hard_new_country_priority_is_untouched(self):
        cqs = [self.cq("W1NEW", -3), self.cq("DL1ABC", -22)]
        ranked = qso.rank_cqs(cqs, True, {"United States"}, {}, worked_alltime=set())
        self.assertEqual([c for _, c, _ in ranked], ["DL1ABC", "W1NEW"])


class TestAllTimeWorkedCalls(unittest.TestCase):
    def test_returns_all_calls_regardless_of_date(self):
        with tempfile.TemporaryDirectory() as d:
            adif_path = os.path.join(d, "log.adi")
            with open(adif_path, "w") as f:
                f.write("<call:6>DL1ABC<qso_date:8>20250101<eor>"
                        "<call:5>W1ABC<qso_date:8>20260101<eor>")
            old_adif = qso.ADIF
            qso.ADIF = adif_path
            try:
                self.assertEqual(qso.all_time_worked_calls(), {"DL1ABC", "W1ABC"})
            finally:
                qso.ADIF = old_adif

    def test_missing_file_returns_empty_set(self):
        old_adif = qso.ADIF
        qso.ADIF = "/nonexistent/path/log.adi"
        try:
            self.assertEqual(qso.all_time_worked_calls(), set())
        finally:
            qso.ADIF = old_adif


class TestEffectiveSnrFloor(unittest.TestCase):
    """effective_snr_floor(): the dashboard's SNR-floor slider writes a live
    override file (SNR_FLOOR_REQ); this resolves it against station.conf's
    SNR_FLOOR without ever raising on a missing/bad override."""

    def test_no_override_uses_base_floor(self):
        self.assertEqual(qso.effective_snr_floor(-16, None), -16)

    def test_override_wins_when_present(self):
        self.assertEqual(qso.effective_snr_floor(-16, -20), -20)

    def test_string_override_from_json_is_coerced(self):
        self.assertEqual(qso.effective_snr_floor(-16, "-22"), -22)

    def test_garbage_override_falls_back_to_base(self):
        self.assertEqual(qso.effective_snr_floor(-16, "not a number"), -16)


class TestReadSnrFloorOverride(unittest.TestCase):
    def test_missing_file_returns_none(self):
        old = qso.SNR_FLOOR_REQ
        qso.SNR_FLOOR_REQ = "/nonexistent/path/snr-floor-request.json"
        try:
            self.assertIsNone(qso._read_snr_floor_override())
        finally:
            qso.SNR_FLOOR_REQ = old

    def test_reads_written_value(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "snr-floor-request.json")
            with open(path, "w") as f:
                f.write('{"snr_floor": -20}')
            old = qso.SNR_FLOOR_REQ
            qso.SNR_FLOOR_REQ = path
            try:
                self.assertEqual(qso._read_snr_floor_override(), -20)
            finally:
                qso.SNR_FLOOR_REQ = old


class TestSomeoneElseCallingUs(unittest.TestCase):
    """someone_else_calling_us(): true when a decode is addressed directly
    to MYCALL from a station OTHER than the one we're currently pursuing --
    a genuinely different caller hailing us mid-chase, used to shorten the
    current target's retry budget so we can pivot to them sooner."""

    def test_true_when_different_station_addresses_us(self):
        decodes = [{"msg": "W1AW K9ZZZ -09"}]
        self.assertTrue(qso.someone_else_calling_us(decodes, "W1AW", "K5XYZ"))

    def test_false_when_it_is_the_current_target_answering(self):
        # current_target == the caller -> that's our own target answering, not "someone else"
        decodes = [{"msg": "W1AW K5XYZ -09"}]
        self.assertFalse(qso.someone_else_calling_us(decodes, "W1AW", "K5XYZ"))

    def test_false_when_no_decodes_address_us(self):
        decodes = [{"msg": "CQ K9ZZZ EN61"}, {"msg": "K5XYZ K9ZZZ R-05"}]
        self.assertFalse(qso.someone_else_calling_us(decodes, "W1AW", "K5XYZ"))

    def test_false_on_empty_decodes(self):
        self.assertFalse(qso.someone_else_calling_us([], "W1AW", "K5XYZ"))


class TestRetryBudget(unittest.TestCase):
    """retry_budget(): the retry/repeat ceiling for the CURRENT target,
    halved when a different station is calling us directly -- finish up (or
    give up) sooner instead of burning the full repeat cap on a target
    that's leaving us hanging while someone else waits."""

    def test_full_budget_when_nobody_else_calling(self):
        self.assertEqual(qso.retry_budget(6, False), 6)

    def test_halved_when_someone_else_calling(self):
        self.assertEqual(qso.retry_budget(6, True), 3)

    def test_never_drops_below_one(self):
        self.assertEqual(qso.retry_budget(1, True), 1)


class TestTargetIsNewCountry(unittest.TestCase):
    """target_is_new_country(): whether the just-picked target represents a
    country never logged before -- drives the dashboard's new-country
    celebration flash. `logged` is now always meaningfully populated (the
    hunt loop computes it regardless of dx_mode, since non-DX-mode ranking
    needs it too -- see TestRankCqs), so this no longer gates on dx_mode."""

    def test_true_when_country_not_logged(self):
        self.assertTrue(qso.target_is_new_country("DL1ABC", set()))

    def test_false_when_country_already_logged(self):
        self.assertFalse(qso.target_is_new_country("DL1ABC", {"Germany"}))

    def test_false_when_call_unmapped(self):
        self.assertFalse(qso.target_is_new_country("QQ9ZZZ", set()))


class TestLogQsoDoesNotFlipOuterState(unittest.TestCase):
    """log_qso() writes the ADIF/QSOLOG entry mid-exchange, BEFORE the
    courtesy 73 has actually been transmitted (in main()'s state machine,
    log_qso() fires, then the inner state becomes "b73" and the 73 gets
    sent). It must never touch engine.json's outer 'state' field itself --
    doing so made the cockpit read "LOGGED" while NEXT TX/PTT still showed
    an active transmission for the courtesy 73, a contradictory display.
    Only the outer hunt loop, once the target's while loop has genuinely
    finished, may set state="logged"."""

    def test_does_not_touch_engine_state(self):
        with tempfile.TemporaryDirectory() as d:
            old_adif, old_qsolog, old_engine = qso.ADIF, qso.QSOLOG, qso.ENGINE_JSON
            qso.ADIF = os.path.join(d, "log.adi")
            qso.QSOLOG = os.path.join(d, "attempts.jsonl")
            qso.ENGINE_JSON = os.path.join(d, "engine.json")
            qso._engine["state"] = "qso"
            try:
                qso.log_qso("W1AW", "FN31", "+05", "-09", 1500, time.time())
                self.assertEqual(qso._engine["state"], "qso")
            finally:
                qso.ADIF, qso.QSOLOG, qso.ENGINE_JSON = old_adif, old_qsolog, old_engine
                qso._engine["state"] = "init"


if __name__ == "__main__":
    unittest.main(verbosity=2)
