"""Logs en el formato estándar de Nubeduca: {"tipo": "INFO" | "ERROR", "log_datos": {...}}."""

import json


def _log(nivel: str, **datos) -> None:
    print(json.dumps({"tipo": nivel, "log_datos": datos}, ensure_ascii=False, default=str))


def info(**datos) -> None:
    _log("INFO", **datos)


def error(**datos) -> None:
    _log("ERROR", **datos)
