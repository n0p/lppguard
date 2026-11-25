#!/usr/bin/env python3
"""Resume los intercambios LPP del mme.log por Correlation-ID (parser por bloques).

Uso:  python3 parse_lcsap.py <volcado_mme.log> [corr_hex]
"""
from __future__ import annotations

import re
import sys

TS = re.compile(r"^(\d\d:\d\d:\d\d\.\d\d\d) ")
VAL = re.compile(r"value '([0-9A-F]{8})'H")
KIND = re.compile(r"lpp-MessageBody c\d: ([A-Za-z-]+)")


def parse(text: str):
    res = []
    ts = None
    cur = "?"
    corr = None
    want_val = False
    for line in text.splitlines():
        m = TS.match(line)
        if m:
            ts = m.group(1)
        if "[LCSAP]" in line:
            cur = "TO" if " TO " in line else ("FROM" if " FROM " in line else "?")
            corr = None
            want_val = False
            continue
        if "id-Correlation-ID" in line:
            want_val = True
            continue
        if want_val:
            mv = VAL.search(line)
            if mv:
                corr = mv.group(1)
                want_val = False
            continue
        mk = KIND.search(line)
        if mk:
            res.append((ts, cur, corr, mk.group(1)))
    return res


def main() -> int:
    path = sys.argv[1]
    want = sys.argv[2].upper() if len(sys.argv) > 2 else None
    txt = open(path, errors="ignore").read()
    for ts, d, c, k in parse(txt):
        if want and c != want:
            continue
        print(f"{ts} {d:4s} corr={c} {k}")
    hits = [l.strip() for l in txt.splitlines()
            if any(x in l for x in ("degreesLatitude", "degreesLongitude",
                                    "errorCause", "altitude "))]
    print("--- coords/errores en el volcado:")
    for c in hits[-14:]:
        print("   ", c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
