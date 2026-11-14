"""ACA-02: perfiles, aprovisionamiento al primer login (user.provisioned) y seed de 30.000."""

import json

from api import periodos, usuarios
from lib import esquemas
from lib import tabla as t
from scripts import poblar_datos
from tests.conftest import ADMIN, ALUMNO, DOCENTE, eventos, items, peticion, token

ANA = "11111111-1111-4111-8111-111111111111"
PROVISIONADO = "lms.users.user.provisioned.v1"


def _llamar(metodo, recurso, headers=None, body=None, path=None, query=None):
    evento = peticion(metodo, token(rol="STUDENT", user=ALUMNO) if headers is None else headers, body, path, query)
    evento["resource"] = recurso
    return usuarios.lambda_handler(evento, None)


def _cuerpo(r):
    return json.loads(r["body"])


def _cognito(sub, rol="STUDENT", tenant="utec", **claims):
    """Petición como la deja API Gateway con el authorizer de Cognito (AUTH-01)."""
    return {"httpMethod": "POST", "resource": "/users/me", "headers": {},
            "requestContext": {"requestId": "r", "authorizer": {"claims": {
                "sub": sub, "custom:tenant_id": tenant, "custom:role": rol, **claims}}}}


# --- POST /users/me ---------------------------------------------------------------


def test_primer_login_crea_perfil_y_publica_una_sola_vez(aws):
    r = _llamar("POST", "/users/me")
    assert r["statusCode"] == 201
    perfil = _cuerpo(r)
    assert perfil["userId"] == ALUMNO and perfil["role"] == "STUDENT"
    assert perfil["email"] == f"{ALUMNO}@utec.demo" and perfil["locale"] == "es-PE"
    assert perfil["timeZone"] == "America/Lima" and perfil["createdAt"]
    r2 = _llamar("POST", "/users/me")
    assert r2["statusCode"] == 200 and _cuerpo(r2) == perfil  # idempotente
    evs = [e for e in eventos() if e["_detailType"] == PROVISIONADO]
    assert len(evs) == 1
    meta, data = evs[0]["metadata"], evs[0]["data"]
    assert data == {"userId": ALUMNO, "role": "STUDENT"}
    assert meta["aggregateType"] == "user" and meta["aggregateId"] == ALUMNO and meta["aggregateVersion"] == 1
    assert esquemas.errores(PROVISIONADO, {"metadata": meta, "data": data}) == []


def test_perfil_desde_claims_de_cognito(aws, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "cognito")
    r = usuarios.lambda_handler(_cognito(DOCENTE, rol="TEACHER", email="Profe.Ruiz@UTEC.edu.pe",
                                         given_name="Rosa", family_name="Ruiz"), None)
    assert r["statusCode"] == 201
    perfil = _cuerpo(r)
    assert (perfil["givenName"], perfil["familyName"], perfil["email"]) == ("Rosa", "Ruiz", "profe.ruiz@utec.edu.pe")
    assert perfil["role"] == "TEACHER" and perfil["institutionalCode"] is None


def test_padron_completa_nombre_y_codigo(aws, monkeypatch):
    poblar_datos.poblar({"utec": 7})
    monkeypatch.setenv("AUTH_MODE", "cognito")
    nuevo = "5e0c9d4a-1b2c-4d3e-8f40-0a1b2c3d4e5f"  # el sub de Cognito no es el id del seed
    r = usuarios.lambda_handler(_cognito(nuevo, email="alumno.demo@utec.edu.pe"), None)
    perfil = _cuerpo(r)
    assert r["statusCode"] == 201
    assert (perfil["givenName"], perfil["familyName"]) == ("Ana", "Torres")
    # el código ya es de la Ana del seed: el perfil se crea igual, sin código
    assert perfil["institutionalCode"] is None
    otro = "6f1d0e5b-2c3d-4e4f-9a51-1b2c3d4e5f60"
    items_padron = items("ROSTER#estudiante1@utec.edu.pe")
    assert items_padron and items_padron[0]["institutionalCode"] == "202310458"
    t_item = {"tenant_id": "utec", "sk": "ROSTER#nueva@utec.edu.pe", "entidad": "ROSTER",
              "institutionalCode": "202399999", "givenName": "Nueva", "familyName": "Alumna"}
    t.tabla().put_item(Item=t_item)
    perfil = _cuerpo(usuarios.lambda_handler(_cognito(otro, email="nueva@utec.edu.pe"), None))
    assert perfil["institutionalCode"] == "202399999" and perfil["givenName"] == "Nueva"
    ev = [e for e in eventos() if e["_detailType"] == PROVISIONADO and e["data"]["userId"] == otro][0]
    assert ev["data"]["institutionalCode"] == "202399999"
    assert items("USERCODE#202399999")[0]["userId"] == otro


