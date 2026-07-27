# JS8 as a native SeeQ mode — own protocol, own modem, own UI

## Context

The JS8 mode currently on `master` (v-unreleased, commits `8700cd9`…`21c3c19`) drives
**JS8Call-improved** — a third-party Qt GUI — over its TCP API. Logan's call is that this
is the wrong shape: JS8 should be a **full mode built into SeeQ**, the way FT8 is —
SeeQ's own protocol implementation, own tone generation, own decoder, own frontend. Not
a remote control for someone else's application.

The wrapper is not wasted. JS8Call stays temporarily as a **cross-check oracle**: it is
the only known-good implementation available, and its API happens to expose the exact
79-tone channel-symbol sequence for any message. It gets used to validate every stage of
our own modem, and then it gets deleted. **Removing JS8Call entirely is the
definition-of-done for this work** — JS8 mode is not "finished" while it is still
installed.

This is a long project. The encoder is a weekend; the decoder is a real DSP effort.

## What the source actually says (verified, not assumed)

Extracted from `JS8Call-improved/JS8Call-improved@master` — `JS8_Mode/JS8.cpp` (the
modem, 2914 lines), `JS8_Mode/JS8Submode.cpp`, `JS8_Include/commons.h`,
`JS8_Main/Varicode.cpp` (2372 lines), `JS8_Mode/Modulator.cpp`.

**Frame — identical geometry to FT8.** 79 channel symbols, 8-FSK.
```
idx  0..6      7..35          36..42     43..71          72..78
     Costas[0] 29 PARITY sym  Costas[1]  29 MESSAGE sym  Costas[2]
```
58 data symbols + 21 sync. **Parity is transmitted before the message.** No Gray code,
no interleaver, no scrambler — coded bits map straight to symbols, MSB first, 3 bits per
tone. (Confirmed: `grep -i gray` finds nothing in the codebase.)

**Information block — 87 bits:** `[0..71] 12 chars × 6 bits` · `[72..74] type` ·
`[75..86] CRC-12`. **CRC-12** is augmented, poly `0xC06`, computed over all 11 bytes with
the CRC field zeroed, then **XOR 42**. **LDPC(174,87)**, generator = 87 hex rows literal
in `JS8.cpp`; `p_i = XOR_j(G[i][j] & m_j)`; codeword = `[parity | message]`.

**Submodes** (`samplesForOneSymbol` @12 kHz → baud = tone spacing):

| | A/Normal | B/Fast | C/JS8 40 | E/Slow | I/JS8 60 |
|---|---|---|---|---|---|
| id (bitset) | 0 | 1 | 2 | 4 | 8 |
| baud = spacing (Hz) | 6.25 | 10.0 | 20.0 | 3.125 | 31.25 |
| data duration (s) | 12.64 | 7.90 | 3.95 | 25.28 | 2.528 |
| period (s) | 15 | 10 | 6 | 30 | 4 |
| Costas | **ORIGINAL** | MODIFIED | MODIFIED | MODIFIED | MODIFIED |

`ORIGINAL` = `{4,2,5,6,1,3,0}`×3 — **the same array FT8 uses**. `MODIFIED` =
`{0,6,2,3,5,4,1},{1,5,0,2,3,6,4},{2,5,0,6,4,1,3}`.

**Modulator is plain phase-continuous 8-FSK at 48 kHz — not GFSK.** `Modulator.cpp`
advances `phi` by `TAU*(f_audio + tone*spacing)/48000` with no Gaussian pulse shaping,
and applies only an exponential fade (`amp *= 0.98`/sample) over the final 0.017 symbol.
This is a real difference from `tools/ft8synth.py`, which does Gaussian BT=2.0 GFSK —
its WAV/ramp/offset scaffolding is reusable, its pulse shaping is not.

**Varicode is the application layer.** 64-char payload alphabet
`0-9 A-Z a-z - +`; frame types (Heartbeat/Compound/CompoundDirected/Directed/Data/
DataCompressed) live in the *payload's* leading bits — **not** in the 3 type bits, which
are transmission flags (`First=1, Last=2, Data=4`). Packers: `packCallsign` 28 bits,
`packAlphaNumeric50` 50 bits, `packGrid` 15 bits, `packCmd`, plus a literal 44-symbol
Huffman table and a 262,144-entry JSC dictionary (~14 MB of generated tables — deferred).

**The oracle.** `TX.FRAME` push events carry the full 79-tone array. With the rig set to
`None` (no CAT, no PTT), JS8Call will encode any message and hand back its exact channel
symbols **without transmitting**. Verified against the API docs: 79 entries, values 0–7,
Costas at 0/36/72.

## Architecture

A new native stack under `bin/modes/js8/`, replacing the wrapper file by file. Pure
stdlib + numpy (already a dependency); no new runtime deps.

| File | Role |
|---|---|
| `spec.py` | All constants: submode table, Costas arrays, alphabet, LDPC `G`, sparse `Mn`/`Nm`, CRC params. Generated-once tables live here, quoted from source. |
| `crc12.py` | Augmented CRC-12/0xC06 ^42, encode + verify. |
| `ldpc.py` | Parity generation (encode) and sum-product BP decode (30 iters, `Mn`/`Nm`). |
| `frame.py` | Port of `JS8::encode`: 12 chars + type → 87 bits → CRC → LDPC → 79 tones. Inverse for RX. |
| `varicode.py` | Text ↔ 72-bit payload: frame types, callsign/grid/cmd packers, Huffman. |
| `synth.py` | 79 tones → 48 kHz phase-continuous 8-FSK WAV, per-submode timing/lead-in. |
| `demod.py` | DSP: Nuttall-window FFT sync spectra, baseline fit, Costas candidate search, downsample, fine freq/time sync, per-symbol FFT → soft symbols. |
| `decode.py` | Soft symbols → whitened LLRs → BP → CRC-12 → payload → text. |
| `protocol.py` | Directed-call grammar, heartbeat, ACK/relay/MSG state machine. |
| `pipeline.py` / `engine.py` | Rewritten against the native stack; same five-function / TX contract `mode_switch.py` already expects. |
| `panel.py` | Native JS8 dashboard widgets (the existing six, rewired to our own data). |

