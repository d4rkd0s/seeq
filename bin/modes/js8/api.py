"""JS8 mode: line-delimited JSON client for JS8Call-improved's TCP API.

Verified against the fork's own `docs/API.md` (v3.0.x, 33 endpoints) and
`JS8_UI/Configuration.cpp`, pulled from github.com/JS8Call-improved/JS8Call-improved
-- not upstream JS8Call from memory, and not the bundled PDF user guide, which
is imprecise here. The roadmap's M1.2 note said to verify the API surface
against this fork specifically before writing any of this; doing so turned up
one thing the fork's own documentation gets wrong. See DEFAULT_PORT below.

Protocol shape: one JSON object per line, both directions, always
`{"type": ..., "value": ..., "params": {...}}`. `params` is required in both
directions even when empty (API.md states this explicitly).

The critical structural fact for everything built on top of this: the server
PUSHES unsolicited events -- RIG.PTT on/off, RX.ACTIVITY/DIRECTED/SPOT,
TX.FRAME, STATION.CLOSING -- interleaved with command replies. A reader can
never assume the next line is the answer to what it just asked. request()
therefore skips pushes (handing them to on_event) until it sees a line whose
type is a documented reply for the request it sent.

Stdlib only, and deliberately no dashboard import: this module is the lowest
layer of JS8 mode and gets loaded by the detached watchdog process, which must
stay as small and dependency-free as possible.
"""
import json
import socket
import time

DEFAULT_HOST = "127.0.0.1"

# docs/API.md's prose says "the API is normally located on localhost port 2242"
# and its telnet example connects there -- but Configuration.cpp shows 2242 is
# the UDP default (`UDPServerPort`, line 2427) and TCP's default is a different
# port entirely (`TCPServerPort`, 2442, line 2440). The doc's own telnet
# example (TCP) points at the UDP port. Trust the source, not the prose.
DEFAULT_PORT = 2442
DEFAULT_UDP_PORT = 2242

DEFAULT_TIMEOUT_S = 10.0

# MODE.SET_SPEED's codes (docs/API.md "MODE Speeds"). Deliberately NOT
# sequential -- 3 is not a speed, and sending it would select an undefined
# submode, so set_speed() validates against these rather than a range.
SPEED_NORMAL = 0
SPEED_FAST = 1
SPEED_TURBO = 2     # "JS8 40", formerly Turbo
SPEED_SLOW = 4
SPEED_ULTRA = 8     # "JS8 60" -- experimental and unreliable per the docs
SPEED_NAMES = {0: "Normal", 1: "Fast", 2: "JS8 40", 4: "Slow", 8: "JS8 60"}

# Per-frame TX duration by speed (API.md "Modulation" table), the basis for
# engine.py's per-frame watchdog deadlines.
SPEED_TX_SECONDS = {0: 12.64, 1: 7.9, 2: 3.95, 4: 25.28, 8: 60.0}

# Server-initiated messages: never a reply to anything we sent.
PUSH_TYPES = frozenset({
    "RIG.PTT",          # PTT on/off, the per-frame key-up signal
    "RX.ACTIVITY",
    "RX.DIRECTED",
    "RX.SPOT",
    "TX.FRAME",
    "STATION.CLOSING",  # clean shutdown only -- a crash sends nothing
})

# Request type -> acceptable reply type(s). Endpoints mapped to None are
# documented as answering nothing at all; request() refuses them so a caller
# can't hang waiting for a reply that is never coming.
REPLY_TYPES = {
    "PING": None,
    "RIG.GET_FREQ": ("RIG.FREQ",),
    "RIG.SET_FREQ": ("STATION.STATUS",),
    "RIG.GET_PTT": ("RIG.PTT_STATUS",),
    "RIG.SET_TUNE": ("RIG.SET_TUNE",),
    "RIG.TX_HALT": ("RIG.TX_HALT",),
    "STATION.GET_CALLSIGN": ("STATION.CALLSIGN",),
    "STATION.GET_GRID": ("STATION.GRID",),
    "STATION.SET_GRID": ("STATION.GRID",),
    "STATION.GET_INFO": ("STATION.INFO",),
    "STATION.SET_INFO": ("STATION.INFO",),
    "STATION.GET_STATUS": ("STATION.STATUS",),
    "STATION.SET_STATUS": ("STATION.STATUS",),
    "STATION.VERSION": ("STATION.VERSION",),
    "STATION.GET_OS": ("STATION.GET_OS",),
    "STATION.GET_SPOT": ("STATION.SPOT",),
    "STATION.SET_SPOT": ("STATION.SPOT",),
    "RX.GET_CALL_ACTIVITY": ("RX.CALL_ACTIVITY",),
    "RX.GET_CALL_SELECTED": ("RX.CALL_SELECTED",),
    "RX.GET_BAND_ACTIVITY": ("RX.BAND_ACTIVITY",),
    "RX.GET_TEXT": ("RX.TEXT",),
    "RX.GET_FREE_OFFSETS": ("RX.FREE_OFFSETS",),
    "TX.GET_TEXT": ("TX.TEXT",),
    "TX.SET_TEXT": ("TX.TEXT",),
    "TX.SEND_MESSAGE": None,   # answers only with the RIG.PTT/TX.FRAME pushes
    "TX.GET_QUEUE_DEPTH": ("TX.QUEUE_DEPTH",),
    "MODE.GET_SPEED": ("MODE.SPEED",),
    # API.md: "Sometimes it comes back with the first form" -- genuinely either.
    "MODE.SET_SPEED": ("MODE.SET_SPEED", "STATION.STATUS"),
    "INBOX.GET_MESSAGES": ("INBOX.MESSAGES",),
    "INBOX.STORE_MESSAGE": ("INBOX.MESSAGE",),
    "WINDOW.RAISE": None,
}

