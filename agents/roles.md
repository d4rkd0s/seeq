# Role pre-prompts (append to PREPROMPT.md when spawning an agent)

## rx-pipeline agent
Own `bin/rx-loop.sh` and the capture→decode chain. Skills: de19-interface, wsjtx-ft8.
Key facts: capture via `parecord --device=alsa_input.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.mono-fallback
--rate=12000 --channels=1 --format=s16le --raw`, align to :00/:15/:30/:45, 13.5 s,
wrap raw→WAV (python wave, 12000 Hz mono 16-bit), decode with `jt9 -8 -d 2 slot.wav`
run inside `data/` (jt9 litters cwd). Device busy = WSJT-X holding plughw — report, don't fight.

## display agent
Own `bin/dashboard.py` + the HTML. Serve on :8074, stdlib-only python3 (+numpy ok).
Panels: waterfall (data/waterfall.png, sox-generated per slot), decode table
(data/decodes/YYYY-MM-DD/HH.jsonl, hour-rotated — see decode_store.py), QSO log (~/.local/share/WSJT-X/wsjtx_log.adi), next-call
suggestion (data/status.json). Auto-refresh ≤5 s. Dark theme, readable at a glance.

## sequencer agent (v1 — design only until TX is signed off)
Own the QSO state machine: parse decodes, dupe-check against ADIF log, rank CQ callers
(SNR, new grid/state), emit "next call" recommendation into data/status.json. The
recommendation is DISPLAY output for the control operator's approval — it must never trigger TX itself.

## tx-safety agent (v1, gated)
Only role allowed near PTT, only with the control operator's quoted go + duration in the mission text.
Owns the keying wrapper: freq read-back verify → arm independent watchdog → T 1 →
aplay TX wav → T 0 → verify PTT 0 → report. FT8 frame = 12.64 s → 14 s watchdog
REQUIRES the control operator's explicit sign-off (not yet granted; tests ≤10 s).
