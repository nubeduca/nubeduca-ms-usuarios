"""
Datos iniciales de ms-usuarios.

    python scripts/poblar_datos.py --stage dev            # tabla dev-t_usuarios
    TABLA=otra-tabla python scripts/poblar_datos.py

Usa las credenciales de AWS del entorno (en nubeduca-backend: source scripts/cuenta.sh).

1. Tenants (ACA-01): crea utec, upc y pucp y publica lms.users.tenant.created.v1 por
   outbox, uno por tenant. Es idempotente: si el tenant ya existe, no publica de nuevo.
2. Usuarios (ACA-02): pendiente. Los 30.000 perfiles NO escriben ítems EVENTO#, para no
   inundar el bus (ver README de la plantilla).
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from botocore.exceptions import ClientError  # noqa: E402

from api.tenant import SK_TENANT, TENANTS  # noqa: E402
from lib import outbox  # noqa: E402

DETAIL_TYPE_TENANT = "lms.users.tenant.created.v1"


def crear_tenants() -> dict[str, str]:
    """Devuelve {tenantId: "CREADO" | "YA_EXISTIA"}."""
    resultado = {}
    for tenant_id, datos in TENANTS.items():
        evento = outbox.nuevo_evento(
            DETAIL_TYPE_TENANT,
            tenant_id=tenant_id,
            aggregate_type="tenant",
            aggregate_id=tenant_id,
            aggregate_version=1,
            actor_id="system",
            data=dict(datos),
        )
        item = {"tenant_id": tenant_id, "sk": SK_TENANT, "entidad": "TENANT", "version": 1,
                "createdAt": evento["detail"]["metadata"]["occurredAt"], **datos}
        try:
            outbox.guardar(
                cambios=[{"Put": {"Item": item, "ConditionExpression": "attribute_not_exists(sk)"}}],
                eventos=[evento],
            )
            resultado[tenant_id] = "CREADO"
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            resultado[tenant_id] = "YA_EXISTIA"
    return resultado


def main():
    parser = argparse.ArgumentParser(description="Datos iniciales de ms-usuarios")
    parser.add_argument("--stage", help="usa la tabla <stage>-t_usuarios")
    args = parser.parse_args()
    if args.stage:
        os.environ["TABLA"] = f"{args.stage}-t_usuarios"
    if not os.environ.get("TABLA"):
        raise SystemExit("Pasa --stage o define TABLA.")
    for tenant_id, estado in crear_tenants().items():
        print(f"{tenant_id}: {estado}")


if __name__ == "__main__":
    main()
