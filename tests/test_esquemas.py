"""El evento se valida contra su esquema de nubeduca-contratos antes de guardarse."""

import json
from pathlib import Path

import pytest

from lib import esquemas, outbox
from lib import tabla as t
from tests.conftest import TENANT

EJEMPLOS = Path(__file__).parent / "ejemplos"
TERM = "trm_01JCP0KWJDXF9GJ9YNTK2H84A9"


@pytest.mark.parametrize("archivo", sorted(p.name for p in EJEMPLOS.glob("*.json")))
def test_ejemplos_oficiales_cumplen_su_esquema(archivo):
    evento = json.loads((EJEMPLOS / archivo).read_text(encoding="utf-8"))
    assert esquemas.errores(evento["detail-type"], evento["detail"]) == []


def _periodo():
    return json.loads((EJEMPLOS / "lms.users.term.started.v1.json").read_text(encoding="utf-8"))["detail"]


def test_rechaza_campo_faltante_extra_y_patron():
    detail = _periodo()
    del detail["data"]["name"]
    detail["data"]["rector"] = "Juan"
    detail["data"]["termId"] = "periodo-1"
    errs = esquemas.errores("lms.users.term.started.v1", detail)
    assert "detail.data.name: es obligatorio" in errs
    assert any("rector" in e for e in errs)
    assert any("termId: no cumple el patrón" in e for e in errs)


def test_rechaza_evento_fuera_del_catalogo():
    with pytest.raises(esquemas.EventoInvalido):
        esquemas.validar("lms.users.term.inventado.v1", _periodo())


def test_outbox_no_guarda_nada_si_el_evento_es_invalido(aws):
    evento = outbox.nuevo_evento(
        "lms.users.term.started.v1", tenant_id=TENANT, aggregate_type="term",
        aggregate_id=TERM, aggregate_version=2, actor_id="system",
        data={"termId": "no-es-un-id", "name": "2026-2", "startsOn": "2026-08-17", "endsOn": "2026-12-12"},
    )
    cambio = {"Put": {"Item": {"tenant_id": TENANT, "sk": f"TERM#{TERM}"}}}
    with pytest.raises(esquemas.EventoInvalido):
        outbox.guardar([cambio], [evento])
    assert t.tabla().get_item(Key={"tenant_id": TENANT, "sk": f"TERM#{TERM}"}).get("Item") is None
