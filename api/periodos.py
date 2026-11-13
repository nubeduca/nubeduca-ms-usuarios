"""
Periodos académicos (ACA-01), según apis/ms-usuarios.openapi.yaml de nubeduca-contratos.

GET  /terms                 (STUDENT, TEACHER, ADMIN)  periodos del tenant, el más reciente primero
POST /terms                 (ADMIN)                    crea un periodo en PLANNED
POST /terms/{termId}/start  (ADMIN)                    PLANNED -> ACTIVE, publica lms.users.term.started.v1

Ítems en la tabla:
- TERM#<termId>      el periodo. termId es un ULID, así que ordenar por sk es ordenar por creación.
- TERMNAME#<name>    reserva el nombre dentro del tenant (409 si se repite).
"""

import base64
import binascii
import json
import re
from datetime import date

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from lib import auth, http, ids, outbox
from lib import tabla as t

DETAIL_TYPE_INICIADO = "lms.users.term.started.v1"
TERM_ID = re.compile(r"^trm_[0-9A-HJKMNP-TV-Z]{26}$")
FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LIMITE_DEFECTO, LIMITE_MAXIMO = 20, 100


def sk_periodo(term_id: str) -> str:
    return f"TERM#{term_id}"


def sk_nombre(nombre: str) -> str:
    return f"TERMNAME#{nombre}"


def a_respuesta(item: dict) -> dict:
    return {k: item[k] for k in ("termId", "name", "startsOn", "endsOn", "status")}


