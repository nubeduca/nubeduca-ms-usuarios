"""
Validación del `detail` de un evento contra su JSON Schema (Draft-04) de nubeduca-contratos.

Diagrama de arquitectura, paso 4: "la Lambda escribe el cambio y, en la misma transacción,
el evento validado contra su esquema". outbox.guardar() llama a validar() antes de escribir:
si el evento no cumple su contrato, no se guarda nada.

- Los esquemas viven en contratos/esquemas/ (copia de nubeduca-contratos/eventos/esquemas,
  se actualiza con scripts/copiar_esquemas.py) y se despliegan con la Lambda.
- Validador propio, sin dependencias: cubre lo que usan los esquemas del catálogo
  (type, required, properties, additionalProperties, enum, pattern, minLength, maxLength,
  minimum, maximum, items, minItems, maxItems y format date-time). Así no hay que empaquetar
  librerías nativas en Lambda.
- Si no existe esquema para el detail-type, se rechaza: ningún servicio publica un evento
  que no esté en el catálogo.
"""

import json
import os
import re
from functools import lru_cache
from pathlib import Path

DIR_POR_DEFECTO = Path(__file__).resolve().parents[1] / "contratos" / "esquemas"
FECHA_HORA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
TIPOS = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
}


class EventoInvalido(ValueError):
    """El evento no cumple su contrato. `errores` trae el detalle de cada campo."""

    def __init__(self, detail_type: str, errores: list[str]):
        self.detail_type = detail_type
        self.errores = errores
        super().__init__(f"{detail_type} no cumple su esquema: {'; '.join(errores)}")


def _directorio() -> Path:
    return Path(os.environ.get("ESQUEMAS_DIR", DIR_POR_DEFECTO))


@lru_cache(maxsize=64)
def _cargar(directorio: str, detail_type: str) -> dict | None:
    ruta = Path(directorio) / f"{detail_type}.json"
    if not ruta.is_file():
        return None
    return json.loads(ruta.read_text(encoding="utf-8"))


def _es_tipo(valor, tipo: str) -> bool:
    if tipo == "integer":
        return isinstance(valor, int) and not isinstance(valor, bool)
    if tipo == "number":
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)
    if tipo == "null":
        return valor is None
    esperado = TIPOS.get(tipo)
    return esperado is not None and isinstance(valor, esperado)


def _revisar(valor, esquema: dict, ruta: str, errores: list[str]) -> None:
    tipo = esquema.get("type")
    if tipo is not None:
        tipos = tipo if isinstance(tipo, list) else [tipo]
        if not any(_es_tipo(valor, t) for t in tipos):
            errores.append(f"{ruta}: debe ser {' o '.join(tipos)}")
            return

    if "enum" in esquema and valor not in esquema["enum"]:
        errores.append(f"{ruta}: debe ser uno de {esquema['enum']}")

    if isinstance(valor, str):
        if "minLength" in esquema and len(valor) < esquema["minLength"]:
            errores.append(f"{ruta}: mínimo {esquema['minLength']} caracteres")
        if "maxLength" in esquema and len(valor) > esquema["maxLength"]:
            errores.append(f"{ruta}: máximo {esquema['maxLength']} caracteres")
        if "pattern" in esquema and not re.search(esquema["pattern"], valor):
            errores.append(f"{ruta}: no cumple el patrón {esquema['pattern']}")
        if esquema.get("format") == "date-time" and not FECHA_HORA.match(valor):
            errores.append(f"{ruta}: debe ser fecha y hora ISO 8601")

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if "minimum" in esquema and valor < esquema["minimum"]:
            errores.append(f"{ruta}: mínimo {esquema['minimum']}")
        if "maximum" in esquema and valor > esquema["maximum"]:
            errores.append(f"{ruta}: máximo {esquema['maximum']}")

    if isinstance(valor, dict):
        propiedades = esquema.get("properties", {})
        for campo in esquema.get("required", []):
            if campo not in valor:
                errores.append(f"{ruta}.{campo}: es obligatorio")
        if esquema.get("additionalProperties") is False:
            for campo in valor:
                if campo not in propiedades:
                    errores.append(f"{ruta}.{campo}: campo no permitido por el contrato")
        for campo, sub in propiedades.items():
            if campo in valor:
                _revisar(valor[campo], sub, f"{ruta}.{campo}", errores)

    if isinstance(valor, list):
        if "minItems" in esquema and len(valor) < esquema["minItems"]:
            errores.append(f"{ruta}: mínimo {esquema['minItems']} elementos")
        if "maxItems" in esquema and len(valor) > esquema["maxItems"]:
            errores.append(f"{ruta}: máximo {esquema['maxItems']} elementos")
        if isinstance(esquema.get("items"), dict):
            for i, item in enumerate(valor):
                _revisar(item, esquema["items"], f"{ruta}[{i}]", errores)


def errores(detail_type: str, detail: dict) -> list[str]:
    """Lista de errores (vacía si el detail cumple su esquema)."""
    esquema = _cargar(str(_directorio()), detail_type)
    if esquema is None:
        return [f"no hay esquema para {detail_type} en {_directorio()}"]
    encontrados: list[str] = []
    _revisar(detail, esquema, "detail", encontrados)
    return encontrados


def validar(detail_type: str, detail: dict) -> None:
    """Lanza EventoInvalido si el detail no cumple su contrato."""
    encontrados = errores(detail_type, detail)
    if encontrados:
        raise EventoInvalido(detail_type, encontrados)
