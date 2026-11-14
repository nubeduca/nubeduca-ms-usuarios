"""
Usuarios y perfiles (ACA-02), según apis/ms-usuarios.openapi.yaml de nubeduca-contratos.

POST  /users/me            (todos)            crea mi perfil en el primer login; publica user.provisioned
GET   /users/me            (todos)            mi perfil (404 si todavía no existe)
PATCH /users/me            (todos)            solo locale y timeZone
GET   /users               (TEACHER, ADMIN)   ?role=&institutionalCode=&limit=&nextToken=
GET   /users/{userId}      (TEACHER, ADMIN)

Ítems en la tabla:
- USER#<userId>          el perfil
- USERCODE#<código>      código institucional -> userId (único en el tenant)
- ROSTER#<email>         padrón: nombre y código que la institución ya conoce de ese correo.
                         Lo carga scripts/poblar_datos.py; el primer login lo usa para armar el perfil.

El tenant, el rol y el userId salen SIEMPRE del token. El correo y el nombre salen de los
claims de Cognito o, si faltan, del padrón. POST /users/me es idempotente: si el perfil ya
existía responde 200 y no publica nada.
"""

import base64
import binascii
import json
import re

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from lib import auth, config, http, outbox
from lib import tabla as t

PROVISIONADO = "lms.users.user.provisioned.v1"
USER_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
CODIGO = re.compile(r"^[A-Za-z0-9-]{3,20}$")
LOCALE_DEFECTO, ZONA_DEFECTO = "es-PE", "America/Lima"
LIMITE_DEFECTO, LIMITE_MAXIMO = 20, 100
CAMPOS = ("userId", "role", "institutionalCode", "givenName", "familyName", "email", "locale", "timeZone",
          "createdAt")


def sk_usuario(user_id: str = "") -> str:
    return f"USER#{user_id}"


def sk_codigo(codigo: str) -> str:
    return f"USERCODE#{codigo}"


def sk_padron(email: str) -> str:
    return f"ROSTER#{email.strip().lower()}"


def a_usuario(item: dict) -> dict:
    return {k: item.get(k) for k in CAMPOS}  # institutionalCode puede ser null (nullable en el contrato)


def _leer(tenant_id: str, user_id: str, consistente: bool = False) -> dict | None:
    clave = {"tenant_id": tenant_id, "sk": sk_usuario(user_id)}
    return t.tabla().get_item(Key=clave, ConsistentRead=consistente).get("Item")


def _no_existe(event) -> dict:
    return http.error(event, 404, "USUARIO_NO_ENCONTRADO", "No existe el usuario en este tenant.")


# --- Mi perfil --------------------------------------------------------------------


def _nombre_de_email(email: str) -> tuple[str, str]:
    partes = re.split(r"[._-]+", email.split("@")[0])
    partes = [p.capitalize() for p in partes if p] or ["Usuario"]
    return partes[0], " ".join(partes[1:])


def _armar_perfil(usuario) -> dict:
    """Perfil del primer login: token primero, luego el padrón, luego el correo."""
    email = (usuario.email or f"{usuario.user_id}@{usuario.tenant_id}.demo").strip().lower()
    padron = t.tabla().get_item(Key={"tenant_id": usuario.tenant_id, "sk": sk_padron(email)}).get("Item") or {}
    nombre_correo, apellido_correo = _nombre_de_email(email)
    codigo = padron.get("institutionalCode")
    return {
        "userId": usuario.user_id,
        "role": usuario.rol,
        "institutionalCode": codigo if isinstance(codigo, str) and CODIGO.match(codigo) else None,
        "givenName": usuario.given_name or padron.get("givenName") or nombre_correo,
        "familyName": usuario.family_name or padron.get("familyName") or apellido_correo,
        "email": email,
        "locale": padron.get("locale") or LOCALE_DEFECTO,
        "timeZone": padron.get("timeZone") or ZONA_DEFECTO,
        "createdAt": outbox.ahora_iso(),
    }


def provisionar(event, usuario):
    existente = _leer(usuario.tenant_id, usuario.user_id)
    if existente:
        return http.respuesta(200, a_usuario(existente))

    perfil = _armar_perfil(usuario)
    data = {"userId": perfil["userId"], "role": perfil["role"]}
    if perfil["institutionalCode"]:
        data["institutionalCode"] = perfil["institutionalCode"]
    evento = outbox.nuevo_evento(
        PROVISIONADO,
        tenant_id=usuario.tenant_id,
        aggregate_type="user",
        aggregate_id=usuario.user_id,
        aggregate_version=1,
        actor_id=usuario.user_id,
        data=data,
    )
    cambios = [{"Put": {"Item": {"tenant_id": usuario.tenant_id, "sk": sk_usuario(usuario.user_id),
                                 "entidad": "USER", "version": 1, **perfil},
                        "ConditionExpression": "attribute_not_exists(sk)"}}]
    if perfil["institutionalCode"]:
        cambios.append({"Put": {"Item": {"tenant_id": usuario.tenant_id, "sk": sk_codigo(perfil["institutionalCode"]),
                                         "entidad": "USERCODE", "userId": usuario.user_id},
                                "ConditionExpression": "attribute_not_exists(sk) OR userId = :u",
                                "ExpressionAttributeValues": {":u": usuario.user_id}}})
    try:
        outbox.guardar(cambios=cambios, eventos=[evento])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        existente = _leer(usuario.tenant_id, usuario.user_id, consistente=True)
        if existente:  # otra llamada del mismo login ganó la carrera
            return http.respuesta(200, a_usuario(existente))
        # El código ya es de otro usuario: se crea el perfil sin código antes que dejarlo sin entrar.
        perfil["institutionalCode"] = None
        data.pop("institutionalCode", None)
        evento["detail"]["data"] = data
        outbox.guardar(cambios=cambios[:1], eventos=[evento])
    return http.respuesta(201, perfil)