def _fecha(valor) -> date | None:
    if not isinstance(valor, str) or not FECHA.match(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _validar(cuerpo: dict) -> str | None:
    nombre = cuerpo.get("name")
    if not isinstance(nombre, str) or not nombre.strip() or len(nombre.strip()) > 40:
        return "name es obligatorio y tiene hasta 40 caracteres."
    inicio, fin = _fecha(cuerpo.get("startsOn")), _fecha(cuerpo.get("endsOn"))
    if inicio is None or fin is None:
        return "startsOn y endsOn son obligatorios y tienen formato AAAA-MM-DD."
    if fin < inicio:
        return "endsOn no puede ser anterior a startsOn."
    return None


def crear(event, usuario):
    if usuario.rol != "ADMIN":
        return http.sin_permiso(event)
    cuerpo = http.leer_json(event)
    if not isinstance(cuerpo, dict):
        return http.datos_invalidos(event)
    problema = _validar(cuerpo)
    if problema:
        return http.datos_invalidos(event, problema)

    nombre = cuerpo["name"].strip()
    term_id = ids.nuevo("trm")
    periodo = {
        "termId": term_id,
        "name": nombre,
        "startsOn": cuerpo["startsOn"],
        "endsOn": cuerpo["endsOn"],
        "status": "PLANNED",
        "version": 1,
        "createdBy": usuario.user_id,
        "createdAt": outbox.ahora_iso(),
    }
    tenant_id = usuario.tenant_id  # del token, nunca del body
    try:
        outbox.guardar(
            cambios=[
                {"Put": {"Item": {"tenant_id": tenant_id, "sk": sk_periodo(term_id), "entidad": "TERM", **periodo},
                         "ConditionExpression": "attribute_not_exists(sk)"}},
                {"Put": {"Item": {"tenant_id": tenant_id, "sk": sk_nombre(nombre), "entidad": "TERMNAME",
                                  "termId": term_id},
                         "ConditionExpression": "attribute_not_exists(sk)"}},
            ],
            eventos=[],  # crear no publica; el contrato solo emite al iniciar
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            return http.error(event, 409, "PERIODO_DUPLICADO", f"Ya existe un periodo llamado {nombre}.")
        raise
    return http.respuesta(201, a_respuesta(periodo))


def iniciar(event, usuario):
    if usuario.rol != "ADMIN":
        return http.sin_permiso(event)
    term_id = ((event or {}).get("pathParameters") or {}).get("termId") or ""
    no_existe = http.error(event, 404, "PERIODO_NO_ENCONTRADO", "No existe el periodo en este tenant.")
    if not TERM_ID.match(term_id):
        return no_existe
    clave = {"tenant_id": usuario.tenant_id, "sk": sk_periodo(term_id)}
    item = t.tabla().get_item(Key=clave, ConsistentRead=True).get("Item")
    if item is None:
        return no_existe
    if item["status"] != "PLANNED":
        return http.error(event, 409, "PERIODO_NO_PLANIFICADO", f"El periodo está en {item['status']}, no en PLANNED.")

    version = int(item["version"]) + 1
    evento = outbox.nuevo_evento(
        DETAIL_TYPE_INICIADO,
        tenant_id=usuario.tenant_id,
        aggregate_type="term",
        aggregate_id=term_id,
        aggregate_version=version,
        actor_id=usuario.user_id,
        data={"termId": term_id, "name": item["name"], "startsOn": item["startsOn"], "endsOn": item["endsOn"]},
    )
    try:
        outbox.guardar(
            cambios=[{"Update": {
                "Key": clave,
                "UpdateExpression": "SET #s = :activo, #v = :nueva, startedAt = :ahora, startedBy = :actor",
                # La condición evita que dos llamadas simultáneas publiquen dos veces.
                "ConditionExpression": "#s = :planificado AND #v = :actual",
                "ExpressionAttributeNames": {"#s": "status", "#v": "version"},
                "ExpressionAttributeValues": {":activo": "ACTIVE", ":planificado": "PLANNED", ":nueva": version,
                                              ":actual": version - 1, ":ahora": outbox.ahora_iso(),
                                              ":actor": usuario.user_id},
            }}],
            eventos=[evento],
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            return http.error(event, 409, "PERIODO_NO_PLANIFICADO", "El periodo cambió de estado; vuelve a intentarlo.")
        raise
    return http.respuesta(200, a_respuesta(item | {"status": "ACTIVE"}))


def _codificar(clave: dict | None) -> str | None:
    if not clave:
        return None
    return base64.urlsafe_b64encode(json.dumps(clave, separators=(",", ":")).encode()).decode()


def _decodificar(token: str, tenant_id: str) -> dict | None:
    try:
        clave = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    valido = (isinstance(clave, dict) and set(clave) == {"tenant_id", "sk"} and clave["tenant_id"] == tenant_id
              and isinstance(clave["sk"], str) and clave["sk"].startswith("TERM#"))
    return clave if valido else None


def listar(event, usuario):
    params = (event or {}).get("queryStringParameters") or {}
    try:
        limite = int(params.get("limit", LIMITE_DEFECTO))
    except (TypeError, ValueError):
        return http.datos_invalidos(event, "limit debe ser un número entre 1 y 100.")
    if not 1 <= limite <= LIMITE_MAXIMO:
        return http.datos_invalidos(event, "limit debe ser un número entre 1 y 100.")
    consulta = {
        "KeyConditionExpression": Key("tenant_id").eq(usuario.tenant_id) & Key("sk").begins_with("TERM#"),
        "ScanIndexForward": False,
        "Limit": limite,
    }
    if params.get("nextToken"):
        inicio = _decodificar(params["nextToken"], usuario.tenant_id)
        if inicio is None:
            return http.datos_invalidos(event, "nextToken no es válido.")
        consulta["ExclusiveStartKey"] = inicio
    resultado = t.tabla().query(**consulta)
    return http.respuesta(200, {
        "items": [a_respuesta(i) for i in resultado.get("Items", [])],
        "nextToken": _codificar(resultado.get("LastEvaluatedKey")),
    })


def lambda_handler(event, context):
    usuario = auth.usuario(event)
    if usuario is None:
        return http.no_autenticado(event)
    metodo = (event or {}).get("httpMethod", "GET")
    if metodo == "GET":
        return listar(event, usuario)
    if ((event or {}).get("pathParameters") or {}).get("termId") is not None:
        return iniciar(event, usuario)
    return crear(event, usuario)
