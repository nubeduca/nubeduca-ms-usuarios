"""
GET /tenant (STUDENT, TEACHER, ADMIN): datos del tenant del token (ACA-01).

Los 3 tenants no se crean por la API: los crea scripts/poblar_datos.py, que publica
lms.users.tenant.created.v1 por outbox (catálogo: emittedBy poblar_datos.py).
"""

from lib import auth, http
from lib import tabla as t

SK_TENANT = "TENANT"

# Datos de cada institución. El tenantId es la clave de partición de todo Nubeduca.
TENANTS = {
    "utec": {"name": "Universidad de Ingeniería y Tecnología", "timeZone": "America/Lima", "locale": "es-PE"},
    "upc": {"name": "Universidad Peruana de Ciencias Aplicadas", "timeZone": "America/Lima", "locale": "es-PE"},
    "pucp": {"name": "Pontificia Universidad Católica del Perú", "timeZone": "America/Lima", "locale": "es-PE"},
}


def a_respuesta(item: dict) -> dict:
    return {
        "tenantId": item["tenant_id"],
        "name": item["name"],
        "timeZone": item["timeZone"],
        "locale": item["locale"],
    }


def lambda_handler(event, context):
    usuario = auth.usuario(event)
    if usuario is None:
        return http.no_autenticado(event)
    item = t.tabla().get_item(Key={"tenant_id": usuario.tenant_id, "sk": SK_TENANT}).get("Item")
    if item is None:
        return http.error(event, 404, "TENANT_NO_ENCONTRADO", "El tenant todavía no existe. Corre poblar_datos.py.")
    return http.respuesta(200, a_respuesta(item))