# API.md: "_ID number is the epoch time of 1499299200000 (July 6, 2017) plus
# current epoch time." Pushes carry _ID:-1.
_ID_EPOCH_OFFSET = 1499299200000


class Js8ApiError(Exception):
    """Any failure talking to JS8Call-improved: unreachable, timed out,
    malformed, or an API.ERROR reply."""


def next_id(clock_fn=time.time):
    return int(clock_fn() * 1000) + _ID_EPOCH_OFFSET


def encode(typ, value="", params=None, clock_fn=time.time):
    """One newline-terminated JSON line, params always present."""
    p = dict(params or {})
    p.setdefault("_ID", next_id(clock_fn))
    return json.dumps({"type": typ, "value": value, "params": p}) + "\n"


def decode(line):
    try:
        obj = json.loads(line)
    except ValueError as e:
        raise Js8ApiError(f"malformed JSON from JS8Call API: {e}") from e
    if not isinstance(obj, dict):
        raise Js8ApiError(f"expected a JSON object from JS8Call API, got {type(obj).__name__}")
    return obj


def is_push(msg):
    """True for server-initiated events, which are never a reply to us."""
    return msg.get("type") in PUSH_TYPES


def is_reachable(host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=2.0):
    """Can we open a TCP connection at all? Used by pipeline.py's readiness
    polling and sanity checks -- deliberately cheaper and more forgiving than
    a full connect+PING, since it's called in a loop while the GUI boots."""
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


