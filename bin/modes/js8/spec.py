#!/usr/bin/env python3
"""JS8 wire-protocol constants -- the permanent record.

Every value here is transcribed from JS8Call-improved @ master (see
docs/js8/PROTOCOL.md for the full specification and the file-by-file
provenance). Once JS8Call is deleted from this machine at phase P7, THIS FILE
AND PROTOCOL.md ARE THE ONLY RECORD -- there is no upstream to re-read.
Do not "clean up" the literal tables below.

Provenance of the three big tables, all from JS8_Mode/JS8.cpp:
  G_ROWS_HEX  87 hex strings, 22 chars each (88 bits; the 88th is discarded)
              -- the dense LDPC generator, used for TX.
  MN          Mn[174][3] -- the 3 parity checks each codeword bit belongs to.
  NM          Nm[87]     -- the 5..7 codeword bits each parity check covers.

MN and NM are the sparse decode-side tables. They are logically redundant with
G, which is exactly what makes them valuable: tools/test_js8_frame.py encodes
with G and checks the syndrome with NM, so a transcription error in either
table fails the suite. Keep that test.
"""
from dataclasses import dataclass

# --- Frame geometry (JS8.cpp) -------------------------------------------------
NN = 79  # total channel symbols
ND = 58  # data symbols (29 parity + 29 message)
NS = 21  # sync symbols (3 Costas arrays of 7)

BITS_PER_SYMBOL = 3  # 8-FSK, MSB first, straight binary -- NO Gray coding
PAYLOAD_BITS = 72  # 12 chars x 6 bits
TYPE_BITS = 3  # i3bit transmission flags
CRC_BITS = 12
INFO_BITS = PAYLOAD_BITS + TYPE_BITS + CRC_BITS  # 87

# Symbol slots. Parity is transmitted BEFORE the message.
COSTAS_POS = (0, 36, 72)
PARITY_SLICE = slice(7, 36)  # 29 symbols
MESSAGE_SLICE = slice(43, 72)  # 29 symbols

# --- Costas arrays (JS8.h) ----------------------------------------------------
# ORIGINAL is byte-for-byte FT8's array; submode A/Normal still uses it.
COSTAS_ORIGINAL = (
    (4, 2, 5, 6, 1, 3, 0),
    (4, 2, 5, 6, 1, 3, 0),
    (4, 2, 5, 6, 1, 3, 0),
)
COSTAS_MODIFIED = (
    (0, 6, 2, 3, 5, 4, 1),
    (1, 5, 0, 2, 3, 6, 4),
    (2, 5, 0, 6, 4, 1, 3),
)

# --- Payload alphabet (JS8.cpp) ----------------------------------------------
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-+"
ALPHABET_INDEX = {c: i for i, c in enumerate(ALPHABET)}
MSG_CHARS = 12

# --- Transmission type flags (i3bit) -----------------------------------------
# A FLAG SET, not the frame type. The frame type lives inside the payload.
I3_ANY = 0
I3_FIRST = 1
I3_LAST = 2
I3_DATA = 4

# --- CRC-12 (JS8.cpp, via boost/crc.hpp) -------------------------------------
CRC_POLY = 0xC06  # truncated polynomial, width 12
CRC_INIT = 0
CRC_FINAL_XOR = 42

# --- LDPC ---------------------------------------------------------------------
LDPC_N = 174
LDPC_K = 87
LDPC_M = 87
BP_MAX_ITERATIONS = 30

RX_SAMPLE_RATE = 12000  # JS8_RX_SAMPLE_RATE
TX_SAMPLE_RATE = 48000  # Modulator.cpp FRAME_RATE


