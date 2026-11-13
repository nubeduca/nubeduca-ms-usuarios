"""
Consumo de eventos con las reglas de ARQ-01 (basado en ms-notificaciones de Dev 6):

- Lector tolerante: solo se leen los campos que se usan; no se valida contra el esquema.
- Idempotencia: INBOX#<eventId> se escribe con attribute_not_exists en la misma
  TransactWriteItems que el cambio. Si ya existía, el evento se descarta.
- Orden: AGG#<tipo>#<id> guarda la última aggregateVersion; si llega una igual o menor,
  el evento es viejo y se descarta.
- Fallos parciales: manejar_lote devuelve batchItemFailures; solo se reintenta el mensaje
  que falló, y tras 5 intentos va a la DLQ.

Cada servicio define un diccionario {detail-type: reaccion}. Una reacción recibe el
Evento y devuelve la lista de escrituras (formato de outbox.guardar) que debe aplicar.
"""

import json
import time
from dataclasses import dataclass

from botocore.exceptions import ClientError

from lib import logs
from lib import tabla as t
from lib.outbox import ahora_iso

TTL_INBOX_SEGUNDOS = 7 * 24 * 60 * 60


@dataclass
class Evento:
    detail_type: str
    source: str
    event_id: str
    tenant_id: str
    occurred_at: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    actor_id: str | None
    course_id: str | None
    data: dict


def leer_mensaje(cuerpo: str) -> Evento:
    """
    Convierte el body de SQS en un Evento. Acepta el evento completo de EventBridge
    (regla del bus → SQS) o solo su `detail` (tópico SNS FIFO con raw delivery).
    """
    mensaje = json.loads(cuerpo)
    if "detail" in mensaje:
        detail_type = mensaje.get("detail-type", "")
        source = mensaje.get("source", "")
        detail = mensaje["detail"]
    else:
        detail = mensaje
        detail_type = mensaje.get("detailType") or mensaje.get("detail-type") or ""
        source = mensaje.get("source", "")
    if isinstance(detail, str):
        detail = json.loads(detail)
    meta = detail["metadata"]
    return Evento(
        detail_type=detail_type,
        source=source,
        event_id=meta["eventId"],
        tenant_id=meta["tenantId"],
        occurred_at=meta["occurredAt"],
        aggregate_type=meta["aggregateType"],
        aggregate_id=meta["aggregateId"],
        aggregate_version=int(meta["aggregateVersion"]),
        actor_id=meta.get("actorId"),
        course_id=meta.get("courseId"),
        data=detail.get("data") or {},
    )


def _motivo_descarte(evento: Evento) -> str | None:
    tabla = t.tabla()
    clave_inbox = {"tenant_id": evento.tenant_id, "sk": t.sk_inbox(evento.event_id)}
    if tabla.get_item(Key=clave_inbox).get("Item"):
        return "DUPLICADO"
    clave_agg = {
        "tenant_id": evento.tenant_id,
        "sk": t.sk_agregado(evento.aggregate_type, evento.aggregate_id),
    }
    agregado = tabla.get_item(Key=clave_agg).get("Item")
    if agregado and int(agregado["version"]) >= evento.aggregate_version:
        return "VIEJO"
    return None


def _confirmar(evento: Evento, escrituras: list) -> bool:
    """TransactWriteItems: inbox + versión + cambios. False si otro proceso ganó la carrera."""
    nombre = t.tabla().name
    operaciones = [
        {
            "Put": {
                "TableName": nombre,
                "Item": {
                    "tenant_id": evento.tenant_id,
                    "sk": t.sk_inbox(evento.event_id),
                    "entidad": "INBOX",
                    "detailType": evento.detail_type,
                    "procesadoEn": ahora_iso(),
                    "ttl": int(time.time()) + TTL_INBOX_SEGUNDOS,
                },
                "ConditionExpression": "attribute_not_exists(sk)",
            }
        },
        {
            "Put": {
                "TableName": nombre,
                "Item": {
                    "tenant_id": evento.tenant_id,
                    "sk": t.sk_agregado(evento.aggregate_type, evento.aggregate_id),
                    "entidad": "AGREGADO",
                    "version": evento.aggregate_version,
                },
                "ConditionExpression": "attribute_not_exists(sk) OR version < :v",
                "ExpressionAttributeValues": {":v": evento.aggregate_version},
            }
        },
    ]
    for escritura in escrituras:
        (accion, cuerpo), = escritura.items()
        operaciones.append({accion: {"TableName": nombre, **cuerpo}})
    try:
        t.cliente().transact_write_items(TransactItems=operaciones)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            return False
        raise


def procesar(evento: Evento, reacciones: dict) -> str:
    """Aplica la reacción del detail-type. Devuelve qué pasó (para logs y pruebas)."""
    reaccion = reacciones.get(evento.detail_type)
    if reaccion is None:
        logs.info(mensaje="Evento ignorado", detailType=evento.detail_type, eventId=evento.event_id)
        return "IGNORADO"
    motivo = _motivo_descarte(evento)
    if motivo:
        logs.info(mensaje="Evento descartado", motivo=motivo, eventId=evento.event_id)
        return motivo
    escrituras = reaccion(evento) or []
    if not _confirmar(evento, escrituras):
        logs.info(mensaje="Evento descartado", motivo="CARRERA", eventId=evento.event_id)
        return "DUPLICADO"
    logs.info(
        mensaje="Evento procesado",
        detailType=evento.detail_type,
        eventId=evento.event_id,
        tenantId=evento.tenant_id,
    )
    return "PROCESADO"


def manejar_lote(event: dict, reacciones: dict) -> dict:
    """Handler de SQS con partial batch response."""
    fallidos = []
    for record in event.get("Records", []):
        try:
            procesar(leer_mensaje(record["body"]), reacciones)
        except Exception as exc:  # noqa: BLE001 - vuelve a la cola y luego a la DLQ
            logs.error(mensaje="Error procesando mensaje", messageId=record.get("messageId"), error=str(exc))
            fallidos.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": fallidos}
