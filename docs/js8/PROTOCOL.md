# JS8 wire protocol — specification

**Purpose.** This is the reference SeeQ's native JS8 implementation is built from. It
exists so that JS8Call-improved can eventually be deleted from this machine without
losing the ability to build, fix or verify JS8 mode. If anything here disagrees with
running code, the code is wrong.

**Source of truth.** Extracted 2026-07-26 from
[`JS8Call-improved/JS8Call-improved`](https://github.com/JS8Call-improved/JS8Call-improved)
@ `master` — read directly from source, not from the user guide or from upstream
JS8Call, both of which are wrong or vague in places (see *Documentation errata*).

| Concern | File in that repo |
|---|---|
| Waveform `#define`s | `JS8_Include/commons.h` |
| Submode table | `JS8_Mode/JS8Submode.cpp`, `.h` |
| Modem (encode **and** decode) | `JS8_Mode/JS8.cpp` (~2914 lines), `JS8_Mode/JS8.h` |
| LLR noise normalisation | `JS8_Mode/whitening_processor.h` |
| TX modulation | `JS8_Mode/Modulator.cpp` |
| Text codec / frame packing | `JS8_Main/Varicode.cpp` (~2372 lines), `.h` |
| Word-dictionary compressor | `JS8_JSC/JSC.cpp`, `JSC_list.cpp`, `JSC_map.cpp` |
| RX frame → text | `JS8_Mode/DecodedText.cpp` |

`JS8_Mode/Decoder.cpp` is **dead code** — it contains the line *"This source file does
not presently take part in the build."* The live decoder is `JS8::Decoder` / `JS8::Worker`
/ `DecodeMode<Mode>` inside `JS8.cpp`. `JS8_Main/Message.cpp` is the JSON IPC class, not
RF frames.

---

## 1. Frame geometry

79 channel symbols, 8-FSK. **Identical layout to FT8.**

```
index  0..6       7..35            36..42     43..71            72..78
       Costas[0]  29 PARITY syms   Costas[1]  29 MESSAGE syms   Costas[2]
```

- 58 data symbols + 21 sync symbols = 79 (`ND=58`, `NS=21`, `NN=79` in `JS8.cpp`)
- **Parity is transmitted before the message.** Codeword order is `[parity | message]`.
- 3 bits per symbol, **MSB first**, straight binary.
- **No Gray code, no interleaver, no scrambler.** Coded bits map to symbols in order.
  (Verified: `gray` appears nowhere in the codebase outside palette filenames. This is a
  real difference from FT8, which *does* Gray-code.)

### Costas arrays (`JS8.h`)

```
ORIGINAL  {4,2,5,6,1,3,0}, {4,2,5,6,1,3,0}, {4,2,5,6,1,3,0}   <- same as FT8
MODIFIED  {0,6,2,3,5,4,1}, {1,5,0,2,3,6,4}, {2,5,0,6,4,1,3}
```

Submode **A/Normal uses ORIGINAL**; every other submode uses MODIFIED. Source comment:
*"JS8 originally used the same Costas arrays as FT8 did, and so that's still the array in
use by 'normal' mode."*

---

## 2. Submodes

Raw constants (`JS8_Include/commons.h`):

```c
#define JS8_NSPS           6192
#define JS8_RX_SAMPLE_RATE 12000
#define JS8_NUM_SYMBOLS    79
JS8A_SYMBOL_SAMPLES 1920 ; JS8A_TX_SECONDS 15 ; JS8A_START_DELAY_MS 500
JS8B_SYMBOL_SAMPLES 1200 ; JS8B_TX_SECONDS 10 ; JS8B_START_DELAY_MS 200
JS8C_SYMBOL_SAMPLES  600 ; JS8C_TX_SECONDS  6 ; JS8C_START_DELAY_MS 100
JS8E_SYMBOL_SAMPLES 3840 ; JS8E_TX_SECONDS 30 ; JS8E_START_DELAY_MS 500
JS8I_SYMBOL_SAMPLES  384 ; JS8I_TX_SECONDS  4 ; JS8I_START_DELAY_MS 100
```

`JS8x_TX_SECONDS` is the **period**, not the transmit duration. Derived in `JS8Submode.cpp`:
`toneSpacing = 12000 / symbolSamples`, `bandwidth = 8 × toneSpacing`,
`dataDuration = 79 × symbolSamples / 12000`, `txDuration = dataDuration + startDelay`.

| | **A / NORMAL** | **B / FAST** | **C / JS8 40** | **E / SLOW** | **I / JS8 60** |
|---|---|---|---|---|---|
| submode id (bitset) | **0** | **1** | **2** | **4** | **8** |
| ALL.TXT char | A | B | C | E | I |
| symbol samples @12 kHz | 1920 | 1200 | 600 | 3840 | 384 |
| baud = tone spacing (Hz) | 6.25 | 10.0 | 20.0 | 3.125 | 31.25 |
| bandwidth (Hz) | 50 | 80 | 160 | 25 | 250 |
| data duration (s) | 12.64 | 7.90 | 3.95 | 25.28 | 2.528 |
| start delay (ms) | 500 | 200 | 100 | 500 | 100 |
| TX duration (s) | 13.14 | 8.10 | 4.05 | 25.78 | 2.628 |
| period (s) | 15 | 10 | 6 | 30 | 4 |
| Costas set | ORIGINAL | MODIFIED | MODIFIED | MODIFIED | MODIFIED |
| RX SNR threshold (dB) | −24 | −22 | −20 | −28 | −18 |

Submode ids are a **bitset**; the multi-decoder ORs them and runs modes in the order
**I, E, C, B, A**. Payload rate = 72 bits/period → 4.8 bps (A), 7.2 (B), 12 (C), 2.4 (E),
18 (I).

---

## 3. The 87-bit information block

```
bits  0..71   12 characters × 6 bits   (payload — see §6)
bits 72..74   3-bit transmission type  (i3bit)
bits 75..86   12-bit CRC
bit  87       unused, always 0         (the block is carried in 11 bytes)
```

### Payload alphabet (64 chars, `JS8.cpp`)

```
0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-+
```
`'0'→0, 'A'→10, 'a'→36, '-'→62, '+'→63`. Packed 4 chars at a time into 3 bytes:
`w = (c0<<18)|(c1<<12)|(c2<<6)|c3`.

### Transmission type (`i3bit`) — a **flag set**, not the frame type

```
JS8Call      = 0  [000]  any other frame of a message
JS8CallFirst = 1  [001]  first frame of a message
JS8CallLast  = 2  [010]  last frame of a message
JS8CallData  = 4  [100]  flagged/"fast data" frame (no frame-type header)
```
All values 0..7 occur. **The frame type is a separate field inside the payload** (§6.2)
— conflating the two is the classic mistake here.

### CRC-12

Augmented CRC, width 12, truncated polynomial **`0xC06`**, init 0, no reflection,
**final XOR 42**. Computed over **all 11 bytes with the CRC field zeroed** (75 real bits
+ 13 zero bits).

```python
def crc12(data11):                    # bytes-like, len 11, CRC field zeroed
    rem = 0
    for byte in data11:               # MSB first
        for i in range(7, -1, -1):
            top = (rem >> 11) & 1
            rem = ((rem << 1) | ((byte >> i) & 1)) & 0xFFF
            if top:
                rem ^= 0xC06
    return rem ^ 42
```

Insertion: `bytes[9] |= (crc >> 7) & 0x1F` ; `bytes[10] = (crc & 0x7F) << 1`.
Verification: recover `crc = ((b[9] & 0x1F) << 7) | (b[10] >> 1)`, then zero
(`b[9] &= 0xE0; b[10] = 0`) and recompute.

Boost's `crc.hpp` is used *only* here. `vendor/CRCpp` is used only by Varicode for
application-layer checksums (CRC-16/KERMIT, CRC-32/BZIP2), never for FEC.

---

## 4. FEC — LDPC(174, 87)

`N=174`, `K=87`, `M=87`. Rate 1/2, regular degree 3 on the variable side.

**Generator:** an 87×87 dense matrix, literal in `JS8.cpp` (~lines 963–1050) as 87 hex
strings of 22 hex chars (88 bits, last discarded). Bit `(row, col)` = bit `col` of row
`row`, reading nibbles MSB-first. First/last rows:
`23bba830e23b6b6f50982e` … `3f231f212055371cf3e2a2`.

**Parity:** `p_i = XOR_j ( G[i][j] AND m_j )` for `i = 0..86`.
**Codeword:** `[p_0..p_86 | m_0..m_86]` = 174 bits.

**Decode-side sparse tables**, also literal in `JS8.cpp` (~lines 597–726):
`Mn[174][3]` — the 3 checks each bit belongs to; `Nm[87]` — 5–7 bit indices per check.
`BP_MAX_ITERATIONS = 30`.

---

## 5. Modulation

**Plain phase-continuous 8-FSK at 48 kHz. Not GFSK** — this differs from FT8, and from
`tools/ft8synth.py`, which applies Gaussian BT=2.0 shaping.

```cpp
// Modulator.cpp::readData, FRAME_RATE = 48000
isym = ic / (4.0 * nsps);                        // 4x: 48 kHz vs 12 kHz
toneFrequency = audioFrequency + itone[isym] * toneSpacing;
dphi = TAU * toneFrequency / 48000;
phi += dphi;  if (phi > TAU) phi -= TAU;
if (ic > i0) amp = 0.98 * amp;                   // exponential fade, last 0.017 symbol
sample = round(amp * sin(phi));                  // amp starts at INT16_MAX
```
`i0 = (79 − 0.017) × 4 × nsps`. Leading silence = `startDelayMS × 48` frames.

---

## 6. Varicode — the application layer

### 6.1 Payload = 72 bits, always

Padding rule for data frames: `pad = 72 − len(bits)`, append **one `0` then all `1`s**.
Unpad by finding the **last** `0`.

### 6.2 Frame types — in the payload's leading bits

```
FrameHeartbeat        = 0  [000]
FrameCompound         = 1  [001]
FrameCompoundDirected = 2  [010]
FrameDirected         = 3  [011]
FrameData             = 4  [10X]   (only the 2 MSBs are encoded)
FrameDataCompressed   = 6  [11X]
FrameUnknown          = 255
```
RX dispatch order: `tryUnpackFastData → tryUnpackData → tryUnpackHeartbeat →
tryUnpackCompound → tryUnpackDirected`, gated on `i3bit & JS8CallData`.

### 6.3 Bit layouts (each exactly 72 bits)

**Compound / Heartbeat / CompoundDirected** (`packCompoundFrame`):
```
[3 type][50 callsign][11 num>>5] [5 num&0x1F][3 bits3]
```
- Heartbeat: `num` = packed grid, bit 15 set ⇒ "alt" (CQ) rather than HB; `bits3` = CQ or HB status index.
- Compound: `num` = `packGrid(grid)`, else `nmaxgrid`.
- CompoundDirected: `num = nusergrid + packCmd(cmd, num)`.

**Directed** (`packDirectedMessage`):
```
[3 flag=3][28 from][28 to][5 cmd%32] [1 portable_from][1 portable_to][6 num]
```
(A comment above `unpackDirectedMessage` claiming `[3][28][22][11]` is **stale**; the
code uses 3/28/28/5.)

**Legacy data frame** (NORMAL only, deprecated since 2.2): `[1 flag=1][1 compressed][70 payload]`.
Builds both a Huffman version (prefix `10`) and a JSC version (prefix `11`), keeps
whichever consumes more input.

**Fast data frame** (all non-NORMAL submodes): **no prefix** — all 72 bits are JSC
payload; the `JS8CallData` bit in `i3bit` is what marks it. Huffman is compiled out of
this path (`JS8_FAST_DATA_CAN_USE_HUFF 0`).

### 6.4 Field packers

| Function | Layout |
|---|---|
| `packCallsign` → 28 b | `p=idx(c0); p=36p+idx(c1); p=10p+idx(c2); p=27p+idx(c3)−10; ×2 more`. Alphabet `0-9 A-Z ' ' / @` (39). Swaziland `3DA0…`→`3D0…`, Guinea `3X[A-Z]`→`Q…`; `/P` stripped to a portable flag. |
| `packAlphaNumeric50` → 50 b | 11 chars, mixed radix `39·38·38·2·38·38·38·2·38·38·38`; slots 3 and 7 are 1-bit `/` flags |
| `packAlphaNumeric22` → 22 b | 4 chars base-38, `<<1` plus a flag |
| `packGrid` → 15 b | `((ilong+180)/2)*180 + ilat` |
| `packNum` → 6 b | `clamp(n,−30,31) + 31` |
| `packCmd` | SNR cmds `[1][isHeartbeatSNR][6-bit num]`; else `cmd & 0x7F` |

```
nbasecall = 37*36*10*27*27*27 = 262177560
nbasegrid = 180*180 = 32400 ; nusergrid = 32410 ; nmaxgrid = 32767
```
`basecalls` maps 54 group names (`@ALLCALL`, `@JS8NET`, `@DX/NA`, …) to
`nbasecall + 1..54`. `cqs = {0:"CQ CQ CQ", 1:"CQ DX", 2:"CQ QRP", 3:"CQ CONTEST",
4:"CQ FIELD", 5:"CQ FD", 6:"CQ CQ", 7:"CQ"}`. `directed_cmds` maps ~40 strings →
ints (`" SNR?"`→0, `">"`→5 relay, `" ACK"`→14, `" GRID"`→15, `" 73"`→28,
`" HEARTBEAT SNR"`→29, `" AGN?"`→30, `" "`→31 free text);
`buffered_cmds = {5,9,10,11,12,13,15,24}`, `snr_cmds = {25,29}`.

### 6.5 Huffman table (44 symbols, literal in `Varicode.cpp`)

Greedy longest-key-first.

```
" " 01        "E" 100       "T" 1101      "A" 0011      "O" 11111
"I" 11100     "N" 10111     "S" 10100     "H" 00011     "R" 00000
"D" 111011    "L" 110011    "C" 110001    "U" 101101    "M" 101011
"W" 001011    "F" 001001    "G" 000101    "Y" 000011
"P" 1111011   "B" 1111001   "." 1110100   "V" 1100101   "K" 1100100
"-" 1100001   "+" 1100000   "?" 1011001   "!" 1011000   "\"" 1010101
"X" 1010100   "0" 0010101   "J" 0010100   "1" 0010001   "Q" 0010000
"2" 0001001   "Z" 0001000   "3" 0000101   "5" 0000100
"4" 11110101  "9" 11110100  "8" 11110001  "6" 11110000
"7" 11101011  "/" 11101010
```
`ESC = '\'`, `EOT = '\x04'`.

### 6.6 JSC word compression (deferred in SeeQ)

`(s,c)`-dense code, `b=4, s=7, c=9`, dictionary of **262144** entries in
`JSC_list.cpp`/`JSC_map.cpp` (~7 MB each — bulk-extract programmatically if ever needed).
Nibbles ≥ 7 are continuation digits; a nibble < 7 terminates a word and is followed by
one separator bit. `base[0]=0; base[k]=base[k−1]+s·c^(k−1)`.

---

## 7. `JS8::encode` — the exact algorithm

`void encode(int type, Costas::Array const &costas, const char *message, int *tones)`
— `message` is exactly 12 chars from the §3 alphabet, `tones` is `int[79]`.

1. `bytes = [0]*11`
2. Pack 12 chars → bytes 0..8 (4 chars → 3 bytes, three times)
3. `bytes[9] = (type & 0b111) << 5`
4. `crc = augmented_crc12(bytes) ^ 42`  ← computed with the CRC field still zero
5. `bytes[9] |= (crc >> 7) & 0x1F` ; `bytes[10] = (crc & 0x7F) << 1`
6. Copy Costas blocks to `tones[0]`, `tones[36]`, `tones[72]`
7. For `i = 0..86`: accumulate `parity_i`, and every 3 bits emit one parity symbol into
   `tones[7..35]` and one message symbol into `tones[43..71]`

```python
def js8_encode(i3bit, costas, msg12):
    b = bytearray(11)
    for i, j in ((0, 0), (4, 3), (8, 6)):
        w = (A[msg12[i]] << 18) | (A[msg12[i+1]] << 12) | (A[msg12[i+2]] << 6) | A[msg12[i+3]]
        b[j], b[j+1], b[j+2] = (w >> 16) & 0xFF, (w >> 8) & 0xFF, w & 0xFF
    b[9] = (i3bit & 7) << 5
    crc = crc12(bytes(b))
    b[9] |= (crc >> 7) & 0x1F
    b[10] = (crc & 0x7F) << 1
    m = [(b[k // 8] >> (7 - (k % 8))) & 1 for k in range(87)]
    p = [sum(G[i][j] & m[j] for j in range(87)) & 1 for i in range(87)]
    t = [0] * 79
    for blk in range(3):
        t[blk * 36 : blk * 36 + 7] = costas[blk]
    for s in range(29):
        t[7 + s]  = (p[3*s] << 2) | (p[3*s+1] << 1) | p[3*s+2]
        t[43 + s] = (m[3*s] << 2) | (m[3*s+1] << 1) | m[3*s+2]
    return t
```

---

## 8. Decoder pipeline

Per-mode constants (`JS8.cpp`, `ModeA..ModeI`) — A shown, others differ in these values:
`NSPS 1920`, `NDOWNSPS 32`, `NDD 100`, `JZ 62`, `ASTART 0.5`, `BASESUB 40.0`, `AZ 4.0`,
`NFFT1 = NSPS·2 = 3840`, `NSTEP = NSPS/4 = 480`, `NHSYM = NMAX/NSTEP−3 = 372`,
`NDOWN = 60`, `DF = 12000/NFFT1 = 3.125`, `FS2 = 12000/NDOWN = 200`.

1. **`syncjs8` — candidate search.** Nuttall-windowed (`a0=0.3635819, a1=−0.4891775,
   a2=0.1365995, a3=−0.0106411`, Kahan-summed) real FFTs of size `NFFT1` every `NSTEP`
   samples → power spectra. Baseline: degree-5 polynomial through Chebyshev nodes at the
   **10th percentile** of `savg` over 500–2500 Hz. Costas correlation over bins
   `[ia,ib]` × offsets `[−JZ, JZ]`; `sync = max(f(0,2), f(0,1), f(1,2))` where
   `f(a,b) = Σ on-tone / ((Σ total − Σ on-tone) / 6)` — so a half-overlapping
   transmission still syncs. Normalise by the 40th percentile. Take candidates greedily,
   stop below `ASYNCMIN = 1.5`, cap `NMAXCAND = 300`, suppress within `±AZ` Hz.
2. **Pass loop** — up to 3 passes; passes 1–2 subtract each decoded signal from the
   waveform before re-searching.
3. **`js8_downsample`** — extract `[f0 − 1.5·baud, f0 + 8.5·baud]`, raised-cosine taper
   of length `NDD+1`, rotate to DC, IFFT of size `NDFFT2` → complex baseband at `FS2`.
4. **`js8dec`** — coarse DT search ±`NQSYMBOL`; fine frequency over `±2.5 Hz in 0.5 Hz
   steps` (`NFSRCH = 5`) by coherent Costas correlation; per-symbol `NDOWNSPS`-point
   complex FFT → `s2[8][79]`. Reject if fewer than 7 of the 21 Costas positions match.
5. **Soft symbols → LLRs** (`whitening_processor.h`). Per data symbol `j`, with tone
   magnitudes `ps[0..7]`, straight binary (no Gray):
   ```
   llr[3j+0] = max(ps[4..7]) − max(ps[0..3])     # MSB
   llr[3j+1] = max(ps[2,3,6,7]) − max(ps[0,1,4,5])
   llr[3j+2] = max(ps[1,3,5,7]) − max(ps[0,2,4,6])   # LSB
   ```
   Then divide by `sqrt(toneNoise · symbolNoise)` (medians of non-winning tones), zero
   below an erasure threshold of 0.25, normalise to σ = 2.83. **LLR indices 0..86 are
   parity, 87..173 are message.**
6. **BP decode** — floating-point sum-product (`tanh`/`atanh`), ≤30 iterations, early
   abort if the failing-check count stalls. OSD is **removed** in this fork.
7. **Accept** if hard errors < 60 (tighter on later passes) **and CRC-12 passes**.
8. **SNR** — re-encode the decode, then
   `xsnr = max(10·log10(max(xsig/xbase − 1, 1.259e-10)) − 32.0, −60.0)`.

---

## Documentation errata (the fork's own docs are wrong)

1. **API port.** `docs/API.md` says the API is on **2242** and its `telnet` example uses
   it. `JS8_UI/Configuration.cpp` shows 2242 is the **UDP** default (`UDPServerPort`);
   **TCP is 2442** (`TCPServerPort`). Both listeners default to **disabled**
   (`AcceptTCPRequests`/`AcceptUDPRequests` = false).
2. **Settings path.** `MultiSettings.cpp::settings_path()` uses Qt's `ConfigLocation`
   (not `AppConfigLocation`), so the ini is `~/.config/"JS8Call[ - <rig-name>].ini"`,
   directly in `~/.config`, keys under `[General]`.
3. **Directed-frame comment** claims `[3][28][22][11]`; the code is `[3][28][28][5]`.

## Implementation gotchas

1. **`NP2` is shadowed.** File-scope `NP2 = 2812` vs per-mode `Mode::NP2 = 79·NDOWNSPS`.
   `syncjs8d` uses the per-mode value; `js8dec` uses the bare 2812 in three places,
   reading past the valid downsampled region for every submode. Decide deliberately
   whether to reproduce this.
2. **`syncjs8` sums 7 of 8 tones** (`freq = 0..6`) and then divides by 6. Inherited from
   the original Fortran; keep it for bit-compatibility.
3. `i3bit` is *not* the frame type (§3, §6.2).
4. Everything is float32 in the C++; the Nuttall window is Kahan-summed specifically to
   match gfortran bit-for-bit.
5. `ldpc_feedback.h` and `soft_combiner.h` are **this fork's own enhancements**, not part
   of the wire protocol — not needed for interoperability.

## Cross-checking against the reference

While JS8Call-improved is still installed, its TCP API is a ground-truth oracle: a
`TX.FRAME` push event carries the complete 79-entry tone array for whatever it is about
to send. With the rig set to **None** (no CAT, no PTT) it will encode and report tones
**without transmitting anything**. That makes every stage of §7 verifiable bit-exactly,
offline. Capture fixtures before the app is removed — see `docs/js8/NATIVE-PLAN.md`.