@dataclass(frozen=True)
class Submode:
    """One JS8 speed. Timings are derived exactly as JS8Submode.cpp derives them."""

    submode_id: int
    name: str
    letter: str  # the character used in ALL.TXT
    symbol_samples: int  # at RX_SAMPLE_RATE
    period_s: int
    start_delay_ms: int
    costas: tuple
    snr_threshold: float  # documented RX sensitivity, dB

    @property
    def tone_spacing(self):
        return RX_SAMPLE_RATE / self.symbol_samples

    @property
    def baud(self):
        # In JS8 the baud rate and the tone spacing are numerically equal.
        return self.tone_spacing

    @property
    def bandwidth(self):
        return 8 * self.tone_spacing

    @property
    def data_duration(self):
        return NN * self.symbol_samples / RX_SAMPLE_RATE

    @property
    def tx_duration(self):
        return self.data_duration + self.start_delay_ms / 1000.0


SUBMODE_NORMAL = 0
SUBMODE_FAST = 1
SUBMODE_TURBO = 2  # "JS8 40"
SUBMODE_SLOW = 4
SUBMODE_ULTRA = 8  # "JS8 60"

# Ids are a bitset the multi-decoder ORs together -- there is deliberately no 3.
SUBMODES = {
    SUBMODE_NORMAL: Submode(0, "Normal", "A", 1920, 15, 500, COSTAS_ORIGINAL, -24.0),
    SUBMODE_FAST: Submode(1, "Fast", "B", 1200, 10, 200, COSTAS_MODIFIED, -22.0),
    SUBMODE_TURBO: Submode(2, "JS8 40", "C", 600, 6, 100, COSTAS_MODIFIED, -20.0),
    SUBMODE_SLOW: Submode(4, "Slow", "E", 3840, 30, 500, COSTAS_MODIFIED, -28.0),
    SUBMODE_ULTRA: Submode(8, "JS8 60", "I", 384, 4, 100, COSTAS_MODIFIED, -18.0),
}

_GENERATOR_ROWS = None


def generator_rows():
    """G as 87 rows of 87 bits, decoded once from G_ROWS_HEX and cached.

    Each hex string is 22 nibbles = 88 bits, MSB first; the 88th bit is padding
    and is discarded.
    """
    global _GENERATOR_ROWS
    if _GENERATOR_ROWS is None:
        rows = []
        for h in G_ROWS_HEX:
            v = int(h, 16)
            rows.append([(v >> (87 - col)) & 1 for col in range(LDPC_K)])
        _GENERATOR_ROWS = rows
    return _GENERATOR_ROWS


# --- Literal tables from JS8_Mode/JS8.cpp (see module docstring) -------------

