"""
Relay del outbox: DynamoDB Stream → bus `lms` de EventBridge.

Se usa cuando el parámetro `outbox` es `lambda` (por defecto): en LocalStack, donde
EventBridge Pipes puede no estar disponible, y en AWS si el LabRole no permite crear el
Pipe. Con `outbox: pipe`, este Lambda queda apagado y el Pipe hace el mismo trabajo.

Solo procesa INSERT de ítems con sk = EVENTO#... (el filtro también está en serverless.yml).
Si EventBridge rechaza alguna entrada, lanza error: el Stream reintenta el lote completo
y los consumidores descartan los duplicados con su inbox.
"""

import boto3

from lib import config, logs

_cliente = None


def _events():
    global _cliente
    if _cliente is None:
        _cliente = boto3.client("events")
    return _cliente


def reiniciar() -> None:
    global _cliente
    _cliente = None


def _entrada(record: dict) -> dict | None:
    if record.get("eventName") != "INSERT":
        return None
    imagen = (record.get("dynamodb") or {}).get("NewImage") or {}
    sk = (imagen.get("sk") or {}).get("S", "")
    if not sk.startswith("EVENTO#"):
        return None
    return {
        "Source": imagen["source"]["S"],
        "DetailType": imagen["detailType"]["S"],
        "Detail": imagen["detail"]["S"],
        "EventBusName": config.bus_nombre(),
    }


def publicar(records: list[dict]) -> int:
    """Publica en el bus los eventos de outbox de una lista de registros del Stream."""
    entradas = [e for e in (_entrada(r) for r in records) if e]
    for i in range(0, len(entradas), 10):  # PutEvents acepta hasta 10 entradas
        lote = entradas[i:i + 10]
        resultado = _events().put_events(Entries=lote)
        if resultado.get("FailedEntryCount"):
            logs.error(mensaje="EventBridge rechazó eventos", detalle=resultado.get("Entries"))
            raise RuntimeError("PutEvents falló; el Stream reintentará el lote")
        for entrada in lote:
            logs.info(mensaje="Evento publicado en el bus", detailType=entrada["DetailType"])
    return len(entradas)


def lambda_handler(event, context):
    return {"publicados": publicar(event.get("Records", []))}