def test_get_y_patch_de_mi_perfil(aws):
    assert _llamar("GET", "/users/me")["statusCode"] == 404
    assert _llamar("PATCH", "/users/me", body={"locale": "en-US"})["statusCode"] == 404
    _llamar("POST", "/users/me")
    r = _llamar("PATCH", "/users/me", body={"locale": "en-US", "timeZone": "America/Bogota"})
    assert r["statusCode"] == 200
    assert (_cuerpo(r)["locale"], _cuerpo(r)["timeZone"]) == ("en-US", "America/Bogota")
    assert _cuerpo(_llamar("GET", "/users/me"))["locale"] == "en-US"
    assert _llamar("PATCH", "/users/me", body={"locale": "español"})["statusCode"] == 400
    assert _llamar("PATCH", "/users/me", body={"role": "ADMIN"})["statusCode"] == 400  # el rol no se cambia
    assert _llamar("GET", "/users/me", headers={})["statusCode"] == 401


# --- GET /users y /users/{id} -----------------------------------------------------------


def test_directorio_filtra_pagina_y_respeta_permisos(aws):
    poblar_datos.poblar({"utec": 50, "upc": 10})
    docente = token(rol="TEACHER", user=DOCENTE)
    r = _llamar("GET", "/users", headers=docente, query={"limit": "100"})
    assert r["statusCode"] == 200 and len(_cuerpo(r)["items"]) == 50
    alumnos = _cuerpo(_llamar("GET", "/users", headers=docente, query={"role": "STUDENT", "limit": "100"}))["items"]
    assert alumnos and all(u["role"] == "STUDENT" for u in alumnos)
    ana = _cuerpo(_llamar("GET", "/users", headers=docente, query={"institutionalCode": "202310457"}))["items"]
    assert [(u["userId"], u["givenName"], u["familyName"]) for u in ana] == [(ANA, "Ana", "Torres")]
    assert _cuerpo(_llamar("GET", "/users", headers=docente,
                           query={"institutionalCode": "202310457", "role": "TEACHER"}))["items"] == []
    p1 = _cuerpo(_llamar("GET", "/users", headers=docente, query={"limit": "30"}))
    p2 = _cuerpo(_llamar("GET", "/users", headers=docente, query={"limit": "30", "nextToken": p1["nextToken"]}))
    assert len(p1["items"]) == 30 and len(p2["items"]) == 20 and p2["nextToken"] is None
    assert {u["userId"] for u in p1["items"]}.isdisjoint({u["userId"] for u in p2["items"]})
    assert _llamar("GET", "/users", headers=docente, query={"nextToken": "basura"})["statusCode"] == 400
    assert _llamar("GET", "/users", headers=docente, query={"role": "JEFE"})["statusCode"] == 400
    assert _llamar("GET", "/users")["statusCode"] == 403  # alumno
    upc = _cuerpo(_llamar("GET", "/users", headers=token(tenant="upc"), query={"limit": "100"}))["items"]
    assert len(upc) == 10 and ANA not in {u["userId"] for u in upc}  # otro tenant