G_ROWS_HEX = (
    "23bba830e23b6b6f50982e",
    "1f8e55da218c5df3309052",
    "ca7b3217cd92bd59a5ae20",
    "56f78313537d0f4382964e",
    "6be396b5e2e819e373340c",
    "293548a138858328af4210",
    "cb6c6afcdc28bb3f7c6e86",
    "3f2a86f5c5bd225c961150",
    "849dd2d63673481860f62c",
    "56cdaec6e7ae14b43feeee",
    "04ef5cfa3766ba778f45a4",
    "c525ae4bd4f627320a3974",
    "41fd9520b2e4abeb2f989c",
    "7fb36c24085a34d8c1dbc4",
    "40fc3e44bb7d2bb2756e44",
    "d38ab0a1d2e52a8ec3bc76",
    "3d0f929ef3949bd84d4734",
    "45d3814f504064f80549ae",
    "f14dbf263825d0bd04b05e",
    "db714f8f64e8ac7af1a76e",
    "8d0274de71e7c1a8055eb0",
    "51f81573dd4049b082de14",
    "d8f937f31822e57c562370",
    "b6537f417e61d1a7085336",
    "ecbd7c73b9cd34c3720c8a",
    "3d188ea477f6fa41317a4e",
    "1ac4672b549cd6dba79bcc",
    "a377253773ea678367c3f6",
    "0dbd816fba1543f721dc72",
    "ca4186dd44c3121565cf5c",
    "29c29dba9c545e267762fe",
    "1616d78018d0b4745ca0f2",
    "fe37802941d66dde02b99c",
    "a9fa8e50bcb032c85e3304",
    "83f640f1a48a8ebc0443ea",
    "3776af54ccfbae916afde6",
    "a8fc906976c35669e79ce0",
    "f08a91fb2e1f78290619a8",
    "cc9da55fe046d0cb3a770c",
    "d36d662a69ae24b74dcbd8",
    "40907b01280f03c0323946",
    "d037db825175d851f3af00",
    "1bf1490607c54032660ede",
    "0af7723161ec223080be86",
    "eca9afa0f6b01d92305edc",
    "7a8dec79a51e8ac5388022",
    "9059dfa2bb20ef7ef73ad4",
    "6abb212d9739dfc02580f2",
    "f6ad4824b87c80ebfce466",
    "d747bfc5fd65ef70fbd9bc",
    "612f63acc025b6ab476f7c",
    "05209a0abb530b9e7e34b0",
    "45b7ab6242b77474d9f11a",
    "6c280d2a0523d9c4bc5946",
    "f1627701a2d692fd9449e6",
    "8d9071b7e7a6a2eed6965e",
    "bf4f56e073271f6ab4bf80",
    "c0fc3ec4fb7d2bb2756644",
    "57da6d13cb96a7689b2790",
    "a9fa2eefa6f8796a355772",
    "164cc861bdd803c547f2ac",
    "cc6de59755420925f90ed2",
    "a0c0033a52ab6299802fd2",
    "b274db8abd3c6f396ea356",
    "97d4169cb33e7435718d90",
    "81cfc6f18c35b1e1f17114",
    "481a2a0df8a23583f82d6c",
    "081c29a10d468ccdbcecb6",
    "2c4142bf42b01e71076acc",
    "a6573f3dc8b16c9d19f746",
    "c87af9a5d5206abca532a8",
    "012dee2198eba82b19a1da",
    "b1ca4ea2e3d173bad4379c",
    "b33ec97be83ce413f9acc8",
    "5b0f7742bca86b8012609a",
    "37d8e0af9258b9e8c5f9b2",
    "35ad3fb0faeb5f1b0c30dc",
    "6114e08483043fd3f38a8a",
    "cd921fdf59e882683763f6",
    "95e45ecd0135aca9d6e6ae",
    "2e547dd7a05f6597aac516",
    "14cd0f642fc0c5fe3a65ca",
    "3a0a1dfd7eee29c2e827e0",
    "c8b5dffc335095dcdcaf2a",
    "3dd01a59d86310743ec752",
    "8abdb889efbe39a510a118",
    "3f231f212055371cf3e2a2",
)

