---
name: qrz-api
description: Working with the QRZ Logbook API (logbook.qrz.com/api) — request/response format, wire quirks, confirmation semantics, and where COTA's implementation lives. Read before touching bin/qrz_api.py, bin/logsync.py, bin/qrz_fetch.py, or bin/logbook.py.
---

# Skill: QRZ Logbook API

Verified 2026-07-12 against the official guide
(https://www.qrz.com/docs/logbook/QRZLogbookAPI.html) and two production
implementations (Wavelog, k0swe/qrz-logbook), then exercised live. Requires
an "XML Logbook Data" subscription; the key lives at
`~/.config/cota/qrz.key` (chmod 600, **outside the repo, never committed —
and never hardcode or echo it in code, tests, logs, or docs**).

## Where things live in COTA

| File | Role |
|---|---|
| `bin/qrz_api.py` | Protocol: request building, response parsing, the one curl touchpoint (`post()`). Pure except `post()`. |
| `bin/adif.py` | ADIF splitting/field parsing (length-prefix-driven), shared by everything. |
| `bin/logsync.py` | Idempotent INSERT sync of new ADIF records (byte-offset state in `~/.config/cota/qrz.state`). `--dry-run` = zero network. |
| `bin/qrz_fetch.py` | Pages the whole QRZ book into `data/qrz-logbook.json` (the dashboard's cache). |
| `bin/logbook.py` | Pure local↔QRZ record matching (±30 min) for the Logbook widget. |
| `tools/test_qrz.py` | Unit tests for all the pure parts — run before any change here. |

## Protocol essentials

HTTPS form-POST to `https://logbook.qrz.com/api`; every request carries
`KEY=` + `ACTION=` (STATUS / FETCH / INSERT / DELETE). **Send an
identifiable User-Agent** (see `qrz_api.USER_AGENT`) — QRZ rate-limits
generic agents.

- **INSERT**: `ADIF=` one record ending `<eor>`. Duplicate → HTTP 200 with
  `STATUS=FAIL&RESULT=FAIL&REASON=Unable to add QSO to database: duplicate&EXTENDED=`
  (treat as already-synced). `OPTION=REPLACE` exists but **overwrites even
  confirmed QSOs** — don't use it casually.
- **FETCH**: `OPTION=` criteria joined with **semicolons** (the guide is
  ambiguous; `;` is what works in production): `TYPE:ADIF;MAX:250;AFTERLOGID:0`,
  also `BETWEEN:YYYY-MM-DD+YYYY-MM-DD`, `CALL:`, `BAND:`, `MODE:`,
  `MODSINCE:`, `STATUS:CONFIRMED`. Page with MAX + AFTERLOGID =
  (highest returned `app_qrzlog_logid` + 1) until a short page.
  **An empty logbook returns `RESULT=FAIL`** — not an outage.
- **STATUS**: summary counts; response nests an `&`-joined `DATA=` blob
  (flat parsing recovers its keys since they're in the known-key set).

## The two wire quirks that WILL bite you

1. **Response values are not URL-escaped**, and FETCH's ADIF value contains
   raw `&` (angle brackets arrive as `&lt;`/`&gt;` HTML entities). A naive
   `&`-split shreds it. `qrz_api.parse_fields` therefore slices between
   *known key* boundaries only — if QRZ adds a new response key, add it to
   `_KNOWN_KEYS` or it'll be swallowed into the preceding value.
2. HTML-unescape the ADIF blob before parsing (`extract_adif_records` does).

## Confirmation semantics (why "time doesn't match" happens)

Both stations must log the QSO; QRZ auto-confirms when **both callsigns,
band, mode, and UTC time within ±30 minutes** agree. `app_qrzlog_status`:
**`C` = Confirmed (the only confirmed value)**; `N` = not, `2` = requested,
`S` = request seen, `R` = rejected, `A` = reserved. COTA's exact FT8 slot
timestamps confirm fast; hand-entered times >30 min off never auto-confirm
and need manual resolution on qrz.com. `bin/logbook.py` mirrors the same
±30 min window (`tol_s=1800`).

## Rules of engagement

- Reading (FETCH/STATUS) is safe anytime; it never touches the rig.
- Don't loop FETCH aggressively — page politely, cache in
  `data/qrz-logbook.json`, let the dashboard read the cache.
- DELETE is irreversible; nothing in COTA calls it, keep it that way
  without the operator's explicit ask.
