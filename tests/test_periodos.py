"""ACA-01: periodos académicos. Iniciar un periodo publica lms.users.term.started.v1."""

import json
import re

import pytest

from api import periodos
from lib import esquemas
from relay import stream_a_bus
from tests.conftest import ALUMNO, DOCENTE, eventos, items, peticion, registros_stream, token

PERIODO = {"name": "2026-2", "startsOn": "2026-08-17", "endsOn": "2026-12-12"}
TERM_ID = re.compile(r"^trm_[0-9A-HJKMNP-TV-Z]{26}$")


def _crear(body=PERIODO, headers=None):
    return periodos.lambda_handler(peticion("POST", token() if headers is None else headers, body), None)


def _iniciar(term_id, headers=None):
    return periodos.lambda_handler(
        peticion("POST", token() if headers is None else headers, path_params={"termId": term_id}) | {"resource": "/terms/{termId}/start"},
        None,
    )


def _listar(headers=None, query=None):
    return periodos.lambda_handler(peticion("GET", token(rol="STUDENT", user=ALUMNO) if headers is None else headers, query=query), None)


def test_crear_periodo_queda_planned_sin_evento(aws):
    r = _crear()
    assert r["statusCode"] == 201
    term = json.loads(r["body"])
    assert TERM_ID.match(term["termId"])
    assert term | {"termId": "x"} == {**PERIODO, "termId": "x", "status": "PLANNED"}
    assert len(items("TERM#")) == 1
    assert eventos() == []  # crear no publica: solo start emite (x-emits del OpenAPI)


@pytest.mark.parametrize("body, motivo", [
    ({}, "vacío"),
    ({**PERIODO, "name": ""}, "nombre vacío"),
    ({**PERIODO, "name": "x" * 41}, "nombre > 40"),
    ({**PERIODO, "startsOn": "17/08/2026"}, "formato de fecha"),
    ({**PERIODO, "endsOn": "2026-02-30"}, "fecha inexistente"),
    ({**PERIODO, "endsOn": "2026-08-01"}, "termina antes de empezar"),
])
def test_crear_periodo_valida_el_body(aws, body, motivo):
    r = _crear(body)
    assert r["statusCode"] == 400, motivo
    assert json.loads(r["body"])["code"] == "DATOS_INVALIDOS"


def test_crear_periodo_solo_admin(aws):
    assert _crear(headers={})["statusCode"] == 401
    assert _crear(headers=token(rol="TEACHER", user=DOCENTE))["statusCode"] == 403
    assert _crear(headers=token(rol="STUDENT", user=ALUMNO))["statusCode"] == 403


def test_nombre_repetido_en_el_tenant_da_409(aws):
    assert _crear()["statusCode"] == 201
    r = _crear()
    assert r["statusCode"] == 409
    assert json.loads(r["body"])["code"] == "PERIODO_DUPLICADO"
    assert len(items("TERM#")) == 1
    # El mismo nombre en otro tenant sí se puede.
    assert _crear(headers=token(tenant="pucp"))["statusCode"] == 201


def test_iniciar_periodo_publica_term_started_y_llega_a_ms_cursos(aws):
    term_id = json.loads(_crear()["body"])["termId"]
    r = _iniciar(term_id)
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["status"] == "ACTIVE"

    evs = eventos()
    assert len(evs) == 1
    ev = evs[0]
    assert ev["_detailType"] == "lms.users.term.started.v1"
    assert ev["metadata"]["aggregateType"] == "term"
    assert ev["metadata"]["aggregateId"] == term_id
    assert ev["metadata"]["aggregateVersion"] == 2
    assert ev["data"] == {"termId": term_id, **PERIODO}
    assert esquemas.errores("lms.users.term.started.v1", {"metadata": ev["metadata"], "data": ev["data"]}) == []

    # Outbox -> Stream -> relay -> bus -> regla de ms-cursos -> su cola.
    assert stream_a_bus.publicar(registros_stream()) == 1
    mensajes = aws["sqs"].receive_message(QueueUrl=aws["cola"], MaxNumberOfMessages=10)["Messages"]
    assert len(mensajes) == 1
    assert json.loads(mensajes[0]["Body"])["detail"]["data"]["termId"] == term_id


def test_iniciar_dos_veces_da_409_y_no_publica_de_nuevo(aws):
    term_id = json.loads(_crear()["body"])["termId"]
    assert _iniciar(term_id)["statusCode"] == 200
    r = _iniciar(term_id)
    assert r["statusCode"] == 409
    assert json.loads(r["body"])["code"] == "PERIODO_NO_PLANIFICADO"
    assert len(eventos()) == 1


def test_iniciar_periodo_inexistente_o_de_otro_tenant_da_404(aws):
    term_id = json.loads(_crear()["body"])["termId"]
    assert _iniciar("trm_01JCQRZHQT9F4WY2JYPDV546K7")["statusCode"] == 404
    assert _iniciar("no-es-un-id")["statusCode"] == 404
    assert _iniciar(term_id, headers=token(tenant="upc"))["statusCode"] == 404  # aislamiento por tenant
    assert _iniciar(term_id, headers=token(rol="TEACHER", user=DOCENTE))["statusCode"] == 403


def test_listar_periodos_paginado_mas_reciente_primero(aws):
    nombres = [f"2026-{i}" for i in range(1, 6)]
    for i, nombre in enumerate(nombres):
        assert _crear({**PERIODO, "name": nombre, "startsOn": f"2026-0{i + 1}-01"})["statusCode"] == 201
    _crear({**PERIODO, "name": "otro-tenant"}, headers=token(tenant="upc"))

    vistos, next_token = [], None
    for _ in range(3):
        query = {"limit": "2"} | ({"nextToken": next_token} if next_token else {})
        r = _listar(query=query)
        assert r["statusCode"] == 200
        pagina = json.loads(r["body"])
        vistos += [p["name"] for p in pagina["items"]]
        next_token = pagina["nextToken"]
        if next_token is None:
            break
    assert vistos == list(reversed(nombres))
    assert next_token is None


@pytest.mark.parametrize("query", [{"limit": "0"}, {"limit": "101"}, {"limit": "x"}, {"nextToken": "basura"}])
def test_listar_valida_parametros(aws, query):
    assert _listar(query=query)["statusCode"] == 400


def test_listar_sin_token(aws):
    assert _listar(headers={})["statusCode"] == 401