MN = (
    (0, 24, 68),
    (1, 4, 72),
    (2, 31, 67),
    (3, 50, 60),
    (5, 62, 69),
    (6, 32, 78),
    (7, 49, 85),
    (8, 36, 42),
    (9, 40, 64),
    (10, 13, 63),
    (11, 74, 76),
    (12, 22, 80),
    (14, 15, 81),
    (16, 55, 65),
    (17, 52, 59),
    (18, 30, 51),
    (19, 66, 83),
    (20, 28, 71),
    (21, 23, 43),
    (25, 34, 75),
    (26, 35, 37),
    (27, 39, 41),
    (29, 53, 54),
    (33, 48, 86),
    (38, 56, 57),
    (44, 73, 82),
    (45, 61, 79),
    (46, 47, 84),
    (58, 70, 77),
    (0, 49, 52),
    (1, 46, 83),
    (2, 24, 78),
    (3, 5, 13),
    (4, 6, 79),
    (7, 33, 54),
    (8, 35, 68),
    (9, 42, 82),
    (10, 22, 73),
    (11, 16, 43),
    (12, 56, 75),
    (14, 26, 55),
    (15, 27, 28),
    (17, 18, 58),
    (19, 39, 62),
    (20, 34, 51),
    (21, 53, 63),
    (23, 61, 77),
    (25, 31, 76),
    (29, 71, 84),
    (30, 64, 86),
    (32, 38, 50),
    (36, 47, 74),
    (37, 69, 70),
    (40, 41, 67),
    (44, 66, 85),
    (45, 80, 81),
    (48, 65, 72),
    (57, 59, 65),
    (60, 64, 84),
    (0, 13, 20),
    (1, 12, 58),
    (2, 66, 81),
    (3, 31, 72),
    (4, 35, 53),
    (5, 42, 45),
    (6, 27, 74),
    (7, 32, 70),
    (8, 48, 75),
    (9, 57, 63),
    (10, 47, 67),
    (11, 18, 44),
    (14, 49, 60),
    (15, 21, 25),
    (16, 71, 79),
    (17, 39, 54),
    (19, 34, 50),
    (22, 24, 33),
    (23, 62, 86),
    (26, 38, 73),
    (28, 77, 82),
    (29, 69, 76),
    (30, 68, 83),
    (21, 36, 85),
    (37, 40, 80),
    (41, 43, 56),
    (46, 52, 61),
    (51, 55, 78),
    (59, 74, 80),
    (0, 38, 76),
    (1, 15, 40),
    (2, 30, 53),
    (3, 35, 77),
    (4, 44, 64),
    (5, 56, 84),
    (6, 13, 48),
    (7, 20, 45),
    (8, 14, 71),
    (9, 19, 61),
    (10, 16, 70),
    (11, 33, 46),
    (12, 67, 85),
    (17, 22, 42),
    (18, 63, 72),
    (23, 47, 78),
    (24, 69, 82),
    (25, 79, 86),
    (26, 31, 39),
    (27, 55, 68),
    (28, 62, 65),
    (29, 41, 49),
    (32, 36, 81),
    (34, 59, 73),
    (37, 54, 83),
    (43, 51, 60),
    (50, 52, 71),
    (57, 58, 66),
    (46, 55, 75),
    (0, 18, 36),
    (1, 60, 74),
    (2, 7, 65),
    (3, 59, 83),
    (4, 33, 38),
    (5, 25, 52),
    (6, 31, 56),
    (8, 51, 66),
    (9, 11, 14),
    (10, 50, 68),
    (12, 13, 64),
    (15, 30, 42),
    (16, 19, 35),
    (17, 79, 85),
    (20, 47, 58),
    (21, 39, 45),
    (22, 32, 61),
    (23, 29, 73),
    (24, 41, 63),
    (26, 48, 84),
    (27, 37, 72),
    (28, 43, 80),
    (34, 67, 69),
    (40, 62, 75),
    (44, 48, 70),
    (49, 57, 86),
    (47, 53, 82),
    (12, 54, 78),
    (76, 77, 81),
    (0, 1, 23),
    (2, 5, 74),
    (3, 55, 86),
    (4, 43, 52),
    (6, 49, 82),
    (7, 9, 27),
    (8, 54, 61),
    (10, 28, 66),
    (11, 32, 39),
    (13, 15, 19),
    (14, 34, 72),
    (16, 30, 38),
    (17, 35, 56),
    (18, 45, 75),
    (20, 41, 83),
    (21, 33, 58),
    (22, 25, 60),
    (24, 59, 64),
    (26, 63, 79),
    (29, 36, 65),
    (31, 44, 71),
    (37, 50, 85),
    (40, 76, 78),
    (42, 55, 67),
    (46, 73, 81),
    (39, 51, 77),
    (53, 60, 70),
    (45, 57, 68),
)

