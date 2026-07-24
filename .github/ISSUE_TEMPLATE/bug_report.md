---
name: Bug report
about: Report a problem with SeeQ station control
title: "[BUG] "
labels: bug
assignees: ''

---

## Describe the bug

A clear and concise description of what went wrong.

## Steps to reproduce

1. I ran `...`
2. Then I...
3. Error occurred

## Expected behavior

What you expected to happen.

## Environment

**Required:**
- Rig: [e.g., Xiegu G90, Icom 7300]
- Interface: [e.g., DE-19, Digirig, SignaLink, built-in USB]
- Distro: [e.g., Ubuntu 24.04, Raspberry Pi OS, Fedora 40]
- Linux kernel: [output of `uname -r`]

**Optional but helpful:**
- Audio device: [output of `pactl list short sources`]
- WSJT-X version: [output of `jt9 --help | head -1`]

## Diagnostic output

Please run `./bin/seeq doctor` and paste the output:

```
<paste seeq doctor output here>
```

## Logs

If the error involves the chaser or RX loop, paste the last 20 lines of:

- `data/rx-loop.log` (RX decoding)
- `data/dashboard.log` (UI server)
- `data/qso.log` or the screen output from `seeq chase`

```
<paste relevant logs here>
```

## Radio configuration

Paste your `station.conf` (mask any sensitive data like grid square if you prefer):

```
<paste relevant parts of station.conf here>
```

## Notes

Any other context or observations that might help us debug.
