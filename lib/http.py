"""Respuestas HTTP (integración lambda-proxy) con el formato de error del contrato."""

import json

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
}


def respuesta(status: int, cuerpo) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS},
        "body": json.dumps(cuerpo, ensure_ascii=False, default=str),
    }


def error(event: dict, status: int, code: str, message: str) -> dict:
    request_id = ((event or {}).get("requestContext") or {}).get("requestId")
    cuerpo = {"code": code, "message": message}
    if request_id:
        cuerpo["requestId"] = request_id
    return respuesta(status, cuerpo)


def no_autenticado(event: dict) -> dict:
    return error(event, 401, "NO_AUTENTICADO", "Falta el token o no es válido.")


def sin_permiso(event: dict) -> dict:
    return error(event, 403, "SIN_PERMISO", "No tienes permisos para esta operación.")


def datos_invalidos(event: dict, message: str = "Los datos enviados no son válidos.") -> dict:
    return error(event, 400, "DATOS_INVALIDOS", message)


def leer_json(event: dict):
    cuerpo = (event or {}).get("body")
    if cuerpo is None:
        return None
    try:
        return json.loads(cuerpo) if isinstance(cuerpo, str) else cuerpo
    except json.JSONDecodeError:
        return None
