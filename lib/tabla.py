"""
Acceso a la tabla única del servicio: PK tenant_id (HASH) + sk (RANGE).

Prefijos de sk reservados por la plantilla:
- EVENTO#<eventId>  ítem de outbox (lo lee el Stream y lo manda al bus).
- INBOX#<eventId>   evento ya procesado por un consumidor (idempotencia).
- AGG#<tipo>#<id>   última aggregateVersion vista de otro servicio (orden).
Cada servicio agrega los suyos (por ejemplo FILE#<fileId>, MODULE#<moduleId>).
"""

import boto3

from lib import config

_recurso = None


def tabla():
    global _recurso
    if _recurso is None:
        _recurso = boto3.resource("dynamodb")
    return _recurso.Table(config.tabla_nombre())


def cliente():
    return tabla().meta.client


def reiniciar() -> None:
    """Solo para pruebas: fuerza a crear el cliente de nuevo."""
    global _recurso
    _recurso = None


def sk_evento(event_id: str) -> str:
    return f"EVENTO#{event_id}"


def sk_inbox(event_id: str) -> str:
    return f"INBOX#{event_id}"


def sk_agregado(tipo: str, agregado_id: str) -> str:
    return f"AGG#{tipo}#{agregado_id}"
