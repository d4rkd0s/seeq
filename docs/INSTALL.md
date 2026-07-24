# INSTALL — get SeeQ on the air in under 30 minutes

This is the step-by-step path from a fresh Linux install to a first FT8
decode. No AI is involved at any point — everything below is plain packages,
`seeq setup`, and the WSJT-X command-line tools.

## 1. Install packages

### Debian / Ubuntu (apt)

```bash
sudo apt-get update
sudo apt-get install wsjtx sox pulseaudio-utils python3-numpy libhamlib-utils
```

### Fedora (dnf)

```bash
sudo dnf install wsjtx sox pulseaudio-utils python3-numpy hamlib
```

(Package names track upstream Hamlib/WSJT-X naming; if `hamlib` doesn't pull
in `rigctl`, try `hamlib-utils`.)

### Raspberry Pi OS

A Pi 4 (2 GB+) runs the whole stack fine — decoding a 15 s FT8 slot with
`jt9` and rendering the dashboard is well within its budget, making it the
natural **$0 shack computer**: no AI, no cloud, and no PC tied up next to the
radio.

```bash
sudo apt-get update
sudo apt-get install wsjtx sox pulseaudio-utils python3-numpy libhamlib-utils
```

Notes specific to the Pi:

- Raspberry Pi OS Lite (no desktop) works — the dashboard is a browser page
  served over HTTP; view it from any device on your LAN at
  `http://<pi-hostname>.local:8074`.
- Use a powered USB hub if your CAT adapter + audio interface + anything
  else pull more current than the Pi's USB ports alone provide — brownouts
  show up as intermittent CAT timeouts or audio dropouts, not clean errors.
- PulseAudio ships by default on Raspberry Pi OS; if you're on a PipeWire
  image, its `pipewire-pulse` shim provides the same `pactl`/`parecord`
  commands used here.

## 2. Clone and configure

```bash
git clone https://github.com/d4rkd0s/seeq.git
cd cota
bin/seeq setup
```

`seeq setup` is an interactive wizard. It only ever *detects* hardware
(`pactl list short sources`, `arecord -l`, `/dev/serial/by-id/`, `rigctl -l`)
— it never opens the CAT port or keys the radio unless you explicitly say
yes to its final, optional "test CAT now?" step. It will:

1. List USB audio capture devices and let you pick yours.
2. List serial ports under `/dev/serial/by-id/` (stable across reboots,
   unlike `/dev/ttyUSB0`) and let you pick your CAT adapter.
3. Let you search Hamlib's rig database (`rigctl -l`, 260+ rigs) by name —
   e.g. type `xiegu`, `icom`, `yaesu` — and pick your model number.
4. Ask your callsign, grid square (4 or 6 characters), band, and power —
   validating callsign format and Maidenhead locator format as you type.
5. Write `station.conf` from `station.conf.example`, preserving every
   comment, so nothing about *why* a value matters gets lost.
6. Optionally run one `rigctl f` frequency read to confirm CAT is working —
   only if you answer yes.

If you'd rather do it by hand: `cp station.conf.example station.conf` and
edit every value yourself (the comments explain each one).

## 3. Prove the decode chain — no radio required

```bash
bin/seeq selftest
```

This synthesizes a reference FT8 frame entirely offline (`ft8code` +
`tools/ft8synth.py`'s numpy GFSK modulator), decodes it with `jt9`, and
checks the message round-trips. It touches no audio device, no CAT port, and
no antenna — if this fails, the problem is in your WSJT-X install, not your
station wiring.

## 4. Full preflight

```bash
bin/seeq doctor
```

Non-interactive diagnostics: NTP sync, required tools on `PATH`, Python/numpy,
`station.conf` sanity, CAT port existence, audio source presence, and disk
space — each printed as `OK`/`WARN`/`FAIL` with a one-line fix. `doctor`
never opens the CAT port or touches audio; it only checks that paths and
devices exist. Exits nonzero if anything hard-fails.

## 5. Go receive-only

```bash
bin/seeq start          # preflight + RX loop + dashboard at http://localhost:8074
bin/seeq status         # one-screen station status
bin/seeq report         # compact session report: QSOs, attempts, per-band
bin/seeq stop           # stop everything, force PTT release
```

`seeq start` alone never transmits — it decodes, plots the waterfall, and
suggests targets. When you're ready to actually work stations, see the
Quick start and safety model in [README.md](../README.md) before running
`seeq chase`.
