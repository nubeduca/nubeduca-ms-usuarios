"""
Datos iniciales de ms-usuarios.

    python scripts/poblar_datos.py --stage dev                       # tabla dev-t_usuarios
    python scripts/poblar_datos.py --stage dev --escala 0.01         # 1 %: 150/100/50 para probar
    python scripts/poblar_datos.py --stage dev --eventos-demo        # + user.provisioned de los usuarios demo
    TABLA=otra-tabla python scripts/poblar_datos.py

Usa las credenciales de AWS del entorno (en nubeduca-backend: source scripts/cuenta.sh).

1. Tenants (ACA-01): crea utec, upc y pucp y publica lms.users.tenant.created.v1 por
   outbox, uno por tenant. Es idempotente: si el tenant ya existe, no publica de nuevo.
2. Usuarios (ACA-02): 30.000 perfiles (USER#), UTEC 15.000, UPC 10.000 y PUCP 5.000, con
   su código (USERCODE#). 90 % alumnos, 9 % docentes y 1 % admins. NO escriben ítems EVENTO#,
   para no inundar el bus. Los ids son fijos (uuid5): correrlo otra vez reescribe lo mismo.
   Incluye los 21 usuarios demo del frontend (mismos ids, nombres, correos y códigos que
   nubeduca-frontend/src/lib/mocks/fixtures/users.ts) con su padrón (ROSTER#<email>).
3. Periodo SEED (trm_0000000000000000000000SEED, ACTIVE): el mismo que carga el seed de
   ms-cursos, para crear cursos en él desde el frontend sin iniciar un periodo.
4. --eventos-demo: publica user.provisioned solo para los usuarios demo, así ms-cursos
   (importación CSV) y ms-notificaciones (preferencias) los conocen. Repetirlo no duplica:
   los consumidores descartan versiones ya vistas.
"""

import argparse
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from botocore.exceptions import ClientError  # noqa: E402

from api.periodos import sk_nombre, sk_periodo  # noqa: E402
from api.tenant import SK_TENANT, TENANTS  # noqa: E402
from api.usuarios import PROVISIONADO, sk_codigo, sk_padron, sk_usuario  # noqa: E402
from lib import outbox  # noqa: E402
from lib import tabla as t  # noqa: E402

DETAIL_TYPE_TENANT = "lms.users.tenant.created.v1"
REPARTO = {"utec": 15000, "upc": 10000, "pucp": 5000}
CREADO_EN = "2026-11-10T15:00:00Z"
ESPACIO = uuid.UUID("3a7c1e52-9b4d-4f6e-8a21-5eed0000a002")
TERM_SEED = "trm_" + "0" * 22 + "SEED"
NOMBRES = ("Lucia", "Mateo", "Valeria", "Diego", "Camila", "Sebastian", "Daniela", "Nicolas", "Sofia", "Joaquin",
           "Fernanda", "Gabriel", "Isabella", "Rodrigo", "Mariana", "Andres", "Paula", "Santiago", "Renata", "Bruno")
APELLIDOS = ("Garcia", "Flores", "Rodriguez", "Sanchez", "Ramirez", "Chavez", "Vargas", "Gutierrez", "Huaman",
             "Mamani", "Castro", "Ruiz", "Rojas", "Mendoza", "Paredes", "Salazar", "Quispe", "Torres", "Diaz", "Leon")
ETIQUETA = {"utec": "UTEC", "upc": "UPC", "pucp": "PUCP"}
ORDEN = ("utec", "upc", "pucp")
DEMO_IDS = {
    "utec": ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
             "33333333-3333-4333-8333-333333333333"),
    "upc": ("44444444-4444-4444-8444-444444444444", "55555555-5555-4555-8555-555555555555",
            "66666666-6666-4666-8666-666666666666"),
    "pucp": ("77777777-7777-4777-8777-777777777777", "88888888-8888-4888-8888-888888888888",
             "99999999-9999-4999-8999-999999999999"),
}
EXTRAS = (("Luis", "Quispe"), ("María", "Rojas"), ("Carlos", "Mendoza"), ("Elena", "Castillo"))


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


# --- Usuarios ---------------------------------------------------------------------


def usuarios_demo(tenant: str) -> list[dict]:
    """Los 7 usuarios demo por tenant del frontend (docente, Ana, admin y 4 alumnos más)."""
    indice = ORDEN.index(tenant)
    alumno, docente, admin = DEMO_IDS[tenant]
    codigo_ana = 202310457 + indice * 1000
    etiqueta = ETIQUETA[tenant]
    perfiles = [
        (docente, "TEACHER", f"DOC-{etiqueta}-001", "Docente", etiqueta, f"docente.demo@{tenant}.edu.pe"),
        (alumno, "STUDENT", str(codigo_ana), "Ana", "Torres", f"alumno.demo@{tenant}.edu.pe"),
        (admin, "ADMIN", None, "Admin", etiqueta, f"admin@{tenant}.edu.pe"),
    ]
    for n, (nombre, apellido) in enumerate(EXTRAS):
        user_id = f"10000000-0000-4000-8000-{indice * 100 + n + 1:012x}"
        perfiles.append((user_id, "STUDENT", str(codigo_ana + n + 1), nombre, apellido,
                         f"estudiante{n + 1}@{tenant}.edu.pe"))
    return [_perfil(*p) for p in perfiles]