class Js8Client:
    """One TCP connection to JS8Call-improved.

    Not thread-safe by design: engine.py's watchdog opens its OWN client on its
    own socket rather than sharing one, so that a wedged command connection
    can't also block the safety path. Sharing a single connection between the
    two would defeat the point.
    """

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 timeout=DEFAULT_TIMEOUT_S, on_event=None, clock_fn=time.monotonic):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.on_event = on_event
        self._clock = clock_fn
        self._sock = None
        self._buf = b""

    # -- lifecycle ---------------------------------------------------------
    def connect(self):
        try:
            self._sock = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as e:
            raise Js8ApiError(
                f"cannot reach JS8Call API at {self.host}:{self.port} ({e}) -- is "
                f"JS8Call-improved running with Accept TCP Requests enabled?") from e
        self._sock.settimeout(self.timeout)
        return self

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def connected(self):
        return self._sock is not None

    # -- framing -----------------------------------------------------------
    def _require_sock(self):
        if self._sock is None:
            raise Js8ApiError("not connected -- call connect() first")
        return self._sock

    def _next_line(self, deadline):
        """One complete JSON line, or None if `deadline` passes first.

        Buffers across recv() boundaries in both directions: a single recv can
        return half a message or three whole ones, and both happen in practice
        once RX.* pushes start flowing during a busy band.
        """
        sock = self._require_sock()
        while True:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                if line.strip():
                    return line.decode("utf-8", "replace")
                continue
            remaining = deadline - self._clock()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                return None
            except OSError as e:
                raise Js8ApiError(f"JS8Call API connection lost: {e}") from e
            if not chunk:
                raise Js8ApiError("JS8Call API closed the connection")
            self._buf += chunk

    # -- messaging ---------------------------------------------------------
    def send(self, typ, value="", params=None):
        """Fire-and-forget. Used for endpoints that answer nothing, and by the
        watchdog's halt path where waiting for an ack would be the wrong
        instinct -- get the halt onto the wire first."""
        sock = self._require_sock()
        try:
            sock.sendall(encode(typ, value, params).encode())
        except OSError as e:
            raise Js8ApiError(f"failed sending {typ} to JS8Call API: {e}") from e

    def read_event(self, timeout=None):
        """Next pushed event, or None on timeout. Command replies arriving here
        (rare -- a late answer to something we gave up on) are handed to
        on_event too rather than silently dropped."""
        deadline = self._clock() + (self.timeout if timeout is None else timeout)
        line = self._next_line(deadline)
        if line is None:
            return None
        return decode(line)

    def request(self, typ, value="", params=None, timeout=None):
        """Send a command and return its reply, skipping any pushes that arrive
        in between. Raises Js8ApiError on API.ERROR, timeout, or disconnect."""
        if typ not in REPLY_TYPES:
            raise ValueError(f"unknown JS8 API endpoint {typ!r}")
        expected = REPLY_TYPES[typ]
        if expected is None:
            raise ValueError(
                f"{typ} does not send a reply -- use send() instead of request()")
        self.send(typ, value, params)
        deadline = self._clock() + (self.timeout if timeout is None else timeout)
        while True:
            line = self._next_line(deadline)
            if line is None:
                raise Js8ApiError(f"timeout waiting for {expected[0]} in reply to {typ}")
            msg = decode(line)
            mtype = msg.get("type")
            if mtype == "API.ERROR":
                raise Js8ApiError(f"JS8Call API rejected {typ}: {msg.get('value')}")
            if mtype in expected:
                return msg
            # Anything else is an interleaved push (or an unrelated reply);
            # surface it, don't swallow it -- the watchdog lives on these.
            if self.on_event:
                self.on_event(msg)

    # -- typed helpers -----------------------------------------------------
    def ping(self):
        """PING answers nothing; it just wakes the API up (API.md)."""
        self.send("PING")

    def get_ptt(self):
        """(ptt: bool, message: str). MESSAGE is non-empty while more is being
        transmitted."""
        p = self.request("RIG.GET_PTT")["params"]
        return bool(p.get("PTT")), p.get("MESSAGE", "")

    def get_freq(self):
        """(dial_hz, offset_hz). FREQ is dial+offset, so it's derivable."""
        p = self.request("RIG.GET_FREQ")["params"]
        return int(p.get("DIAL", 0)), int(p.get("OFFSET", 0))

    def set_freq(self, dial, offset):
        p = self.request("RIG.SET_FREQ", params={"DIAL": int(dial), "OFFSET": int(offset)})["params"]
        return int(p.get("DIAL", 0)), int(p.get("OFFSET", 0))

    def tx_halt(self):
        """Halt the transmitter immediately. This is the strongest lever SeeQ
        has in JS8 mode -- and it only works if JS8Call-improved is alive
        enough to service it. See engine.py's module docstring."""
        return bool(self.request("RIG.TX_HALT")["params"].get("value", True))

    def queue_depth(self):
        return int(self.request("TX.GET_QUEUE_DEPTH")["params"].get("DEPTH", 0))

    def get_speed(self):
        return int(self.request("MODE.GET_SPEED")["params"].get("SPEED", 0))

    def set_speed(self, speed):
        if speed not in SPEED_NAMES:
            raise ValueError(
                f"{speed} is not a JS8 speed code (valid: "
                f"{', '.join(f'{k}={v}' for k, v in sorted(SPEED_NAMES.items()))})")
        return self.request("MODE.SET_SPEED", params={"SPEED": int(speed)})

    def set_text(self, text):
        return self.request("TX.SET_TEXT", value=text)

    def send_message(self, text):
        """Queue `text` for the next TX cycle.

        Clears the text box first, deliberately: API.md warns that if the
        message window already has text in it, TX.SEND_MESSAGE will silently
        NOT transmit. A send that quietly does nothing is a worse failure than
        one that errors, so never issue a bare TX.SEND_MESSAGE.
        """
        self.set_text("")
        self.send("TX.SEND_MESSAGE", value=text)

    def rx_text(self):
        return self.request("RX.GET_TEXT").get("value", "")

    def call_activity(self):
        """{callsign: {GRID, SNR, UTC}} for recently heard stations."""
        p = dict(self.request("RX.GET_CALL_ACTIVITY")["params"])
        p.pop("_ID", None)
        return p

    def band_activity(self):
        """{offset_str: {DIAL, FREQ, OFFSET, SNR, TEXT, UTC}}."""
        p = dict(self.request("RX.GET_BAND_ACTIVITY")["params"])
        p.pop("_ID", None)
        return p

    def inbox_messages(self):
        return self.request("INBOX.GET_MESSAGES")["params"].get("MESSAGES", [])

    def inbox_store(self, callsign, text):
        return self.request("INBOX.STORE_MESSAGE",
                             params={"CALLSIGN": callsign, "TEXT": text})["params"].get("ID")

    def version(self):
        return self.request("STATION.VERSION")["params"].get("VERSION", "")

    def callsign(self):
        return self.request("STATION.GET_CALLSIGN").get("value", "")

    def grid(self):
        return self.request("STATION.GET_GRID").get("value", "")
