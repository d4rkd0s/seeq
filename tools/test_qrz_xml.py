#!/usr/bin/env python3
"""Tests for bin/qrz_xml_api.py -- the QRZ XML (callsign/bio lookup) API
client. Pure XML parsing + response-shape logic; network I/O (_get) is
monkeypatched, never actually called. Run: python3 tools/test_qrz_xml.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import qrz_xml_api as qx

LOGIN_OK = """<?xml version="1.0" ?>
<QRZDatabase version="1.34">
 <Session>
  <Key>abc123sessionkey</Key>
  <Count>123</Count>
  <SubExp>Wed Jan 1 12:34:03 2013</SubExp>
  <GMTime>Sun Aug 16 03:51:47 2012</GMTime>
 </Session>
</QRZDatabase>"""

LOGIN_ERROR = """<?xml version="1.0" ?>
<QRZDatabase version="1.34">
 <Session>
  <Error>Username/password incorrect</Error>
  <GMTime>Sun Nov 16 05:11:58 2003</GMTime>
 </Session>
</QRZDatabase>"""

LOOKUP_OK = """<?xml version="1.0" ?>
<QRZDatabase version="1.34">
 <Callsign>
  <call>AA7BQ</call>
  <fname>FRED L</fname>
  <name>LLOYD</name>
  <country>United States</country>
  <image>https://files.qrz.com/q/aa7bq/aa7bq.jpg</image>
 </Callsign>
 <Session>
  <Key>abc123sessionkey</Key>
  <GMTime>Sun Nov 16 04:13:46 2012</GMTime>
 </Session>
</QRZDatabase>"""

LOOKUP_NO_PHOTO = """<?xml version="1.0" ?>
<QRZDatabase version="1.34">
 <Callsign>
  <call>W1AW</call>
  <fname>ARRL</fname>
  <name>HQ OPERATORS CLUB</name>
  <country>United States</country>
 </Callsign>
 <Session>
  <Key>abc123sessionkey</Key>
 </Session>
</QRZDatabase>"""

LOOKUP_NOT_FOUND = """<?xml version="1.0" ?>
<QRZDatabase version="1.34">
 <Session>
  <Error>Not found: zz9zzz</Error>
  <Key>abc123sessionkey</Key>
 </Session>
</QRZDatabase>"""

SESSION_EXPIRED = """<?xml version="1.0" ?>
<QRZDatabase version="1.34">
 <Session>
  <Error>Session Timeout</Error>
  <GMTime>Sun Nov 16 05:11:58 2003</GMTime>
 </Session>
</QRZDatabase>"""


class TestParseResponse(unittest.TestCase):
    def test_parses_session_and_callsign_blocks(self):
        p = qx.parse_response(LOOKUP_OK)
        self.assertEqual(p["callsign"]["call"], "AA7BQ")
        self.assertEqual(p["callsign"]["image"], "https://files.qrz.com/q/aa7bq/aa7bq.jpg")
        self.assertEqual(p["session"]["Key"], "abc123sessionkey")

    def test_malformed_xml_returns_empty_not_raises(self):
        self.assertEqual(qx.parse_response("not xml at all"), {"session": {}, "callsign": {}})

    def test_empty_string_returns_empty(self):
        self.assertEqual(qx.parse_response(""), {"session": {}, "callsign": {}})


class TestLogin(unittest.TestCase):
    def test_ok_returns_session_key(self):
        orig = qx._get
        qx._get = lambda params, timeout=20: (True, LOGIN_OK)
        try:
            ok, key = qx.login("user", "pass")
            self.assertTrue(ok)
            self.assertEqual(key, "abc123sessionkey")
        finally:
            qx._get = orig

    def test_bad_credentials_returns_error_not_key(self):
        orig = qx._get
        qx._get = lambda params, timeout=20: (True, LOGIN_ERROR)
        try:
            ok, reason = qx.login("user", "wrongpass")
            self.assertFalse(ok)
            self.assertIn("incorrect", reason)
        finally:
            qx._get = orig

    def test_transport_failure_propagates(self):
        orig = qx._get
        qx._get = lambda params, timeout=20: (False, "curl not found on this system")
        try:
            ok, reason = qx.login("user", "pass")
            self.assertFalse(ok)
            self.assertIn("curl", reason)
        finally:
            qx._get = orig


class TestLookup(unittest.TestCase):
    def test_ok_with_photo(self):
        orig = qx._get
        qx._get = lambda params, timeout=20: (True, LOOKUP_OK)
        try:
            ok, fields = qx.lookup("abc123sessionkey", "AA7BQ")
            self.assertTrue(ok)
            self.assertEqual(fields["image"], "https://files.qrz.com/q/aa7bq/aa7bq.jpg")
        finally:
            qx._get = orig

    def test_ok_without_photo_no_image_key(self):
        orig = qx._get
        qx._get = lambda params, timeout=20: (True, LOOKUP_NO_PHOTO)
        try:
            ok, fields = qx.lookup("abc123sessionkey", "W1AW")
            self.assertTrue(ok)
            self.assertNotIn("image", fields)
            self.assertEqual(fields["call"], "W1AW")
        finally:
            qx._get = orig

    def test_not_found_is_not_ok(self):
        orig = qx._get
        qx._get = lambda params, timeout=20: (True, LOOKUP_NOT_FOUND)
        try:
            ok, reason = qx.lookup("abc123sessionkey", "ZZ9ZZZ")
            self.assertFalse(ok)
            self.assertIn("Not found", reason)
        finally:
            qx._get = orig

    def test_expired_session_is_not_ok(self):
        orig = qx._get
        qx._get = lambda params, timeout=20: (True, SESSION_EXPIRED)
        try:
            ok, reason = qx.lookup("expiredkey", "AA7BQ")
            self.assertFalse(ok)
            self.assertIn("Session Timeout", reason)
        finally:
            qx._get = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