**Reuse rather than rebuild:** `tools/ft8synth.py` (WAV writing, lead-in, offset, ramp
conventions), `bin/rx-loop.sh`'s slot-aligned capture loop as the model for JS8's RX
timing, `dashboard._spawn_detached`/`_pkill`/`_proc_running`, `bin/band_plan.py` for the
privilege gate, and the existing `data-mode=js8` widget chrome and `mode_switch.py`
changeover — all of which already work.

**Cross-check harness** — `tools/js8_reference.py`, built on the existing
`bin/modes/js8/api.py`: launches JS8Call with rig `None`, sends a message, captures the
`TX.FRAME` tones, writes `tests/fixtures/js8/vectors.jsonl`. Fixtures are committed, so
the corpus outlives the app.

## Phases, each with a gate that must pass before the next

**P0 — Spec document.** `docs/js8/PROTOCOL.md`: everything in "What the source actually
says" above, expanded, with the constant tables and the exact encode algorithm. This is
the artifact that lets JS8Call be deleted. Also documents the two decoder quirks worth
knowing: `syncjs8` sums 7 of 8 tones then divides by 6, and `js8dec` uses a shadowed
file-scope `NP2 = 2812` instead of the per-mode value (reads past the valid downsampled
region for every submode).

**P1 — Encoder core.** `spec.py`, `crc12.py`, `ldpc.py`, `frame.py`.
*Gate:* our 79 tones are **bit-exact** against JS8Call's `TX.FRAME` for a corpus of ≥200
messages spanning every frame type and both Costas sets.

**P2 — Varicode.** Callsign/grid/cmd packers, frame types, Huffman free text.
*Gate:* round-trip in Python, **and** JS8Call decodes text we packed, **and** we unpack
text JS8Call packed. JSC compression deferred (Huffman covers free text).

**P3 — Synthesis.** `synth.py` → WAV.
*Gate:* JS8Call decodes our WAV and recovers the exact message, at full scale and with
added noise. This is the loopback proof, entirely offline — no RF, no licence exposure.

**P4 — Decoder.** `demod.py`, `decode.py`. The large phase.
*Gate:* we decode JS8Call-generated WAVs across an SNR sweep, and report sensitivity
honestly against the −24 dB spec figure for Normal. Falling short of JS8Call's
sensitivity is acceptable and expected; misreporting it is not.

**P5 — Protocol + RX loop.** `protocol.py`, slot-aligned capture, decode storage.
*Gate:* a live receive session decodes real on-air JS8 traffic, cross-checked against
JS8Call listening to the same audio.

**P6 — Native UI.** `panel.py`; rewire the six JS8 widgets to our own decode/state data.
*Gate:* Logan's dashboard walkthrough, RX-only.

**P7 — Retire the oracle.** Delete the AppImage, `vendor.py`, `api.py`, the wrapper
`pipeline.py`/`engine.py`/`watchdog.py`/`rx_capture.py`, the CI fallback job, and the
`docs/js8/JS8Call_User_Guide.pdf` if superseded. Drop `JS8_TCP_PORT` from
`station.conf.example`.
*Gate:* full suite green with JS8Call absent from the machine, and `seeq doctor` clean.
**This is the definition of done.**

**P8 — First on-air TX.** Logan's call, per his standard: bit-exact symbols *and*
loopback decode, then judgement. The frozen TX-safety chain applies — but note it gets
*stronger* here than in the wrapper: once SeeQ owns the modem, `rigctl`-based PTT and the
detached `sleep N; rigctl T 0` watchdog work exactly as they do for FT8, so JS8 regains
the same hardware-independent unkey guarantee the JS8Call design could not offer. The
honest-limitation notice on the panel comes out at that point.

## Verification

- **TDD throughout**, per `CLAUDE.md`: red test first, wired into `Makefile`'s `test`
  target. Pure functions (CRC, LDPC, packers, symbol mapping) are exhaustively testable
  offline with zero hardware.
- **Fixture-driven**: `tests/fixtures/js8/vectors.jsonl` makes every encoder assertion a
  bit-exact comparison against a known-good implementation, permanently.
- **Loopback before RF**: P3's gate proves the waveform is correct in software before
  anything reaches an antenna.
- **Nothing transmits** until P8, and P8 is a separate, explicitly-gated session.
- Existing suites (`test_mode_registry`, `test_mode_switch`, `test_dashboard_*`) must
  stay green the whole way — the mode registry, changeover and widget chrome are
  unchanged by this work.

## Out of scope for now

- JSC dictionary compression (262k entries) — Huffman covers free text; revisit if
  on-air throughput needs it.
- Submodes B/C/E/I decode — get Normal (A) right first; the others differ only in
  timing constants and the Costas set, both already tabled.
- The fork's own enhancements (`ldpc_feedback.h`, `soft_combiner.h`) — not wire
  protocol, and not needed for interoperability.
- Rewriting FT8 mode. `qso.py`, `rx-loop.sh`, `parse_decodes.py` stay frozen.