NM = (
    (0, 29, 59, 88, 117, 146),
    (1, 30, 60, 89, 118, 146),
    (2, 31, 61, 90, 119, 147),
    (3, 32, 62, 91, 120, 148),
    (1, 33, 63, 92, 121, 149),
    (4, 32, 64, 93, 122, 147),
    (5, 33, 65, 94, 123, 150),
    (6, 34, 66, 95, 119, 151),
    (7, 35, 67, 96, 124, 152),
    (8, 36, 68, 97, 125, 151),
    (9, 37, 69, 98, 126, 153),
    (10, 38, 70, 99, 125, 154),
    (11, 39, 60, 100, 127, 144),
    (9, 32, 59, 94, 127, 155),
    (12, 40, 71, 96, 125, 156),
    (12, 41, 72, 89, 128, 155),
    (13, 38, 73, 98, 129, 157),
    (14, 42, 74, 101, 130, 158),
    (15, 42, 70, 102, 117, 159),
    (16, 43, 75, 97, 129, 155),
    (17, 44, 59, 95, 131, 160),
    (18, 45, 72, 82, 132, 161),
    (11, 37, 76, 101, 133, 162),
    (18, 46, 77, 103, 134, 146),
    (0, 31, 76, 104, 135, 163),
    (19, 47, 72, 105, 122, 162),
    (20, 40, 78, 106, 136, 164),
    (21, 41, 65, 107, 137, 151),
    (17, 41, 79, 108, 138, 153),
    (22, 48, 80, 109, 134, 165),
    (15, 49, 81, 90, 128, 157),
    (2, 47, 62, 106, 123, 166),
    (5, 50, 66, 110, 133, 154),
    (23, 34, 76, 99, 121, 161),
    (19, 44, 75, 111, 139, 156),
    (20, 35, 63, 91, 129, 158),
    (7, 51, 82, 110, 117, 165),
    (20, 52, 83, 112, 137, 167),
    (24, 50, 78, 88, 121, 157),
    (21, 43, 74, 106, 132, 154, 171),
    (8, 53, 83, 89, 140, 168),
    (21, 53, 84, 109, 135, 160),
    (7, 36, 64, 101, 128, 169),
    (18, 38, 84, 113, 138, 149),
    (25, 54, 70, 92, 141, 166),
    (26, 55, 64, 95, 132, 159, 173),
    (27, 30, 85, 99, 116, 170),
    (27, 51, 69, 103, 131, 143),
    (23, 56, 67, 94, 136, 141),
    (6, 29, 71, 109, 142, 150),
    (3, 50, 75, 114, 126, 167),
    (15, 44, 86, 113, 124, 171),
    (14, 29, 85, 114, 122, 149),
    (22, 45, 63, 90, 143, 172),
    (22, 34, 74, 112, 144, 152),
    (13, 40, 86, 107, 116, 148, 169),
    (24, 39, 84, 93, 123, 158),
    (24, 57, 68, 115, 142, 173),
    (28, 42, 60, 115, 131, 161),
    (14, 57, 87, 111, 120, 163),
    (3, 58, 71, 113, 118, 162, 172),
    (26, 46, 85, 97, 133, 152),
    (4, 43, 77, 108, 140),
    (9, 45, 68, 102, 135, 164),
    (8, 49, 58, 92, 127, 163),
    (13, 56, 57, 108, 119, 165),
    (16, 54, 61, 115, 124, 153),
    (2, 53, 69, 100, 139, 169),
    (0, 35, 81, 107, 126, 173),
    (4, 52, 80, 104, 139),
    (28, 52, 66, 98, 141, 172),
    (17, 48, 73, 96, 114, 166),
    (1, 56, 62, 102, 137, 156),
    (25, 37, 78, 111, 134, 170),
    (10, 51, 65, 87, 118, 147),
    (19, 39, 67, 116, 140, 159),
    (10, 47, 80, 88, 145, 168),
    (28, 46, 79, 91, 145, 171),
    (5, 31, 86, 103, 144, 168),
    (26, 33, 73, 105, 130, 164),
    (11, 55, 83, 87, 138),
    (12, 55, 61, 110, 145, 170),
    (25, 36, 79, 104, 143, 150),
    (16, 30, 81, 112, 120, 160),
    (27, 48, 58, 93, 136),
    (6, 54, 82, 100, 130, 167),
    (23, 49, 77, 105, 142, 148),
)
