"""ACA-01: tenants (instituciones). Los crea el seed y publican lms.users.tenant.created.v1."""

import json

from api import salud, tenant
from lib import esquemas
from scripts import poblar_datos
from tests.conftest import ALUMNO, eventos, items, peticion, token


def test_salud_dice_ms_usuarios():
    cuerpo = json.loads(salud.lambda_handler({}, None)["body"])
    assert cuerpo == {"status": "ok", "service": "ms-usuarios", "version": "1.0.0"}


def test_crear_tenants_guarda_los_3_y_publica_tenant_created(aws):
    assert poblar_datos.crear_tenants() == {"utec": "CREADO", "upc": "CREADO", "pucp": "CREADO"}
    for tid in ("utec", "upc", "pucp"):
        assert len(items("TENANT", tid)) == 1
        evs = eventos(tid)
        assert len(evs) == 1
        ev = evs[0]
        assert ev["_detailType"] == "lms.users.tenant.created.v1"
        assert ev["_source"] == "lms.users"
        assert ev["metadata"]["tenantId"] == tid
        assert ev["metadata"]["aggregateType"] == "tenant"
        assert ev["metadata"]["aggregateId"] == tid
        assert ev["metadata"]["actorId"] == "system"
        assert ev["data"]["timeZone"] == "America/Lima"
        assert ev["data"]["locale"] == "es-PE"
        detail = {"metadata": ev["metadata"], "data": ev["data"]}
        assert esquemas.errores("lms.users.tenant.created.v1", detail) == []


def test_crear_tenants_es_idempotente(aws):
    poblar_datos.crear_tenants()
    assert poblar_datos.crear_tenants() == {"utec": "YA_EXISTIA", "upc": "YA_EXISTIA", "pucp": "YA_EXISTIA"}
    for tid in ("utec", "upc", "pucp"):
        assert len(eventos(tid)) == 1  # el segundo intento no publica otra vez


def test_get_tenant_devuelve_el_del_token(aws):
    poblar_datos.crear_tenants()
    r = tenant.lambda_handler(peticion("GET", token(rol="STUDENT", tenant="upc", user=ALUMNO)), None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"]) == {
        "tenantId": "upc",
        "name": "Universidad Peruana de Ciencias Aplicadas",
        "timeZone": "America/Lima",
        "locale": "es-PE",
    }


def test_get_tenant_sin_token_y_sin_seed(aws):
    assert tenant.lambda_handler(peticion("GET", {}), None)["statusCode"] == 401
    r = tenant.lambda_handler(peticion("GET", token()), None)
    assert r["statusCode"] == 404
    assert json.loads(r["body"])["code"] == "TENANT_NO_ENCONTRADO"
