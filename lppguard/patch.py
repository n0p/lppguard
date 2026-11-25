"""Archivo de la imagen del módem: fija la posición que el terminal responde por LPP.

La imagen `md1img` es una cabecera de 0x200 bytes seguida del volcado lineal de
memoria desde la VA 0x90000000. Se parchean cinco puntos del código nanoMIPS y
se alojan cinco stubs en el hueco que deja una función de test del firmware
(VA 0x90d0ff6e, 0x434 = 1076 bytes) que en este build solo es alcanzable por el
canal de depuración del módem, no por el AP.

Efecto: toda petición de posición A-GNSS se contesta con una posición fija, y
cualquier otro método (ECID, OTDOA…) se contesta con `locationError` en vez de
silencio.

Necesita un toolchain nanoMIPS (binutils) en `MODEM_TOOLCHAIN_DIR`. El ELF del
firmware es opcional: solo para replicar los parches y poder desensamblar.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

IMG_VA_BASE = 0x90000000
IMG_HDR = 0x200

# Hueco liberado para los stubs (0x434 bytes en total, ~120 usados).
STUB1_VA = 0x90D0FF6E

# (nombre, VA, bytes originales esperados, símbolo del stub)
JUMPS = [
    ("forzar resultStatus/type/gnssMethod en la confirmación de posición",
     0x90D0FC24, bytes.fromhex("000a1a4d"), "stub1"),
    ("escribir el locationEstimate fijo",
     0x90D135A8, bytes.fromhex("c060f701"), "stub2"),
    ("métodos soportados: solo A-GNSS",
     0x90D0AA16, bytes.fromhex("c0600c00"), "stub3"),
    ("capacidad de posicionamiento: solo A-GNSS",
     0x90C9E29E, bytes.fromhex("236294ff"), "stub4"),
    ("petición que no es A-GNSS: contestar locationError",
     0x90D0EBD8, bytes.fromhex("f0845820"), "stub5"),
]

# colas de 2 bytes de las instrucciones de 6 bytes reemplazadas -> nop
DEAD_TAILS = [(0x90D135AC, "702e"), (0x90D0AA1A, "702e"), (0x90C9E2A2, "6c95")]


class PatchError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _toolchain() -> dict[str, str]:
    d = os.environ.get("MODEM_TOOLCHAIN_DIR", "")
    if not d:
        raise PatchError("define MODEM_TOOLCHAIN_DIR (bin de un toolchain nanoMIPS)")
    out = {}
    for name in ("as", "ld", "objcopy", "nm"):
        path = os.path.join(d, f"nanomips-elf-{name}")
        if not os.path.exists(path):
            raise PatchError(f"falta {path}")
        out[name] = path
    return out


def va_to_img(va: int) -> int:
    return IMG_HDR + (va - IMG_VA_BASE)


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise PatchError(f"{' '.join(cmd[:2])}: {r.stderr.strip()[:200]}")


def _coords_inc(lat: float, lon: float, alt: int, unc_k: int, uncalt_k: int,
                conf: int, loc_err: int) -> str:
    lat_raw = round(lat / 90 * 2 ** 23)
    lon_raw = round(lon / 180 * 2 ** 24)
    if not 0 <= lat_raw <= 0x7FFFFF:
        raise PatchError("latitud fuera de rango LPP")
    if not -0x800000 <= lon_raw <= 0x7FFFFF:
        raise PatchError("longitud fuera de rango LPP")
    return (
        "/* generado por lppguard — no editar */\n"
        f".set LAT_RAW, {lat_raw}\n"
        f".set LON_RAW, {lon_raw & 0xFFFFFFFF}\n"
        f".set ALT_M, {alt}\n"
        f".set UNC_K, {unc_k}\n"
        f".set UNCALT_K, {uncalt_k}\n"
        f".set CONF, {conf}\n"
        f".set LOC_ERR_CAUSE, {loc_err}\n"
    )


def _build_stubs(tc: dict[str, str], coords: str) -> tuple[bytes, dict[str, int]]:
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(DATA / "stubs.s", os.path.join(tmp, "stubs.s"))
        open(os.path.join(tmp, "coords.inc"), "w").write(coords)
        obj = os.path.join(tmp, "stubs.o")
        elf = os.path.join(tmp, "stubs.elf")
        raw = os.path.join(tmp, "stubs.bin")
        _run([tc["as"], "-I", tmp, "-o", obj, os.path.join(tmp, "stubs.s")])
        _run([tc["ld"], f"-Ttext={STUB1_VA:#x}", "-o", elf, obj])
        _run([tc["objcopy"], "-O", "binary", "-j", ".text", elf, raw])
        blob = open(raw, "rb").read()
        if len(blob) > 0x434:
            raise PatchError(f"los stubs ocupan {len(blob)} B y el hueco es de 1076")
        syms: dict[str, int] = {}
        nm = subprocess.run([tc["nm"], obj], capture_output=True, text=True)
        for line in nm.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[2].startswith("stub"):
                syms[parts[2]] = STUB1_VA + int(parts[0], 16)
        for i in range(1, 6):
            if f"stub{i}" not in syms:
                raise PatchError(f"falta el símbolo stub{i}")
        return blob, syms


def _jump_bytes(tc: dict[str, str], va: int, target: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "jump.s")
        open(src, "w").write(
            "    .set noreorder\n    .text\n    .globl _start\n_start:\n"
            f"    bc  {target:#x}\n")
        obj = os.path.join(tmp, "jump.o")
        elf = os.path.join(tmp, "jump.elf")
        raw = os.path.join(tmp, "jump.bin")
        _run([tc["as"], "-o", obj, src])
        _run([tc["ld"], f"-Ttext={va:#x}", "-e", "_start", "-o", elf, obj])
        _run([tc["objcopy"], "-O", "binary", "-j", ".text", elf, raw])
        data = open(raw, "rb").read()
        if len(data) != 4:
            raise PatchError(f"salto en {va:#x}: {len(data)} B, esperaba 4")
        return data


def check(img: bytes) -> list[tuple[str, bool]]:
    """Comprueba que los cinco puntos tienen los bytes esperados."""
    out = []
    for label, va, want, _sym in JUMPS:
        off = va_to_img(va)
        out.append((f"{va:#010x} {label}", bytes(img[off:off + len(want)]) == want))
    for va, tail in DEAD_TAILS:
        off = va_to_img(va)
        out.append((f"{va:#010x} cola de instrucción", bytes(img[off:off + 2]) == bytes.fromhex(tail)))
    return out


def build(img: bytes, lat: float = 0.0, lon: float = 0.0, alt: int = 0,
          unc_k: int = 4, uncalt_k: int = 4, conf: int = 68,
          loc_err: int = 1) -> tuple[bytes, list[str]]:
    """Devuelve la imagen parcheada y la lista de parches aplicados."""
    tc = _toolchain()
    bad = [label for label, ok in check(img) if not ok]
    if bad:
        raise PatchError("la imagen no es la esperada en: " + "; ".join(bad))
    coords = _coords_inc(lat, lon, alt, unc_k, uncalt_k, conf, loc_err)
    stubs, syms = _build_stubs(tc, coords)

    out = bytearray(img)
    log = [f"stubs: {len(stubs)} B en {STUB1_VA:#x} "
           + ", ".join(f"{k}@{v:#x}" for k, v in sorted(syms.items()))]
    off = va_to_img(STUB1_VA)
    out[off:off + len(stubs)] = stubs

    for label, va, want, sym in JUMPS:
        blk = _jump_bytes(tc, va, syms[sym])
        o = va_to_img(va)
        out[o:o + 4] = blk
        log.append(f"salto en {va:#010x} -> {sym}@{syms[sym]:#x}: {label}")

    for va, tail in DEAD_TAILS:
        o = va_to_img(va)
        out[o:o + 2] = b"\x00\x00"
        log.append(f"nop en cola {va:#010x}")

    log.append(f"coordenadas: lat {lat}, lon {lon}, alt {alt} m")
    return bytes(out), log
