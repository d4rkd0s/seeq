#!/usr/bin/env python3
"""Unit tests for the pure (no-network, no-radio) parts of the QRZ Logbook
integration: bin/adif.py (ADIF record parsing), bin/qrz_api.py (QRZ response
parsing/decoding), bin/logbook.py (local<->QRZ record matching + merged
status view). Run: python3 tools/test_qrz.py

All I/O (curl, file reads) lives outside the functions tested here; every
test feeds data in as arguments. Callsigns are fakes (K1ABC/W9XYZ style),
never a real operator's.
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import adif
import qrz_api
import logbook

# A realistic WSJT-X-style ADIF body (two records) with a header.
ADIF_TEXT = (
    "WSJT-X ADIF Export<eoh>\n"
    "<call:5>K1ABC<gridsquare:4>FN42<mode:3>FT8<rst_sent:3>-08<rst_rcvd:3>-05"
    "<qso_date:8>20260704<time_on:6>003800<qso_date_off:8>20260704<time_off:6>003959"
    "<band:3>40m<freq:8>7.074421<station_callsign:6>N0CALL<my_gridsquare:4>AA00"
    "<tx_pwr:1>5 <eor>\n"
    "<call:5>W9XYZ<gridsquare:4>EM48<mode:3>FT8<rst_sent:3>+02<rst_rcvd:3>+02"
    "<qso_date:8>20260704<time_on:6>020343<band:3>40m<freq:8>7.075912"
    "<station_callsign:6>N0CALL<my_gridsquare:4>AA00<tx_pwr:2>10 <eor>\n"
)


def rec(call="K1ABC", band="40m", mode="FT8", date="20260704", time_on="003800", **kw):
    d = {"call": call, "band": band, "mode": mode, "qso_date": date, "time_on": time_on}
    d.update(kw)
    return d


class TestAdifParseFields(unittest.TestCase):
    def test_basic_fields(self):
        recs = adif.split_records(ADIF_TEXT.encode())
        self.assertEqual(len(recs), 2)
        f = adif.parse_fields(recs[0][0].decode())
        self.assertEqual(f["call"], "K1ABC")
        self.assertEqual(f["gridsquare"], "FN42")
        self.assertEqual(f["qso_date"], "20260704")
        self.assertEqual(f["time_on"], "003800")
        self.assertEqual(f["band"], "40m")

    def test_field_names_case_insensitive_and_lowercased(self):
        f = adif.parse_fields("<CALL:5>K1ABC<Band:3>40m<eor>")
        self.assertEqual(f["call"], "K1ABC")
        self.assertEqual(f["band"], "40m")

    def test_length_prefix_respected_not_delimiters(self):
        # value contains a '<' lookalike? length must win, not regex greed
        f = adif.parse_fields("<comment:9>a<b> then<call:5>W9XYZ<eor>")
        self.assertEqual(f["comment"], "a<b> then")
        self.assertEqual(f["call"], "W9XYZ")

    def test_type_suffix_in_specifier(self):
        # ADIF allows <name:len:type>
        f = adif.parse_fields("<freq:8:N>7.074421<eor>")
        self.assertEqual(f["freq"], "7.074421")

    def test_records_from_bytes_convenience(self):
        recs = adif.records_from_bytes(ADIF_TEXT.encode())
        self.assertEqual([r["call"] for r in recs], ["K1ABC", "W9XYZ"])

    def test_empty_and_headerless(self):
        self.assertEqual(adif.records_from_bytes(b""), [])
        recs = adif.records_from_bytes(b"<call:5>K1ABC<band:3>40m<eor>")
        self.assertEqual(recs[0]["call"], "K1ABC")


class TestQrzResponseParsing(unittest.TestCase):
    def test_parse_fields_basic(self):
        f = qrz_api.parse_fields("RESULT=OK&COUNT=1&LOGID=12345")
        self.assertEqual(f["RESULT"], "OK")
        self.assertEqual(f["COUNT"], "1")
        self.assertEqual(f["LOGID"], "12345")

    def test_parse_fields_raw_unescaped_values(self):
        # Real duplicate-INSERT response, verified against QRZ's live API by
        # two production implementations: values arrive RAW (spaces, colons,
        # no URL-encoding), and STATUS/RESULT/EXTENDED all appear.
        f = qrz_api.parse_fields(
            "STATUS=FAIL&RESULT=FAIL&REASON=Unable to add QSO to database: duplicate&EXTENDED=")
        self.assertEqual(f["RESULT"], "FAIL")
        self.assertEqual(f["REASON"], "Unable to add QSO to database: duplicate")
        self.assertEqual(f["EXTENDED"], "")

    def test_parse_fields_survives_raw_ampersands_in_adif(self):
        # QRZ HTML-entity-encodes the ADIF's angle brackets, so the ADIF
        # value contains RAW '&' characters (&lt; &gt;) — a naive &-split
        # would shred it. Only known response keys may act as boundaries.
        resp = "RESULT=OK&COUNT=1&ADIF=&lt;call:5&gt;K1ABC&lt;band:3&gt;40m&lt;eor&gt;"
        f = qrz_api.parse_fields(resp)
        self.assertEqual(f["RESULT"], "OK")
        self.assertEqual(f["COUNT"], "1")
        self.assertIn("&lt;call:5&gt;", f["ADIF"])

    def test_result_of_prefers_result_then_status(self):
        self.assertEqual(qrz_api.result_of({"RESULT": "OK"}), ("OK", ""))
        r, reason = qrz_api.result_of({"STATUS": "FAIL", "REASON": "x"})
        self.assertEqual(r, "FAIL")
        self.assertEqual(reason, "x")

    def test_result_of_reason_falls_back_to_raw(self):
        r, reason = qrz_api.result_of({"RESULT": "FAIL"}, raw="RESULT=FAIL&junk")
        self.assertEqual(reason, "RESULT=FAIL&junk")

    def test_extract_adif_decodes_records(self):
        # as-on-the-wire: HTML entities, raw & inside the value, no URL enc.
        resp = ("RESULT=OK&COUNT=2&ADIF="
                "&lt;call:5&gt;K1ABC&lt;band:3&gt;40m&lt;app_qrzlog_status:1&gt;C"
                "&lt;app_qrzlog_logid:6&gt;123456&lt;eor&gt;"
                "&lt;call:5&gt;W9XYZ&lt;band:3&gt;40m&lt;app_qrzlog_status:1&gt;N&lt;eor&gt;")
        recs = qrz_api.extract_adif_records(qrz_api.parse_fields(resp))
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["call"], "K1ABC")
        self.assertEqual(recs[0]["app_qrzlog_status"], "C")
        self.assertEqual(recs[0]["app_qrzlog_logid"], "123456")
        self.assertEqual(recs[1]["app_qrzlog_status"], "N")

    def test_extract_adif_absent(self):
        self.assertEqual(qrz_api.extract_adif_records({"RESULT": "OK"}), [])

    def test_fetch_distinguishes_transport_failure_from_qrz_fail(self):
        # transport failure (curl error, no HTTP response) must NOT look
        # like QRZ answering RESULT=FAIL — callers treat FAIL-with-no-
        # records as "empty logbook" and would clobber a good cache.
        orig = qrz_api.post
        try:
            qrz_api.post = lambda form, timeout=30: (False, "curl exit 6: could not resolve host")
            result, reason, recs = qrz_api.fetch("FAKE-KEY")
            self.assertEqual(result, "TRANSPORT")
            self.assertEqual(recs, [])
            qrz_api.post = lambda form, timeout=30: (True, "RESULT=FAIL&REASON=no records")
            result, reason, recs = qrz_api.fetch("FAKE-KEY")
            self.assertEqual(result, "FAIL")
        finally:
            qrz_api.post = orig

    def test_status_data_keys_recovered(self):
        # ACTION=STATUS nests an &-joined DATA blob; because its inner keys
        # are in the known-key set, flat parsing still recovers them.
        resp = "RESULT=OK&DATA=BOOKID=99&COUNT=14&CONFIRMED=3&DXCC_COUNT=5"
        f = qrz_api.parse_fields(resp)
        self.assertEqual(f["COUNT"], "14")
        self.assertEqual(f["CONFIRMED"], "3")


class TestQsoEpoch(unittest.TestCase):
    def test_full_hhmmss(self):
        t = logbook.qso_epoch(rec(date="20260704", time_on="003800"))
        self.assertEqual(t % 60, 0)
        t2 = logbook.qso_epoch(rec(date="20260704", time_on="003900"))
        self.assertEqual(t2 - t, 60)

    def test_hhmm_only(self):
        # ADIF allows 4-digit times
        t4 = logbook.qso_epoch(rec(time_on="0038"))
        t6 = logbook.qso_epoch(rec(time_on="003800"))
        self.assertEqual(t4, t6)

    def test_missing_time_returns_none(self):
        self.assertIsNone(logbook.qso_epoch({"call": "K1ABC"}))
        self.assertIsNone(logbook.qso_epoch(rec(time_on="")))


class TestMatching(unittest.TestCase):
    def test_exact_match_confirmed(self):
        local = [rec()]
        remote = [rec(app_qrzlog_status="C")]
        rows = logbook.merge(local, remote, synced_through=10**9)
        self.assertEqual(rows[0]["qrz"], "confirmed")

    def test_only_status_c_counts_as_confirmed(self):
        # QRZ status codes: C=Confirmed is the only confirmed value;
        # N/2/S/R/A are all shades of not-confirmed.
        for status in ("N", "2", "S", "R", "A", "V", ""):
            rows = logbook.merge([rec()], [rec(app_qrzlog_status=status)],
                                 synced_through=0)
            self.assertEqual(rows[0]["qrz"], "uploaded", f"status={status!r}")

    def test_match_within_tolerance(self):
        local = [rec(time_on="003800")]
        remote = [rec(time_on="004512", app_qrzlog_status="N")]   # +7m12s
        rows = logbook.merge(local, remote, synced_through=10**9, tol_s=1800)
        self.assertEqual(rows[0]["qrz"], "uploaded")

    def test_no_match_outside_tolerance(self):
        local = [rec(time_on="003800")]
        remote = [rec(time_on="043800", app_qrzlog_status="C")]   # +4h
        rows = logbook.merge(local, remote, synced_through=0, tol_s=1800)
        self.assertEqual(rows[0]["qrz"], "not synced")

    def test_band_mismatch_never_matches(self):
        local = [rec(band="40m")]
        remote = [rec(band="20m", app_qrzlog_status="C")]
        rows = logbook.merge(local, remote, synced_through=0)
        self.assertEqual(rows[0]["qrz"], "not synced")

    def test_call_mismatch_never_matches(self):
        rows = logbook.merge([rec(call="K1ABC")], [rec(call="W9XYZ", app_qrzlog_status="C")],
                             synced_through=0)
        self.assertEqual(rows[0]["qrz"], "not synced")

    def test_nearest_remote_wins_and_is_consumed(self):
        # two locals 30 min apart, two remotes each within tolerance of both:
        # each remote must be used at most once, nearest-first
        local = [rec(time_on="010000"), rec(time_on="013000")]
        remote = [rec(time_on="010100", app_qrzlog_status="C"),
                  rec(time_on="012900", app_qrzlog_status="N")]
        rows = logbook.merge(local, remote, synced_through=10**9, tol_s=1800)
        self.assertEqual(rows[0]["qrz"], "confirmed")
        self.assertEqual(rows[1]["qrz"], "uploaded")

    def test_offset_marks_uploaded_without_fetch_data(self):
        # record ends before the synced byte offset -> at least "uploaded"
        # even when the QRZ fetch cache is empty/stale
        local = [dict(rec(), _end=100)]
        rows = logbook.merge(local, [], synced_through=150)
        self.assertEqual(rows[0]["qrz"], "uploaded")
        rows = logbook.merge(local, [], synced_through=50)
        self.assertEqual(rows[0]["qrz"], "not synced")

    def test_case_insensitive_call_and_band(self):
        rows = logbook.merge([rec(call="k1abc", band="40M")],
                             [rec(call="K1ABC", band="40m", app_qrzlog_status="C")],
                             synced_through=0)
        self.assertEqual(rows[0]["qrz"], "confirmed")

    def test_rows_carry_display_fields(self):
        rows = logbook.merge([rec(gridsquare="FN42", rst_sent="-08", rst_rcvd="-05")],
                             [], synced_through=0)
        r = rows[0]
        for k in ("call", "band", "grid", "date", "time", "sent", "rcvd", "qrz"):
            self.assertIn(k, r)
        self.assertEqual(r["grid"], "FN42")
        self.assertEqual(r["sent"], "-08")


if __name__ == "__main__":
    unittest.main(verbosity=2)
