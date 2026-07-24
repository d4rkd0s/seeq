---
name: RFI war story
about: Report radio interference (RFI), noise issues, or isolation challenges
title: "[RFI] "
labels: rfi
assignees: ''

---

## Describe the RFI / noise issue

What did you observe? (e.g., receiver desense, decoder misses, TX arcing, USB errors, audio distortion, etc.)

## When it occurred

- Date/time (UTC or local): 
- Band/frequency: 
- TX or RX affected: 
- Does it happen always, or intermittent?

## Station setup

**Rig:**
- Model: [e.g., Xiegu G90]
- TX power set to: [e.g., 5 W]

**Interface:**
- Model: [e.g., DE-19, Digirig, SignaLink]
- Audio cable shielding: [e.g., Belden 8723, stock, unknown]
- USB cable length: [e.g., 6 feet]

**Antenna:**
- Type: [e.g., dipole, end-fed halfwave, vertical]
- Location: [e.g., in attic, on balcony, at roof]
- SWR @ 7.074 MHz (or 14.074 if on 20m): [e.g., 1.3:1]
- Feedline: [e.g., RG-58, LMR-400]

**Shielding / ferrites:**
- [x] Ferrite clamps on audio cable (how many, where)
- [x] Ferrite clamp on USB cable
- [x] Shielded enclosure or Faraday cage around interface
- [x] Other: describe

## What you tried

What steps did you take to isolate or fix the RFI?

1. ...
2. ...

**Result:** Did it help?

## Diagnostic output

Run `./bin/seeq doctor` and paste the output (especially audio levels and clock sync):

```
<paste seeq doctor output here>
```

Useful commands:

- `pactl list short sources` — show audio device details
- `rigctl -m <model> -r /dev/ttyUSB0 -s 19200 f` — confirm frequency readback (CAT health)
- `sox -r 12000 -b 16 -c 1 -e signed-integer /dev/stdin spectrogram.png | parecord -r 12000 -c 1 -f s16le -d 15` — 15-sec waterfall of RX

Paste any relevant output:

```
<paste diagnostics here>
```

## Notes for other hams

What did you learn? Any observations about what made the RFI worse or better?

**War story bonus:** If you solved it, describe your solution in detail — others hitting the same problem will thank you.
