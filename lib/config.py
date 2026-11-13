"""Configuración del servicio, leída de las variables de entorno que pone serverless.yml."""

import os

TENANTS = ("utec", "upc", "pucp")
ROLES = ("STUDENT", "TEACHER", "ADMIN")

# Cambia estos dos valores al copiar la plantilla (en este servicio: "ms-usuarios" y "lms.users").
SERVICIO = os.environ.get("SERVICIO", "ms-usuarios")
SOURCE = os.environ.get("EVENT_SOURCE", "lms.users")


def tabla_nombre() -> str:
    return os.environ.get("TABLA", "dev-t_usuarios")


def bus_nombre() -> str:
    return os.environ.get("EVENT_BUS", "lms")


def auth_mode() -> str:
    """cognito: claims del authorizer de API Gateway · mock: token de demo del frontend."""
    return os.environ.get("AUTH_MODE", "cognito").lower()
