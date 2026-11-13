"""
Transactional outbox (ARQ-01, "Cómo se publica un evento").

La Lambda NUNCA llama a EventBridge directamente. Guarda el cambio y el evento en la
misma TransactWriteItems: si una de las dos escrituras falla, no se guarda ninguna.
El ítem de outbox va en la misma tabla con:

    tenant_id = <tenantId>
    sk        = EVENTO#<eventId>
    source, detailType, detail (JSON en texto), ttl (7 días)

Antes de escribir, cada evento se valida contra su JSON Schema de nubeduca-contratos
(lib/esquemas.py). Si alguno no cumple, se lanza EventoInvalido y no se guarda nada.

Después, el Stream de la tabla lo lleva al bus: con EventBridge Pipes en AWS, o con la
Lambda relay/stream_a_bus.py (ver serverless.yml, parámetro `outbox`).
"""

import json
import time
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from lib import config, esquemas, ids, logs
from lib import tabla as t

TTL_EVENTO_SEGUNDOS = 7 * 24 * 60 * 60


def ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def nuevo_evento(
    detail_type: str,
    *,
    tenant_id: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    actor_id: str,
    data: dict,
    course_id: str | None = None,
    traceparent: str | None = None,
    source: str | None = None,
) -> dict:
    """Arma el `detail` {metadata, data} del contrato y lo devuelve listo para guardar."""
    metadata = {
        "eventId": ids.ulid(),
        "tenantId": tenant_id,
        "occurredAt": ahora_iso(),
        "aggregateType": aggregate_type,
        "aggregateId": aggregate_id,
        "aggregateVersion": int(aggregate_version),
        "actorId": actor_id,
    }
    if course_id:
        metadata["courseId"] = course_id
    if traceparent:
        metadata["traceparent"] = traceparent
    return {
        "source": source or config.SOURCE,
        "detailType": detail_type,
        "detail": {"metadata": metadata, "data": data},
    }


def item_outbox(evento: dict) -> dict:
    """Ítem de DynamoDB para el evento (lo que lee el Stream)."""
    meta = evento["detail"]["metadata"]
    return {
        "tenant_id": meta["tenantId"],
        "sk": t.sk_evento(meta["eventId"]),
        "entidad": "EVENTO",
        "source": evento["source"],
        "detailType": evento["detailType"],
        "detail": json.dumps(evento["detail"], ensure_ascii=False, separators=(",", ":")),
        "ttl": int(time.time()) + TTL_EVENTO_SEGUNDOS,
    }


def guardar(cambios: list[dict], eventos: list[dict]) -> None:
    """
    Guarda los cambios de estado y sus eventos en una sola transacción.

    `cambios` usa el formato de TransactWriteItems SIN TableName, por ejemplo:
        {"Put": {"Item": {...}, "ConditionExpression": "attribute_not_exists(sk)"}}
        {"Update": {"Key": {...}, "UpdateExpression": "...", ...}}
    Máximo 100 operaciones por transacción (límite de DynamoDB).
    """
    for evento in eventos:  # contrato primero: si un evento no cumple, no se escribe nada
        try:
            esquemas.validar(evento["detailType"], evento["detail"])
        except esquemas.EventoInvalido as exc:
            logs.error(mensaje="Evento no cumple su contrato", detailType=exc.detail_type, errores=exc.errores)
            raise
    nombre = t.tabla().name
    operaciones = []
    for cambio in cambios:
        (accion, cuerpo), = cambio.items()
        operaciones.append({accion: {"TableName": nombre, **cuerpo}})
    for evento in eventos:
        operaciones.append(
            {
                "Put": {
                    "TableName": nombre,
                    "Item": item_outbox(evento),
                    "ConditionExpression": "attribute_not_exists(sk)",
                }
            }
        )
    try:
        t.cliente().transact_write_items(TransactItems=operaciones)
    except ClientError as exc:
        logs.error(mensaje="No se pudo guardar el cambio con su evento", error=str(exc))
        raise
    for evento in eventos:
        meta = evento["detail"]["metadata"]
        logs.info(
            mensaje="Evento guardado en outbox",
            detailType=evento["detailType"],
            eventId=meta["eventId"],
            tenantId=meta["tenantId"],
        )
