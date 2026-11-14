"""
Usuario de la petición. El tenant y el rol salen SIEMPRE del token, nunca del body.

- cognito: API Gateway valida el ID token con el authorizer de Cognito (AUTH-01) y deja
  los claims en requestContext.authorizer.claims (sub, custom:tenant_id, custom:role).
- mock: acepta "Bearer mock.<tenant>.<ROL>.<userId>", el token del frontend en modo demo.
  Solo para desarrollo, mientras AUTH-01 no entregue el User Pool.
"""

from dataclasses import dataclass

from lib import config


@dataclass
class Usuario:
    tenant_id: str
    user_id: str
    rol: str
    # Solo con Cognito (claims del ID token). En modo mock el token no los trae.
    email: str | None = None
    given_name: str | None = None
    family_name: str | None = None


def _valido(tenant_id, user_id, rol) -> bool:
    return tenant_id in config.TENANTS and rol in config.ROLES and bool(user_id)


def usuario(event: dict) -> Usuario | None:
    event = event or {}
    extras = {}
    if config.auth_mode() == "mock":
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        valor = headers.get("authorization", "")
        if not valor.startswith("Bearer "):
            return None
        partes = valor[len("Bearer "):].strip().split(".")
        if len(partes) != 4 or partes[0] != "mock":
            return None
        _, tenant_id, rol, user_id = partes
    else:
        claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
        tenant_id = claims.get("custom:tenant_id")
        rol = claims.get("custom:role")
        user_id = claims.get("sub")
        extras = {"email": claims.get("email"), "given_name": claims.get("given_name"),
                  "family_name": claims.get("family_name")}
    return Usuario(tenant_id, user_id, rol, **extras) if _valido(tenant_id, user_id, rol) else None
