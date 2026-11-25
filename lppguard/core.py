"""Cliente del API WebSocket del core Amarisoft para disparar LPP.

Flujo EPC (probado contra ltemme 2024-06-15):
    location_req  -> correlation_id (crea el contexto de localización)
    lpp_request_location {supi, correlation_id}  -> el e-SMLC envía
                                                    RequestLocationInformation

`lpp_request_location` NO acepta `lpp_methods` (el parser lo marca como
propiedad no usada); los métodos se eligen en el core (lpp_test).
"""

from __future__ import annotations

import json
import time


class CoreError(RuntimeError):
    pass


class CoreClient:
    def __init__(self, url: str = "ws://127.0.0.1:9000/", timeout: float = 15.0):
        from websocket import create_connection  # import perezoso
        self.url = url
        self._ws = create_connection(url, timeout=timeout)

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    # -- transporte -----------------------------------------------------
    def send(self, msg: dict) -> None:
        self._ws.send(json.dumps(msg))

    def recv(self, timeout: float = 2.0) -> dict | None:
        self._ws.settimeout(timeout)
        try:
            return json.loads(self._ws.recv())
        except Exception:
            return None

    def call(self, msg: dict, reply_to: str, timeout: float = 8.0) -> dict:
        """Envía y espera la respuesta con `message == reply_to`."""
        self.send(msg)
        end = time.time() + timeout
        while time.time() < end:
            r = self.recv(max(0.2, end - time.time()))
            if not r:
                continue
            if r.get("message") == reply_to:
                if "error" in r:
                    raise CoreError(r["error"])
                return r
        raise CoreError(f"timeout esperando {reply_to}")

    # -- operaciones ----------------------------------------------------
    def ue_get(self) -> list[dict]:
        return self.call({"message": "ue_get"}, "ue_get").get("ue_list", [])

    def location_req(self, imsi: str, cell_id: int = 0,
                     plmn: str = "00101", type_: str = "geographic") -> int:
        r = self.call({"message": "location_req", "plmn": plmn,
                       "cell_id": cell_id, "imsi": imsi, "type": type_},
                      "location_req")
        return int(r["correlation_id"])

    def lpp_request_location(self, imsi: str, correlation_id: int,
                             methods: list[str] | None = None,
                             report_type: str | None = None,
                             is_5gs: bool | None = None) -> dict:
        msg: dict = {"message": "lpp_request_location",
                     "supi": f"imsi-{imsi}",
                     "correlation_id": correlation_id}
        if methods:
            msg["lpp_methods"] = methods
        if report_type:
            msg["report_type"] = report_type
        if is_5gs is not None:
            msg["is_5gs"] = bool(is_5gs)
        return self.call(msg, "lpp_request_location", timeout=10)

    def request_location(self, imsi: str, cell_id: int = 0,
                         plmn: str = "99910", delay: float = 0.0,
                         methods: list[str] | None = None,
                         retry: int = 1, retry_delay: float = 3.0) -> int:
        """location_req + lpp_request_location. Devuelve el correlation_id.
        El e-SMLC de test numera TODO como transactionNumber 1 con
        endTransaction FALSE: manda requestCapabilities al crear la sesión y
        luego requestLocationInformation con el mismo número. El módem crea
        la transacción con la primera y descarta la segunda, así que nunca se
        ejecuta el camino de posicionamiento.

        Arreglo: reenviar lpp_request_location con el MISMO correlation_id
        (y por tanto el mismo transactionNumber 1). Como la transacción
        anterior ya está abortada, el módem procesa la petición y responde
        con provideLocationInformation.

        `retry` = nº de reenvíos (1 por defecto); `retry_delay` = espera entre
        ellos (3 s medidos suficientes). `delay` retrasa el primer envío
        respecto al location_req.
        """
        cid = self.location_req(imsi, cell_id, plmn)
        if delay > 0:
            time.sleep(delay)
        for i in range(retry + 1):
            if i:
                time.sleep(retry_delay)
            self.lpp_request_location(imsi, cid, methods=methods)
        return cid