def obtener_mio(event, usuario):
    item = _leer(usuario.tenant_id, usuario.user_id)
    return http.respuesta(200, a_usuario(item)) if item else _no_existe(event)


def editar_mio(event, usuario):
    cuerpo = http.leer_json(event)
    if not isinstance(cuerpo, dict) or not cuerpo or set(cuerpo) - {"locale", "timeZone"}:
        return http.datos_invalidos(event, "Solo se pueden cambiar locale y timeZone.")
    if "locale" in cuerpo and (not isinstance(cuerpo["locale"], str) or not LOCALE.match(cuerpo["locale"])):
        return http.datos_invalidos(event, "locale tiene la forma es-PE.")
    if "timeZone" in cuerpo and (not isinstance(cuerpo["timeZone"], str) or not 1 <= len(cuerpo["timeZone"]) <= 64):
        return http.datos_invalidos(event, "timeZone es una zona horaria IANA, por ejemplo America/Lima.")
    nombres = {f"#{k}": k for k in cuerpo}
    valores = {f":{k}": v for k, v in cuerpo.items()}
    try:
        resultado = t.tabla().update_item(
            Key={"tenant_id": usuario.tenant_id, "sk": sk_usuario(usuario.user_id)},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in cuerpo),
            ConditionExpression="attribute_exists(sk)",
            ExpressionAttributeNames=nombres,
            ExpressionAttributeValues=valores,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _no_existe(event)
        raise
    return http.respuesta(200, a_usuario(resultado["Attributes"]))


# --- Directorio (TEACHER, ADMIN) ----------------------------------------------------


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
              and isinstance(clave["sk"], str) and clave["sk"].startswith("USER#"))
    return clave if valido else None


def listar(event, usuario):
    params = (event or {}).get("queryStringParameters") or {}
    try:
        limite = int(params.get("limit", LIMITE_DEFECTO))
    except (TypeError, ValueError):
        limite = 0
    if not 1 <= limite <= LIMITE_MAXIMO:
        return http.datos_invalidos(event, "limit debe ser un número entre 1 y 100.")
    rol = params.get("role")
    if rol and rol not in config.ROLES:
        return http.datos_invalidos(event, "role debe ser STUDENT, TEACHER o ADMIN.")

    if params.get("institutionalCode"):
        codigo = t.tabla().get_item(Key={"tenant_id": usuario.tenant_id,
                                         "sk": sk_codigo(params["institutionalCode"].strip())}).get("Item")
        item = _leer(usuario.tenant_id, codigo["userId"]) if codigo else None
        encontrados = [item] if item and (not rol or item["role"] == rol) else []
        return http.respuesta(200, {"items": [a_usuario(i) for i in encontrados], "nextToken": None})

    clave = None
    if params.get("nextToken"):
        clave = _decodificar(params["nextToken"], usuario.tenant_id)
        if clave is None:
            return http.datos_invalidos(event, "nextToken no es válido.")
    # Con filtro, DynamoDB aplica Limit antes de filtrar: se sigue leyendo hasta llenar la página.
    encontrados = []
    while True:
        consulta = {"KeyConditionExpression": Key("tenant_id").eq(usuario.tenant_id) & Key("sk").begins_with("USER#"),
                    "Limit": limite - len(encontrados)}
        if rol:
            consulta["FilterExpression"] = Attr("role").eq(rol)
        if clave:
            consulta["ExclusiveStartKey"] = clave
        resultado = t.tabla().query(**consulta)
        encontrados += resultado.get("Items", [])
        clave = resultado.get("LastEvaluatedKey")
        if clave is None or len(encontrados) >= limite:
            break
    return http.respuesta(200, {"items": [a_usuario(i) for i in encontrados], "nextToken": _codificar(clave)})


def obtener(event, usuario, user_id: str):
    if not USER_ID.match(user_id or ""):
        return _no_existe(event)
    item = _leer(usuario.tenant_id, user_id)
    return http.respuesta(200, a_usuario(item)) if item else _no_existe(event)


def lambda_handler(event, context):
    usuario = auth.usuario(event)
    if usuario is None:
        return http.no_autenticado(event)
    metodo = (event or {}).get("httpMethod", "GET")
    recurso = (event or {}).get("resource") or ""
    if recurso.endswith("/me"):
        if metodo == "POST":
            return provisionar(event, usuario)
        if metodo == "PATCH":
            return editar_mio(event, usuario)
        return obtener_mio(event, usuario)
    if usuario.rol not in ("TEACHER", "ADMIN"):
        return http.sin_permiso(event)
    user_id = ((event or {}).get("pathParameters") or {}).get("userId")
    if user_id is not None:
        return obtener(event, usuario, user_id)
    return listar(event, usuario)
