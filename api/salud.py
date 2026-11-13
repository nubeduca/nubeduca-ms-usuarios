"""GET /health (pública). GATE-0 y REL-01 la llaman después de cada despliegue."""

from lib import config
from lib.http import respuesta


def lambda_handler(event, context):
    return respuesta(200, {"status": "ok", "service": config.SERVICIO, "version": "1.0.0"})
