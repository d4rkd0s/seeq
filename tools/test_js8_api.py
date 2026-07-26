#!/usr/bin/env python3
"""Tests for bin/modes/js8/api.py -- the line-delimited JSON client for
JS8Call-improved's TCP API.

Tested against a REAL localhost socket (FakeJs8Server below) rather than a
mocked socket object, because the things most likely to break here are framing
concerns -- partial reads, two JSON objects arriving in one recv(), an
unsolicited push landing in the middle of a request/response pair -- and a
mock that hands back whole messages on demand tests none of that.

No JS8Call-improved binary is launched, no radio is touched, and every timeout
in here is sub-second.
Run: python3 tools/test_js8_api.py
"""
import importlib.util
import json
import os
import socket
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(ROOT, "bin", "modes", "js8", "api.py")


def _api_module():
    spec = importlib.util.spec_from_file_location("js8_api", API_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


api = _api_module()


class FakeJs8Server:
    """A real TCP listener on 127.0.0.1 speaking JS8Call-improved's protocol.

    `responses` maps a request type to the reply dict (or a callable taking the
    request and returning one, or None for endpoints that genuinely don't
    answer -- PING, TX.SEND_MESSAGE, WINDOW.RAISE). Anything in `pending_pushes`
    is emitted *before* the next reply, which is how the real API behaves: push
    events are interleaved with command traffic, not queued politely behind it.
    """

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.received = []
        self.pending_pushes = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(2)
        self.port = self.sock.getsockname()[1]
        self.conn = None
        self._connected = threading.Event()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.conn = conn
            self._connected.set()
            try:
                self._handle(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle(self, conn):
        buf = b""
        while not self._stop.is_set():
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                msg = json.loads(line.decode())
                self.received.append(msg)
                while self.pending_pushes:
                    self._write(conn, self.pending_pushes.pop(0))
                reply = self.responses.get(msg.get("type"))
                if callable(reply):
                    reply = reply(msg)
                if reply is not None:
                    self._write(conn, reply)

    @staticmethod
    def _write(conn, obj):
        conn.sendall((json.dumps(obj) + "\n").encode())

    def wait_connected(self, timeout=2.0):
        """Block until the accept() has actually landed. Without this, a test
        that pushes immediately after Js8Client.connect() returns can beat the
        server thread to assigning self.conn."""
        if not self._connected.wait(timeout):
            raise AssertionError("fake server never accepted a connection")
        return self.conn

    def wait_received(self, count, timeout=2.0):
        """Block until `count` requests have been fully parsed server-side.
        Needed for fire-and-forget sends, which return before the server has
        necessarily read them."""
        deadline = time.monotonic() + timeout
        while len(self.received) < count:
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"expected {count} request(s), saw {len(self.received)}: "
                    f"{[m.get('type') for m in self.received]}")
            time.sleep(0.005)
        return self.received

    def push(self, obj):
        """Emit an unsolicited event right now, unprompted."""
        self._write(self.wait_connected(), obj)

    def queue_push(self, obj):
        """Emit this immediately before the next command reply."""
        self.pending_pushes.append(obj)

    def close(self):
        self._stop.set()
        for s in (self.conn, self.sock):
            try:
                if s:
                    s.close()
            except OSError:
                pass


PTT_OFF = {"params": {"MESSAGE": "", "PTT": False, "_ID": 269908447335},
           "type": "RIG.PTT_STATUS", "value": ""}
PTT_ON = {"params": {"MESSAGE": "SLbuEVAt7YC0", "PTT": True, "_ID": 269908640060},
          "type": "RIG.PTT_STATUS", "value": ""}
FREQ = {"params": {"DIAL": 7078000, "FREQ": 7079950, "OFFSET": 1950, "_ID": 269554125481},
        "type": "RIG.FREQ", "value": ""}
# Pushes carry _ID:-1 -- these are the interleaved event stream, not replies.
PUSH_PTT_ON = {"params": {"PTT": True, "UTC": 1768760160665, "_ID": -1},
               "type": "RIG.PTT", "value": "on"}
PUSH_DIRECTED = {"params": {"CMD": " HEARTBEAT", "DIAL": 7078000, "FROM": "KE2DMC",
                            "GRID": "FN32", "OFFSET": 816, "SNR": 1, "SPEED": 0,
                            "TO": "@HB", "UTC": 1769740226361, "_ID": -1},
                 "type": "RX.DIRECTED", "value": "KE2DMC: @HB HEARTBEAT"}


class TestEncodeDecode(unittest.TestCase):
    """Pure framing helpers -- no socket involved."""

    def test_encode_is_one_newline_terminated_json_object(self):
        line = api.encode("PING")
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(line.count("\n"), 1)
        obj = json.loads(line)
        self.assertEqual(obj["type"], "PING")

    def test_encode_always_includes_params_even_when_empty(self):
        # API.md: '{"params":xxx} is **required** in both directions'.
        obj = json.loads(api.encode("PING"))
        self.assertIn("params", obj)
        self.assertIn("value", obj)

    def test_encode_stamps_an_id(self):
        obj = json.loads(api.encode("RIG.GET_PTT"))
        self.assertIn("_ID", obj["params"])
        self.assertNotEqual(obj["params"]["_ID"], -1)

    def test_encode_passes_params_through(self):
        obj = json.loads(api.encode("RIG.SET_FREQ", params={"DIAL": 14078000, "OFFSET": 1500}))
        self.assertEqual(obj["params"]["DIAL"], 14078000)
        self.assertEqual(obj["params"]["OFFSET"], 1500)

    def test_decode_parses_a_line(self):
        self.assertEqual(api.decode(json.dumps(PTT_OFF))["type"], "RIG.PTT_STATUS")

    def test_is_push_event(self):
        self.assertTrue(api.is_push(PUSH_PTT_ON))
        self.assertTrue(api.is_push(PUSH_DIRECTED))
        self.assertFalse(api.is_push(PTT_OFF))
        self.assertFalse(api.is_push(FREQ))


class TestClientRequests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.server = None
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.close()
        if self.server:
            self.server.close()

    def _connect(self, responses):
        self.server = FakeJs8Server(responses)
        self.client = api.Js8Client(port=self.server.port, timeout=1.0,
                                     on_event=self.events.append)
        self.client.connect()
        return self.client

    def test_request_returns_the_matching_reply(self):
        c = self._connect({"RIG.GET_PTT": PTT_OFF})
        reply = c.request("RIG.GET_PTT")
        self.assertEqual(reply["type"], "RIG.PTT_STATUS")
        self.assertIs(reply["params"]["PTT"], False)

    def test_request_skips_interleaved_pushes_and_reports_them(self):
        # The real API pushes RIG.PTT/RX.* whenever they happen, including
        # between our command and its answer. Swallowing them would be wrong
        # (the watchdog needs them) and mistaking one for the answer would be
        # worse.
        c = self._connect({"RIG.GET_PTT": PTT_OFF})
        self.server.queue_push(PUSH_PTT_ON)
        self.server.queue_push(PUSH_DIRECTED)
        reply = c.request("RIG.GET_PTT")
        self.assertEqual(reply["type"], "RIG.PTT_STATUS")
        self.assertEqual([e["type"] for e in self.events], ["RIG.PTT", "RX.DIRECTED"])

    def test_api_error_reply_raises(self):
        err = {"params": {"_ID": "269558031750"}, "type": "API.ERROR",
               "value": "unterminated object: json parsing error"}
        c = self._connect({"RIG.GET_PTT": err})
        with self.assertRaises(api.Js8ApiError) as cm:
            c.request("RIG.GET_PTT")
        self.assertIn("json parsing error", str(cm.exception))

    def test_request_times_out_when_nothing_answers(self):
        c = self._connect({})  # server accepts, never replies
        with self.assertRaises(api.Js8ApiError) as cm:
            c.request("RIG.GET_PTT", timeout=0.3)
        self.assertIn("timeout", str(cm.exception).lower())

    def test_connect_to_a_dead_port_raises_a_clear_error(self):
        # Bind then immediately close, so the port is known-dead.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()
        c = api.Js8Client(port=dead_port, timeout=0.5)
        with self.assertRaises(api.Js8ApiError) as cm:
            c.connect()
        self.assertIn(str(dead_port), str(cm.exception))

    def test_send_is_fire_and_forget(self):
        c = self._connect({})
        c.send("PING")
        self.assertEqual(self.server.wait_received(1)[0]["type"], "PING")

    def test_two_objects_in_one_packet_are_split_correctly(self):
        # Framing: the client must not assume one recv() == one message.
        c = self._connect({})
        self.server.push(PUSH_PTT_ON)
        self.server.push(PUSH_DIRECTED)
        first = c.read_event(timeout=1.0)
        second = c.read_event(timeout=1.0)
        self.assertEqual(first["type"], "RIG.PTT")
        self.assertEqual(second["type"], "RX.DIRECTED")

    def test_read_event_returns_none_on_timeout(self):
        c = self._connect({})
        self.assertIsNone(c.read_event(timeout=0.2))


class TestClientHelpers(unittest.TestCase):
    """The typed wrappers pipeline.py/engine.py actually call."""

    def setUp(self):
        self.server = None
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.close()
        if self.server:
            self.server.close()

    def _connect(self, responses):
        self.server = FakeJs8Server(responses)
        self.client = api.Js8Client(port=self.server.port, timeout=1.0)
        self.client.connect()
        return self.client

    def test_get_ptt_returns_bool_and_message(self):
        c = self._connect({"RIG.GET_PTT": PTT_ON})
        ptt, msg = c.get_ptt()
        self.assertIs(ptt, True)
        self.assertEqual(msg, "SLbuEVAt7YC0")

    def test_get_freq_returns_dial_and_offset(self):
        c = self._connect({"RIG.GET_FREQ": FREQ})
        dial, offset = c.get_freq()
        self.assertEqual(dial, 7078000)
        self.assertEqual(offset, 1950)

    def test_tx_halt_sends_the_halt_frame(self):
        c = self._connect({"RIG.TX_HALT": {"params": {"_ID": 270426906894, "value": True},
                                            "type": "RIG.TX_HALT", "value": ""}})
        self.assertTrue(c.tx_halt())
        self.assertEqual(self.server.received[-1]["type"], "RIG.TX_HALT")

    def test_queue_depth(self):
        c = self._connect({"TX.GET_QUEUE_DEPTH": {"params": {"DEPTH": 2, "_ID": 270440267253},
                                                   "type": "TX.QUEUE_DEPTH", "value": ""}})
        self.assertEqual(c.queue_depth(), 2)

    def test_get_speed_and_names(self):
        c = self._connect({"MODE.GET_SPEED": {"params": {"SPEED": 4, "_ID": 1},
                                               "type": "MODE.SPEED", "value": ""}})
        self.assertEqual(c.get_speed(), 4)
        self.assertEqual(api.SPEED_NAMES[4], "Slow")

    def test_set_speed_accepts_either_documented_reply_form(self):
        # API.md: "Sometimes it comes back with the first form" -- MODE.SET_SPEED
        # answers with either MODE.SET_SPEED or STATION.STATUS.
        c = self._connect({"MODE.SET_SPEED": {"params": {"DIAL": 7078000, "SPEED": 0, "_ID": "1"},
                                               "type": "STATION.STATUS", "value": ""}})
        c.set_speed(api.SPEED_NORMAL)
        self.assertEqual(self.server.received[-1]["params"]["SPEED"], 0)

    def test_set_speed_rejects_a_number_that_is_not_a_speed(self):
        # The speed codes are 0/1/2/4/8 -- 3 is not one, and silently sending it
        # would put the rig in an undefined submode.
        c = self._connect({})
        with self.assertRaises(ValueError):
            c.set_speed(3)

    def test_send_message_clears_the_text_box_first(self):
        # API.md: "If the message window already has text displayed, this will
        # **NOT** transmit your new message!" -- so a bare TX.SEND_MESSAGE can
        # silently do nothing. Clearing first is the fix.
        c = self._connect({"TX.SET_TEXT": {"params": {"_ID": 1}, "type": "TX.TEXT", "value": ""}})
        c.send_message("N0CALL TEST")
        types = [m["type"] for m in self.server.wait_received(2)]
        self.assertEqual(types, ["TX.SET_TEXT", "TX.SEND_MESSAGE"])
        self.assertEqual(self.server.received[0]["value"], "")
        self.assertEqual(self.server.received[1]["value"], "N0CALL TEST")


class TestReachability(unittest.TestCase):
    def test_reachable_when_listening(self):
        srv = FakeJs8Server()
        try:
            self.assertTrue(api.is_reachable(port=srv.port, timeout=1.0))
        finally:
            srv.close()

    def test_not_reachable_when_nothing_is_listening(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()
        self.assertFalse(api.is_reachable(port=dead_port, timeout=0.5))


class TestDefaults(unittest.TestCase):
    def test_default_port_is_tcp_2442_not_udp_2242(self):
        # This is the one fact the fork's own docs get wrong: docs/API.md's
        # prose and its telnet example both say 2242, but Configuration.cpp
        # shows 2242 is UDPServerPort and TCP defaults to TCPServerPort=2442.
        # Building against the doc would connect to the wrong socket.
        self.assertEqual(api.DEFAULT_PORT, 2442)
        self.assertEqual(api.DEFAULT_UDP_PORT, 2242)


if __name__ == "__main__":
    unittest.main(verbosity=2)
