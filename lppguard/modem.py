"""Lectura de logs del módem (.muxz) y localización de trazas LPP.

El .muxz es `MUZ\\x1a` + TLV + bloques Snappy; cada bloque contiene registros
DHL 2.0 `0x15 | u32 len | 01 f8 f9 | u32 trace_id | ...`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MUXZ_MAGIC = b"MUZ\x1a"
DHL_MAGIC = b"\x01\xf8\xf9"


def snappy_decompress(data: bytes) -> bytes:
    i = 0
    ulen = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        ulen |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
    out = bytearray()
    while i < len(data):
        tag = data[i]
        i += 1
        kind = tag & 3
        if kind == 0:
            ln = (tag >> 2) + 1
            if ln > 60:
                extra = ln - 60
                ln = int.from_bytes(data[i:i + extra], "little") + 1
                i += extra
            out += data[i:i + ln]
            i += ln
        elif kind == 1:
            ln = ((tag >> 2) & 7) + 4
            off = ((tag >> 5) << 8) | data[i]
            i += 1
            for _ in range(ln):
                out.append(out[-off])
        elif kind == 2:
            ln = (tag >> 2) + 1
            off = int.from_bytes(data[i:i + 2], "little")
            i += 2
            for _ in range(ln):
                out.append(out[-off])
        else:
            ln = (tag >> 2) + 1
            off = int.from_bytes(data[i:i + 4], "little")
            i += 4
            for _ in range(ln):
                out.append(out[-off])
    assert len(out) == ulen, (len(out), ulen)
    return bytes(out)


def muxz_blocks(path: str | Path):
    d = Path(path).read_bytes()
    if not d.startswith(MUXZ_MAGIC):
        raise ValueError("no es un .muxz")
    off = 4
    while off + 3 <= len(d):
        ty = d[off]
        ln = int.from_bytes(d[off + 1:off + 3], "little")
        off += 3 + ln
        if ty == 0:
            break
    while off + 6 <= len(d):
        ulen = int.from_bytes(d[off:off + 3], "little")
        clen = int.from_bytes(d[off + 3:off + 6], "little")
        off += 6
        comp = d[off:off + clen]
        off += clen
        if len(comp) < clen:
            break
        yield snappy_decompress(comp)


@dataclass
class Record:
    offset: int
    trace_id: int | None
    payload: bytes
    text: str


def records(block: bytes):
    n = len(block)
    i = 0
    while i + 9 <= n:
        if block[i] == 0x15:
            ln = int.from_bytes(block[i + 1:i + 5], "little")
            if 5 <= ln <= 8192 and i + ln <= n and block[i + 5:i + 8] == DHL_MAGIC:
                payload = block[i + 5:i + ln]
                tid = int.from_bytes(payload[3:7], "little") if len(payload) >= 7 else None
                yield Record(i, tid, payload, printable(payload))
                i += ln
                continue
        i += 1


def printable(payload: bytes, max_runs: int = 3) -> str:
    runs, cur = [], bytearray()
    for b in payload:
        if 0x20 <= b < 0x7F:
            cur.append(b)
        else:
            if len(cur) >= 5:
                runs.append(cur.decode("latin-1"))
                if len(runs) >= max_runs:
                    return " | ".join(runs)
            cur = bytearray()
    if len(cur) >= 5:
        runs.append(cur.decode("latin-1"))
    return " | ".join(runs)


def lpp_records(path: str | Path):
    """Trazas del módulo LPP (0x2E70/0x2E80) y LBS (0x3E40)."""
    for block in muxz_blocks(path):
        for rec in records(block):
            if rec.trace_id is None:
                continue
            slot = rec.trace_id >> 16
            if slot in (0x2E70, 0x2E80, 0x3E40):
                yield rec
