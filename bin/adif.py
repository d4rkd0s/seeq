"""Minimal ADIF parsing — stdlib only, no I/O.

Two layers:
  split_records(data)        raw bytes -> [(record_bytes, end_byte_offset)]
  parse_fields(record_str)   one record -> {lowercased_field: value}

parse_fields honors ADIF length prefixes (`<name:len>` / `<name:len:type>`)
by consuming exactly `len` characters per value and resuming the scan after
them — so a value may legally contain '<' without confusing the parser.
Field names are lowercased; values are returned verbatim.
"""
import re

_TAG = re.compile(r"<([A-Za-z_][A-Za-z0-9_]*):(\d+)(?::[^>]*)?>")


def split_records(data):
    """data: raw bytes of an ADIF file.
    Returns [(record_bytes, end_byte_offset), ...] — records are delimited
    by <eor> (case-insensitive); everything up to and including <eoh> (the
    ADIF header) is skipped. Absent an <eoh>, the whole file is candidate
    body (some minimal exports omit it)."""
    eoh = re.search(rb"<eoh>", data, re.I)
    body_start = eoh.end() if eoh else 0
    records = []
    pos = body_start
    for m in re.finditer(rb"<eor>", data[body_start:], re.I):
        end = body_start + m.end()
        rec = data[pos:end]
        if rec.strip():
            records.append((rec, end))
        pos = end
    return records


def parse_fields(record_str):
    """One ADIF record (str) -> {field_name_lower: value}. Length-prefix
    driven: after each <name:len> tag, exactly `len` characters are the
    value and scanning resumes past them."""
    fields = {}
    pos = 0
    while True:
        m = _TAG.search(record_str, pos)
        if not m:
            break
        n = int(m.group(2))
        start = m.end()
        fields[m.group(1).lower()] = record_str[start:start + n]
        pos = start + n
    return fields


def records_from_bytes(data):
    """Raw ADIF bytes -> [fields_dict, ...], with each record's end byte
    offset included as "_end" (used by the sync-offset bookkeeping)."""
    out = []
    for rec, end in split_records(data):
        f = parse_fields(rec.decode("utf-8", errors="replace"))
        if f:
            f["_end"] = end
            out.append(f)
    return out
