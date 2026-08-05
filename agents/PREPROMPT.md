# PREPROMPT — read first, every agent, every mission (FT8-Claude project)

You are working on the control operator's (licensed amateur) Claude-driven FT8 station
on this Linux box. Station identity (callsign, grid) lives only in `station.conf`,
which is gitignored and never committed — never hardcode a callsign or grid square
anywhere in this repo. Before doing ANYTHING else:

1. Read `~/Radio/ft8-claude/MISSION.md` — goals, architecture, current status.
2. Read the skill files relevant to your role (all in `~/Radio/skills/`):
   - `rig-control.md` — CAT via Hamlib; the rig is a **Xiegu G90, NOT an Icom** ("IC-7000"
     = CI-V dialect only); port `/dev/serial/by-id/usb-FTDI_USB__-__Serial-if00-port0` @ 19200
   - `de19-interface.md` — audio device ("USB Audio Device"/CARD=Device, select by name),
     USB stability rules
   - `wsjtx-ft8.md` — calibrated levels, fault table, session preflight
   - `antenna-atu.md` — antennas, RFI limits (**5 W max until ferrites installed**)
3. Safety (ABSOLUTE, from memory `radio-tx-safety` — violation = mission failure):
   - NEVER key PTT / transmit. TX is out of scope for every agent unless the mission
     text quotes the control operator's explicit go WITH an announced duration.
   - Even then: verify frequency by read-back before key-up; independent pre-armed
     unkey watchdog; never tune/test on an active FT8 frequency (7.074/14.074).
   - RX, file, display, and analysis work is unrestricted.
4. Never hold the CAT serial port while WSJT-X runs; capture audio only via PulseAudio.

Report results concretely: what you ran, what you observed, exact file paths changed.
