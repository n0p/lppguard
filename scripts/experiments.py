#!/usr/bin/env python3
"""Experimentos sobre el numbering de transacciones del e-SMLC (Amarisoft).

Variantes:
  A) location_req + lpp_request_location  -> rechazo (baseline)
     y una SEGUNDA lpp_request_location con el mismo correlation_id tras N s.
  B) location_req + lpp_request_location con delay N (caducidad de la
     transacción del requestCapabilities en el módem).

Cada variante marca el offset del mme.log y resume lo que aparece.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lppguard.core import CoreClient  # noqa: E402

CORE = os.environ.get("LPPGUARD_CORE_SSH", "")
LOG = os.environ.get("LPPGUARD_MME_LOG", "/var/log/mme.log")
WS = os.environ.get("LPPGUARD_CORE", "ws://127.0.0.1:9000/")
IMSI = os.environ.get("LPPGUARD_IMSI", "")


def ssh(cmd: str, timeout: int = 60) -> str:
    r = subprocess.run(["ssh", "-o", "LogLevel=ERROR", CORE, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def offset() -> int:
    return int(ssh(f"stat -c %s {LOG}").strip())


def since(off: int) -> str:
    return ssh(f"tail -c +{off + 1} {LOG}", timeout=90)


def report(off: int, tag: str) -> dict:
    text = since(off)
    ul = [l.strip() for l in text.splitlines()
          if l.strip().startswith("lpp-MessageBody c0")
          or "provideLocationInformation" in l or "provideCapabilities" in l
          or "error-r9" in l or "commonIEsError" in l]
    coords = [l.strip() for l in text.splitlines()
              if any(k in l for k in ("degreesLatitude", "degreesLongitude",
                                      "latitudeSign", "altitude",
                                      "locationEstimate"))]
    dl = [l.strip() for l in text.splitlines()
          if l.strip().startswith("lpp-MessageBody c1")]
    print(f"--- [{tag}] DL: {dl}")
    print(f"--- [{tag}] UL/errores: {ul[:12]}")
    if coords:
        print(f"--- [{tag}] COORDENADAS: {coords}")
    d = ROOT / "logs"
    d.mkdir(exist_ok=True)
    (d / f"exp_{tag}.log").write_text(text)
    return {"dl": dl, "ul": ul, "coords": coords}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=["A", "B"])
    ap.add_argument("--gap", type=float, default=3.0,
                    help="A: segundos hasta la 2ª petición; B: delay")
    a = ap.parse_args()

    core = CoreClient(WS)
    off = offset()
    cid = core.location_req(IMSI)
    print(f"location_req -> correlation_id={cid}")
    if a.variant == "B":
        time.sleep(a.gap)
        core.lpp_request_location(IMSI, cid, methods=["gnss"])
        print(f"lpp_request_location(gnss) tras {a.gap}s")
        time.sleep(6)
        report(off, f"B_{a.gap:g}s")
    else:
        time.sleep(0.5)
        core.lpp_request_location(IMSI, cid, methods=["gnss"])
        print("1ª lpp_request_location(gnss) enviada")
        time.sleep(a.gap)
        core.lpp_request_location(IMSI, cid, methods=["gnss"])
        print(f"2ª lpp_request_location(gnss) tras {a.gap}s (mismo cid)")
        time.sleep(6)
        report(off, f"A_gap{a.gap:g}s")
    core.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
