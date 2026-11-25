# lppguard

Herramientas de laboratorio alrededor de **LPP** (*LTE Positioning Protocol*)
contra un core 4G/5G propio. Dos mitades:

- **Pedir y mirar**: disparar una petición de posición como lo haría la red,
  resumir qué contesta el terminal —una estimación, un error o nada— y buscar
  los mensajes LPP en una captura de trazas del módem.
- **Falsear**: parchear la imagen del firmware para que el módem conteste
  siempre una posición fija, y con un `locationError` cuando se le pide un
  método que no es A-GNSS.

## Qué hay

| Pieza | Qué hace |
|---|---|
| `status` | lista las UEs registradas en el core (por su API WebSocket) |
| `trigger` | `location_req` + `lpp_request_location`: la red pide la posición |
| `verify` | lo anterior y, después, lee el log del MME y resume la respuesta del terminal |
| `watch` | lista las trazas LPP/LBS de un `.muxz` (contenedor de trazas del módem) |
| `spoof` | parchea la imagen `md1img` del teléfono: posición fija por LPP |
| `scripts/parse_lcsap.py` | resume por *correlation id* los intercambios LPP de un volcado del log |
| `scripts/probe_core.py` | dispara y describe lo que aparece en el log |

## Requisitos

- Python 3.10 o posterior.
- `websocket-client` para los subcomandos que hablan con el core.
- Acceso SSH al log del core solo para `verify` y `probe_core`.
- Para `spoof`: un toolchain **nanoMIPS** (binutils) y la imagen `md1img` del
  propio teléfono (`dd if=/dev/disk/by-partlabel/md1img_a of=modem.img`).

```bash
python3 -m venv .venv && .venv/bin/pip install websocket-client
```

## Uso

La configuración del banco va por entorno; el repositorio no lleva ni
direcciones ni identificadores:

```bash
export LPPGUARD_CORE=ws://127.0.0.1:9000/      # API del core
export LPPGUARD_IMSI=...                        # UE objetivo
export LPPGUARD_CELL_ID=...                     # celda del banco
export LPPGUARD_CORE_SSH=root@core              # para leer el log (verify)
export LPPGUARD_MME_LOG=/var/log/mme.log

.venv/bin/python -m lppguard.cli status
.venv/bin/python -m lppguard.cli trigger --imsi "$LPPGUARD_IMSI"
.venv/bin/python -m lppguard.cli verify  --imsi "$LPPGUARD_IMSI" --wait 8 --cycles 3
.venv/bin/python -m lppguard.cli watch captura.muxz
```

## Falsear la posición

La imagen `md1img` es una cabecera de 0x200 bytes seguida del volcado lineal de
memoria desde la VA `0x90000000`. Se parchean cinco puntos del código nanoMIPS
y se alojan cinco *stubs* en el hueco (1076 bytes) que deja una función de test
del firmware, alcanzable solo por el canal de depuración del módem y no por el
AP.

| Punto | Efecto |
|---|---|
| `0x90D0FC24` | fuerza `resultStatus=NO_ERROR`, `type=POS_CNF`, `gnssMethod=GPS` en la confirmación de posición |
| `0x90D135A8` | escribe el `locationEstimate` ASN.1 con latitud, longitud y altitud fijas |
| `0x90D0AA16` | el estado de métodos soportados pasa a «solo A-GNSS» |
| `0x90C9E29E` | ídem del lado de la capacidad de posicionamiento |
| `0x90D0EBD8` | si la petición no es A-GNSS, contesta `provideLocationInformation` + `locationError` |

Cada salto se ensambla y se valida antes de aplicarlo: si los bytes originales
no son los esperados (otro build, otro firmware), aborta sin tocar nada.

```bash
export MODEM_TOOLCHAIN_DIR=/ruta/al/bin        # nanomips-elf-{as,ld,objcopy,nm}
.venv/bin/python -m lppguard.cli spoof --check --orig modem.img
.venv/bin/python -m lppguard.cli spoof --orig modem.img --out modem-patched.img \
    --lat 38.023085 --lon -1.173735 --alt 167
```

Escribe la imagen con `dd` en `/dev/disk/by-partlabel/md1img_a` y reinicia. No
lleva firmas ni sumas de verificación y el bootloader la acepta tal cual; el AP
no depende de ella, así que se revierte por SSH con la copia original. Guarda
esa copia y su `sha256` antes de tocar nada.

Con el módem parcheado, `verify --methods gnss` debe devolver el
`locationEstimate` que pusiste; con `--methods ecid`,
`locationError requestedMethodNotSupported`.

## Notas de campo

Dos cosas que nos costaron horas y que quedan documentadas en el código:

- El e-SMLC del core de test numera la sesión entera como `transactionNumber 1`
  con `endTransaction FALSE`: manda `requestCapabilities` y luego
  `requestLocationInformation` con el mismo número. El módem crea la
  transacción con el primero y descarta el segundo. Reenviar
  `lpp_request_location` con el mismo `correlation_id` sí se procesa.
- En el log, el APDU del terminal aparece también como `lpp-MessageBody c1:`,
  así que no se distingue DL/UL por `c0`/`c1`: las respuestas del terminal son
  las que traen `provideCapabilities`, `provideLocationInformation` o
  `error`.

## Alcance

Herramienta de banco: redes propias, terminales propios. Nada de esto tiene
sentido —ni es legal— contra una red o un terminal ajenos.

Forma parte del trabajo que contamos en el blog de Gaia Lab:
<https://ants.inf.um.es/gaialab/blog/projects/bajo-el-capo-del-modem/>.