def _perfil(user_id, rol, codigo, nombre, apellido, email) -> dict:
    return {"userId": user_id, "role": rol, "institutionalCode": codigo, "givenName": nombre,
            "familyName": apellido, "email": email, "locale": "es-PE", "timeZone": "America/Lima",
            "createdAt": CREADO_EN}


def _rol(n: int) -> str:
    resto = n % 100
    return "ADMIN" if resto == 0 else "TEACHER" if resto <= 9 else "STUDENT"


def usuario_generado(tenant: str, n: int) -> dict:
    rol = _rol(n)
    nombre, apellido = NOMBRES[n % len(NOMBRES)], APELLIDOS[(n // len(NOMBRES)) % len(APELLIDOS)]
    codigo = None if rol == "ADMIN" else (f"DOC-{n:06d}" if rol == "TEACHER" else f"A{n:07d}")
    email = f"{nombre}.{apellido}{n}@{tenant}.edu.pe".lower()
    return _perfil(str(uuid.uuid5(ESPACIO, f"{tenant}-usuario-{n}")), rol, codigo, nombre, apellido, email)


def _items(tenant: str, perfil: dict, demo: bool) -> list[dict]:
    items = [{"tenant_id": tenant, "sk": sk_usuario(perfil["userId"]), "entidad": "USER", "version": 1,
              "seed": True, **perfil}]
    if perfil["institutionalCode"]:
        items.append({"tenant_id": tenant, "sk": sk_codigo(perfil["institutionalCode"]), "entidad": "USERCODE",
                      "userId": perfil["userId"], "seed": True})
    if demo:
        items.append({"tenant_id": tenant, "sk": sk_padron(perfil["email"]), "entidad": "ROSTER", "seed": True,
                      **{k: perfil[k] for k in ("institutionalCode", "givenName", "familyName", "locale",
                                                 "timeZone")}})
    return items


def _periodo_seed(tenant: str) -> list[dict]:
    periodo = {"termId": TERM_SEED, "name": "SEED 2026-2", "startsOn": "2026-08-17", "endsOn": "2026-12-12",
               "status": "ACTIVE", "version": 2, "createdBy": "system", "createdAt": CREADO_EN}
    return [{"tenant_id": tenant, "sk": sk_periodo(TERM_SEED), "entidad": "TERM", "seed": True, **periodo},
            {"tenant_id": tenant, "sk": sk_nombre(periodo["name"]), "entidad": "TERMNAME", "termId": TERM_SEED,
             "seed": True}]


def poblar(reparto: dict[str, int] = REPARTO) -> dict[str, int]:
    """Escribe los perfiles (los demo incluidos en la cantidad). Devuelve {tenant: perfiles}."""
    escritos = {}
    with t.tabla().batch_writer(overwrite_by_pkeys=["tenant_id", "sk"]) as lote:
        for tenant, cantidad in reparto.items():
            demo = usuarios_demo(tenant)
            for item in _periodo_seed(tenant):
                lote.put_item(Item=item)
            for perfil in demo:
                for item in _items(tenant, perfil, demo=True):
                    lote.put_item(Item=item)
            for n in range(1, max(0, cantidad - len(demo)) + 1):
                for item in _items(tenant, usuario_generado(tenant, n), demo=False):
                    lote.put_item(Item=item)
            escritos[tenant] = max(cantidad, len(demo))
    return escritos


def publicar_eventos_demo(tenants=ORDEN) -> int:
    """user.provisioned de los usuarios demo (por outbox). Devuelve cuántos publicó."""
    total = 0
    for tenant in tenants:
        for perfil in usuarios_demo(tenant):
            data = {"userId": perfil["userId"], "role": perfil["role"]}
            if perfil["institutionalCode"]:
                data["institutionalCode"] = perfil["institutionalCode"]
            evento = outbox.nuevo_evento(PROVISIONADO, tenant_id=tenant, aggregate_type="user",
                                         aggregate_id=perfil["userId"], aggregate_version=1, actor_id="system",
                                         data=data)
            outbox.guardar(cambios=[], eventos=[evento])
            total += 1
    return total


def main():
    parser = argparse.ArgumentParser(description="Datos iniciales de ms-usuarios")
    parser.add_argument("--stage", help="usa la tabla <stage>-t_usuarios")
    parser.add_argument("--escala", type=float, default=1.0, help="fracción del reparto (1 = 30.000)")
    parser.add_argument("--eventos-demo", action="store_true",
                        help="publica user.provisioned de los 21 usuarios demo")
    args = parser.parse_args()
    if args.stage:
        os.environ["TABLA"] = f"{args.stage}-t_usuarios"
    if not os.environ.get("TABLA"):
        raise SystemExit("Pasa --stage o define TABLA.")
    for tenant_id, estado in crear_tenants().items():
        print(f"{tenant_id}: tenant {estado}")
    reparto = {k: max(1, int(v * args.escala)) for k, v in REPARTO.items()}
    for tenant, cantidad in poblar(reparto).items():
        print(f"{tenant}: {cantidad} usuarios")
    print(f"Total: {sum(reparto.values())} usuarios en {os.environ['TABLA']}")
    if args.eventos_demo:
        print(f"user.provisioned publicados: {publicar_eventos_demo()}")


if __name__ == "__main__":
    main()
