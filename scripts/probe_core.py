#!/usr/bin/env python3
"""Lanza un trigger LPP y resume lo que aparece en el mme.log del core.

Uso:  python3 probe_core.py [--delay N] [--methods gnss] [--tag nombre]

Imprime, de las líneas nuevas del log: tipos de mensaje LPP, transactionNumber,
y el contenido de provideLocationInformation (si llega).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CORE = os.environ.get("LPPGUARD_CORE_SSH", "")
LOG = os.environ.get("LPPGUARD_MME_LOG", "/var/log/mme.log")


def ssh(cmd: str, timeout: int = 30) -> str:
    r = subprocess.run(["ssh", "-o", "LogLevel=ERROR", CORE, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def log_lines() -> int:
    return int(ssh(f"stat -c %s {LOG}").strip() or 0)


def new_lines(since: int) -> str:
    return ssh(f"tail -c +{since + 1} {LOG}", timeout=60)


def summarize(text: str) -> str:
    out = []
    ts = None
    for line in text.splitlines():
        m = re.match(r"^(\d\d:\d\d:\d\d\.\d\d\d) ", line)
        if m:
            ts = m.group(1)
        s = line.strip()
        if s.startswith("lpp-MessageBody"):
            out.append(f"{ts} {s}")
        elif s.startswith("transactionNumber"):
            out.append(f"{ts}   {s}")
        elif s.startswith("initiator"):
            out.append(f"{ts}   {s}")
        elif s.startswith("endTransaction"):
            out.append(f"{ts}   {s}")
        elif s.startswith("commonIEsError") or s.startswith("errorCause"):
            out.append(f"{ts} {s}")
        elif re.search(r"degreesLatitude|degreesLongitude|latitudeSign|altitude|"
                       r"locationEstimate|horizont|uncertainty", s):
            out.append(f"{ts}     {s}")
        elif "[NAS] UL" in line and "generic NAS" in line:
            out.append(f"{ts} NAS-UL genérico")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--methods", default=None)
    ap.add_argument("--tag", default="")
    ap.add_argument("--wait", type=float, default=6.0, help="s tras el trigger")
    ap.add_argument("--imsi", default=os.environ.get("LPPGUARD_IMSI"))
    a = ap.parse_args()

    n0 = log_lines()
    cmd = [sys.executable, "-m", "lppguard.cli", "trigger", "--imsi", a.imsi,
           "--delay", str(a.delay)]
    if a.methods:
        cmd += ["--methods", a.methods]
    print(f"[tag={a.tag}] log lines antes={n0}  cmd={' '.join(cmd[3:])}")
    r = subprocess.run(cmd, cwd=ROOT.parent, capture_output=True, text=True,
                       timeout=a.delay + 60)
    print(r.stdout.strip() or r.stderr.strip())
    time.sleep(a.wait)
    text = new_lines(n0)
    print("--- log nuevo ---")
    print(summarize(text) or "(nada LPP en el log)")
    # guardar crudo para inspección
    dump = ROOT.parent / "logs" / f"probe_{a.tag or 'run'}_{int(time.time())}.log"
    dump.parent.mkdir(exist_ok=True)
    dump.write_text(text)
    print(f"--- crudo: {dump} ({len(text.splitlines())} líneas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