def test_obtener_un_usuario(aws):
    poblar_datos.poblar({"utec": 7, "upc": 7})
    admin = token(user=ADMIN)
    r = _llamar("GET", "/users/{userId}", headers=admin, path={"userId": ANA})
    assert r["statusCode"] == 200 and _cuerpo(r)["email"] == "alumno.demo@utec.edu.pe"
    assert _llamar("GET", "/users/{userId}", headers=token(tenant="upc"), path={"userId": ANA})["statusCode"] == 404
    assert _llamar("GET", "/users/{userId}", headers=admin, path={"userId": "x"})["statusCode"] == 404
    assert _llamar("GET", "/users/{userId}", path={"userId": ANA})["statusCode"] == 403


def test_usuario_del_seed_entra_sin_crear_otro_perfil(aws):
    poblar_datos.poblar({"utec": 7})
    r = _llamar("POST", "/users/me", headers=token(rol="STUDENT", user=ANA))
    assert r["statusCode"] == 200 and _cuerpo(r)["givenName"] == "Ana"
    assert [e for e in eventos() if e["_detailType"] == PROVISIONADO] == []  # ya existía: no publica


# --- Seed --------------------------------------------------------------------------


def test_reparto_oficial():
    assert poblar_datos.REPARTO == {"utec": 15000, "upc": 10000, "pucp": 5000}
    assert sum(poblar_datos.REPARTO.values()) == 30000


def test_seed_reparto_idempotente_y_sin_eventos(aws):
    reparto = {"utec": 300, "upc": 200, "pucp": 100}
    assert poblar_datos.poblar(reparto) == reparto
    assert poblar_datos.poblar(reparto) == reparto
    for tenant, cantidad in reparto.items():
        perfiles = items("USER#", tenant)
        assert len(perfiles) == cantidad
        roles = [u["role"] for u in perfiles]
        assert roles.count("STUDENT") > roles.count("TEACHER") > roles.count("ADMIN") > 0
        codigos = [u["institutionalCode"] for u in perfiles if u["institutionalCode"]]
        assert len(codigos) == len(set(codigos)) == len(items("USERCODE#", tenant))
        assert items("EVENTO#", tenant) == []
        periodo = items("TERM#trm_0000000000000000000000SEED", tenant)
        assert periodo[0]["status"] == "ACTIVE"
    for perfil in items("USER#")[:20]:
        assert esquemas.errores(PROVISIONADO, {
            "metadata": {"eventId": "01JC6HTF3PYB8X8HHCZDGRPY33", "tenantId": "utec",
                         "occurredAt": "2026-11-10T15:00:00Z", "aggregateType": "user",
                         "aggregateId": perfil["userId"], "aggregateVersion": 1, "actorId": "system"},
            "data": {k: perfil[k] for k in ("userId", "role", "institutionalCode") if perfil.get(k)}}) == []


def test_seed_trae_los_usuarios_demo_del_frontend(aws):
    poblar_datos.poblar({"utec": 10, "upc": 10, "pucp": 10})
    ana = items(f"USER#{ANA}")[0]
    assert (ana["givenName"], ana["familyName"], ana["institutionalCode"]) == ("Ana", "Torres", "202310457")
    assert items("USERCODE#DOC-UTEC-001")[0]["userId"] == "22222222-2222-4222-8222-222222222222"
    assert items("USERCODE#202311457", "upc")[0]["userId"] == "44444444-4444-4444-8444-444444444444"
    assert items("USER#10000000-0000-4000-8000-000000000001")[0]["givenName"] == "Luis"
    periodos_utec = _cuerpo(periodos.lambda_handler(peticion("GET", token(rol="TEACHER", user=DOCENTE)), None))
    assert "trm_0000000000000000000000SEED" in [p["termId"] for p in periodos_utec["items"]]


def test_eventos_demo(aws):
    poblar_datos.poblar({"utec": 7, "upc": 7, "pucp": 7})
    assert poblar_datos.publicar_eventos_demo() == 21
    evs = [e for e in eventos() if e["_detailType"] == PROVISIONADO]
    assert len(evs) == 7
    assert all(esquemas.errores(PROVISIONADO, {"metadata": e["metadata"], "data": e["data"]}) == [] for e in evs)
    ana = [e for e in evs if e["data"]["userId"] == ANA][0]
    assert ana["data"] == {"userId": ANA, "role": "STUDENT", "institutionalCode": "202310457"}
