"""lppguard — dispara y observa LPP; laboratorio de posiciones falsas."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

from .core import CoreClient, CoreError
from . import modem, patch

# Todo lo del banco se toma del entorno: el repo no lleva IPs ni trazas.
CORE_SSH = os.environ.get("LPPGUARD_CORE_SSH", "")          # p. ej. root@192.0.2.10
MME_LOG = os.environ.get("LPPGUARD_MME_LOG", "/var/log/mme.log")
CORE_WS = os.environ.get("LPPGUARD_CORE", "ws://127.0.0.1:9000/")


# -- log del core -------------------------------------------------------
def _ssh(cmd: str, timeout: int = 60) -> str:
    r = subprocess.run(["ssh", "-o", "LogLevel=ERROR", CORE_SSH, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def log_offset() -> tuple[str, int]:
    """Marca (inodo, tamaño) del mme.log; el log rota a 1 GB."""
    out = _ssh(f"stat -c '%i %s' {MME_LOG}").strip().split()
    return out[0], int(out[1])


def log_since(mark: tuple[str, int], tail_bytes: int = 1_500_000) -> str:
    """Trozo de log posterior a `mark`.

    No se puede confiar en offsets: el log rota a 1 GB y el canal ssh trunca
    `tail -c +N`. Se lee la cola (1,5 MB ~ varios minutos de log) y, si rotó,
    el fichero entero.
    """
    ino, size = mark
    cur = _ssh(f"stat -c '%i %s' {MME_LOG}").strip().split()
    if not cur:
        return ""
    rotated = cur[0] != ino or int(cur[1]) < size
    if rotated:
        return _ssh(f"cat {MME_LOG}", timeout=120)
    return _ssh(f"tail -c {tail_bytes} {MME_LOG}", timeout=120)


def parse_result(text: str, since_ts: str | None = None) -> dict:
    """Extrae de un trozo de mme.log la respuesta LPP del UE.

    Ojo: el log imprime el APDU del UE también como `lpp-MessageBody c1:` (vista
    LCSAP FROM), así que no se puede distinguir DL/UL por c0/c1: las respuestas
    del UE son las que traen provideCapabilities / provideLocationInformation /
    error-r9 (el e-SMLC sólo manda request*).

    `since_ts` = 'HH:MM:SS' del disparo; se descartan las líneas anteriores
    (el Correlation-ID no sirve como marcador: '00000005'H aparece en otras IEs).
    Ojo: las líneas del volcado ASN.1 (*continuación*) no llevan hora, así que el
    corte se hace por posición: se descarta todo hasta la primera línea con hora
    >= since_ts.  Si no, coordenadas de una respuesta anterior se cuelan en el
    resumen.
    """
    res: dict = {"dl": [], "provide": None, "error": None, "coords": {},
                 "loc_error": None}
    lines = text.splitlines()
    start = 0
    if since_ts:
        for i, line in enumerate(lines):
            m0 = re.match(r"^(\d\d:\d\d:\d\d)\.", line)
            if m0 and m0.group(1) >= since_ts:
                start = i
                break
        else:
            return res
    kind = None
    for line in lines[start:]:
        s = line.strip()
        m = re.match(r"lpp-MessageBody c\d: ([A-Za-z-]+)", s)
        if m:
            kind = m.group(1)
            kind = "request" if kind.startswith("request") else "provide"
            if kind == "request":
                res["dl"].append(m.group(1))
            else:
                res["provide"] = m.group(1)
                res["coords"] = {}       # cada cuerpo del UE trae lo suyo
                res["loc_error"] = None
        if s.startswith("errorCause"):
            res["error"] = s.split(None, 1)[1] if len(s.split()) > 1 else s
        m = re.match(r"locationfailurecause (\w+)", s)
        if m:
            res["loc_error"] = m.group(1)
        if kind != "provide":
            continue        # coordenadas de algo que no es del UE
        m = re.match(r"degreesLatitude (-?\d+)", s)
        if m:
            res["coords"]["lat"] = int(m.group(1))
        m = re.match(r"degreesLongitude (-?\d+)", s)
        if m:
            res["coords"]["lon"] = int(m.group(1))
        m = re.match(r"latitudeSign (north|south)", s)
        if m:
            res["coords"]["hemisferio"] = m.group(1)
        m = re.match(r"altitude (-?\d+)", s)
        if m:
            res["coords"]["alt"] = int(m.group(1))
    return res


# -- subcomandos --------------------------------------------------------
def cmd_spoof(a) -> int:
    """Parchea la imagen del modem para que conteste una posicion fija."""
    img = open(a.orig, "rb").read()
    if a.check:
        ok = True
        for label, good in patch.check(img):
            print(f"{'ok  ' if good else 'NO  '} {label}")
            ok = ok and good
        print("imagen esperada" if ok else "la imagen no es la esperada")
        return 0 if ok else 1
    try:
        out, log = patch.build(img, lat=a.lat, lon=a.lon, alt=a.alt)
    except patch.PatchError as e:
        print(f"no se puede parchear: {e}", file=sys.stderr)
        return 1
    open(a.out, "wb").write(out)
    for line in log:
        print("  " + line)
    print(f"\noriginal : {patch.sha256(img)}")
    print(f"parcheada: {patch.sha256(out)}  ({len(out)} bytes)")
    print(f"\nescribe la imagen:\n  sudo dd if={a.out} of=/dev/disk/by-partlabel/md1img_a bs=1M conv=fsync"
          "\n  sudo reboot")
    print("comprueba con: lppguard verify --methods gnss")
    return 0



def cmd_trigger(a) -> int:
    methods = ([m.strip() for m in a.methods.split(",") if m.strip()]
               if a.methods else None)
    try:
        core = CoreClient(a.core)
    except Exception as e:
        print(f"no conecta con el core: {e}", file=sys.stderr)
        return 2
    try:
        cid = core.request_location(a.imsi, a.cell_id, delay=a.delay,
                                    methods=methods, retry=a.retry,
                                    retry_delay=a.retry_delay)
        print(f"LPP RequestLocationInformation enviado (correlation_id={cid}"
              f"{', delay=%.0fs' % a.delay if a.delay else ''}"
              f"{', methods=' + ','.join(methods) if methods else ''}"
              f", {a.retry + 1} envío(s))")
    except CoreError as e:
        print(f"error del core: {e}", file=sys.stderr)
        return 1
    finally:
        core.close()
    return 0


def cmd_verify(a) -> int:
    """Dispara y comprueba en el mme.log que llega provideLocationInformation.

    Repite el ciclo completo (location_req + N lpp_request_location) hasta
    `--cycles` veces o hasta que llegue el provideLocationInformation: el estado
    LPP del módem se queda "sordo" a veces (no contesta ni al
    requestCapabilities) y sólo un ciclo nuevo lo desatasca.
    """
    methods = ([m.strip() for m in a.methods.split(",") if m.strip()]
               if a.methods else None)
    try:
        core = CoreClient(a.core)
    except Exception as e:
        print(f"no conecta con el core: {e}", file=sys.stderr)
        return 2
    res: dict = {}
    cid = None
    try:
        for ciclo in range(1, a.cycles + 1):
            off = log_offset()
            t0 = time.strftime("%H:%M:%S")
            cid = core.request_location(a.imsi, a.cell_id, delay=a.delay,
                                        methods=methods, retry=a.retry,
                                        retry_delay=a.retry_delay)
            time.sleep(a.wait)
            res = parse_result(log_since(off), t0)
            print(f"[ciclo {ciclo}/{a.cycles}] corr={cid} DL={res['dl']} "
                  f"UL={res['provide']} err={res['error']} "
                  f"locErr={res['loc_error']}")
            if res["provide"] == "provideLocationInformation":
                break
            time.sleep(a.pause)
    except CoreError as e:
        print(f"error del core: {e}", file=sys.stderr)
        return 1
    finally:
        core.close()
    print(f"correlation_id={cid} methods={methods or 'core(default)'}")
    if res.get("provide"):
        print(f"UL del UE     : {res['provide']}")
    if res.get("loc_error"):
        print(f"locationError : {res['loc_error']}")
    if res.get("error"):
        print(f"error LPP     : {res['error']}")
    if res.get("coords", {}).get("lat") is not None:
        lat = res["coords"]["lat"] * (-1 if res["coords"].get("hemisferio") == "south" else 1)
        lon = res["coords"].get("lon", 0)
        print(f"locationEstimate: lat {lat} (1e-7 deg) lon {lon} "
              f"alt {res['coords'].get('alt')} m  -> lat {lat/1e7:.7f} / lon {lon/1e7:.7f}")
        return 0
    if res.get("loc_error"):
        return 0        # el UE contestó, pero con locationError (sin posición)
    print("sin locationEstimate (provideLocationInformation ausente o sin estimación)")
    return 3


def cmd_watch(a) -> int:
    n = 0
    for rec in modem.lpp_records(a.file):
        n += 1
        tid = f"0x{rec.trace_id:08X}" if rec.trace_id else "-"
        print(f"{tid} slot={(rec.trace_id or 0) >> 16:04X} {rec.text[:90]}")
    print(f"-- {n} registros LPP/LBS")
    return 0


def cmd_status(a) -> int:
    try:
        core = CoreClient(a.core)
    except Exception as e:
        print(f"core inaccesible: {e}", file=sys.stderr)
        return 2
    ues = core.ue_get()
    core.close()
    print(f"core ok, {len(ues)} UE(s)")
    for u in ues:
        print(f"  {u.get('imsi')} rat={u.get('rat_type')} tac={u.get('tac')} "
              f"registered={u.get('registered')} bearers={len(u.get('bearers', []))}")
    return 0


def _common(parser: argparse.ArgumentParser, request: bool = True) -> None:
    parser.add_argument("--imsi", default=os.environ.get("LPPGUARD_IMSI"))
    if not request:
        return
    parser.add_argument("--cell-id", type=int,
                        default=int(os.environ.get("LPPGUARD_CELL_ID", "0")))
    parser.add_argument("--delay", type=float, default=0.0,
                        help="segundos entre location_req y el primer "
                             "lpp_request_location")
    parser.add_argument("--methods", default=None,
                        help="lista separada por comas: gnss,ecid,otdoa")
    parser.add_argument("--retry", type=int, default=1,
                        help="reenvíos de lpp_request_location (default 1: el "
                             "1º aborta la transacción zombie del "
                             "requestCapabilities y el 2º se procesa)")
    parser.add_argument("--retry-delay", type=float, default=3.0)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lppguard")
    p.add_argument("--core", default=CORE_WS)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("trigger", help="envía un RequestLocationInformation")
    _common(t)
    t.set_defaults(func=cmd_trigger)

    v = sub.add_parser("verify", help="trigger + comprueba el mme.log")
    _common(v)
    v.add_argument("--wait", type=float, default=8.0,
                   help="segundos a esperar la respuesta del UE")
    v.add_argument("--cycles", type=int, default=3,
                   help="ciclos completos (location_req + peticiones) hasta "
                        "obtener provideLocationInformation")
    v.add_argument("--pause", type=float, default=10.0,
                   help="segundos entre ciclos")
    v.set_defaults(func=cmd_verify)

    w = sub.add_parser("watch", help="lista trazas LPP/LBS de un .muxz")
    w.add_argument("file")
    w.set_defaults(func=cmd_watch)

    sp = sub.add_parser("spoof", help="parchea la imagen del módem: posición fija por LPP")
    sp.add_argument("--orig", required=True, help="imagen md1img original (dd de la partición)")
    sp.add_argument("--out", default="modem-patched.img")
    sp.add_argument("--check", action="store_true", help="solo comprobar que la imagen es la esperada")
    sp.add_argument("--lat", type=float, default=0.0)
    sp.add_argument("--lon", type=float, default=0.0)
    sp.add_argument("--alt", type=int, default=0)
    sp.set_defaults(func=cmd_spoof)

    s = sub.add_parser("status", help="estado del core y UEs")
    s.set_defaults(func=cmd_status)

    a = p.parse_args(argv)
    if getattr(a, "cmd", "") in ("trigger", "verify") and not a.imsi:
        p.error("falta --imsi (o LPPGUARD_IMSI en el entorno)")
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
