from decimal import Decimal

import pytest
from fabrica import tarifario_xlsx

from validador.base.config import ReglasTarifa
from validador.tarifario.importer import importar
from validador.tarifario.matcher import Tarifario

FILAS = [
    ("ACD237", 890244, "CONSULTA DE PRIMERA VEZ POR ESPECIALISTA EN ENDOCRINOLOGIA", 17.67, 213983.7, 214000),
    ("ACD231", 890350, "CONSULTA CONTROL O SEGUIMIENTO POR ESPECIALISTA EN MEDICINA MATERNO FETAL", 17.67, 213983.7, 214000),
    ("ACD194", 998702, "SEDACION DE UN PROCEDIMIENTO DIAGNOSTICO O TERAPEUTICO", 15.95, 193154.5, 193200),
    ("ACD015", 998702, "SEDACION PARA PROCEDIMIENTO DE RESONANCIA MAGNETICA (ADULTO", 49.14, 595085.4, 595100),
    ("ACD279", 998702, "SEDACION PARA PROCEDIMIENTO DE TOMOGRAFIA AXIAL COMPUTARIZADA", 39.22, 474954.2, 475000),
    ("ACD578", "\xa0072502", "ADRENALECTOMIA", 723.68, 8763764.8, 8763800),
    ("ACD137", "601T01", "TRANSPORTE ASISTENCIAL BASICO -TAB SENCILLO X KM", 1.53, 18528.3, 18500),
]


@pytest.fixture
def tarifario(tmp_path):
    ruta = tmp_path / "t.xlsx"
    tarifario_xlsx(ruta, FILAS)
    res = importar(ruta)
    assert res.ok, res.errores
    return Tarifario(res.items)


def test_importa_normalizando(tmp_path):
    ruta = tmp_path / "t.xlsx"
    tarifario_xlsx(ruta, FILAS)
    res = importar(ruta)
    assert res.uvb_valor == Decimal(12110)  # sale del encabezado
    cups = {i.cups for i in res.items}
    assert {"890244", "072502", "601T01"} <= cups
    assert all(isinstance(i.cups, str) for i in res.items)


def test_rechaza_codigo_institucional_repetido(tmp_path):
    ruta = tmp_path / "t.xlsx"
    tarifario_xlsx(ruta, FILAS + [("ACD237", 890245, "OTRA", 1, 12110, 12100)])
    res = importar(ruta)
    assert not res.ok and "repetido" in res.errores[0]


def test_avisa_redondeo_inconsistente(tmp_path):
    ruta = tmp_path / "t.xlsx"
    tarifario_xlsx(ruta, [("ACD001", 890244, "X", 17.67, 213983.7, 213900)])
    assert any("REDONDEADO" in a for a in importar(ruta).avisos)


def test_cups_con_una_tarifa_y_descripcion_que_coincide(tarifario):
    r = tarifario.buscar("890244", "CONSULTA DE PRIMERA VEZ POR ESPECIALISTA EN ENDOCRINOLOGIA-CONSULTA - INTERCON", Decimal(214000), ReglasTarifa())
    assert r.estado == "coincide" and r.codigo_institucional == "ACD237"


def test_descripcion_generica_no_genera_falsa_alarma(tarifario):
    # Mismo CUPS pero facturado con otra descripción y otro valor: no se puede afirmar que el valor esté mal.
    r = tarifario.buscar("890350", "CONSULTA MEDICINA ESPECIALIZADA AMBULATORIA-CONSULTA - INTERCON", Decimal(79000), ReglasTarifa())
    assert r.estado == "no_determinable"


def test_cups_con_varias_tarifas_decide_por_descripcion(tarifario):
    r = tarifario.buscar("998702", "SEDACION PARA PROCEDIMIENTO DE RESONANCIA MAGNETICA ADULTO-PROCED ESPECIALES", Decimal(595100), ReglasTarifa())
    assert r.estado == "coincide" and r.codigo_institucional == "ACD015" and r.metodo == "por_descripcion"


def test_cups_con_varias_tarifas_valor_distinto(tarifario):
    r = tarifario.buscar("998702", "SEDACION PARA PROCEDIMIENTO DE TOMOGRAFIA AXIAL COMPUTARIZADA", Decimal(400000), ReglasTarifa())
    assert r.estado == "valor_difiere" and r.valor_unitario_tarifa == Decimal(475000)


def test_descripcion_ambigua_se_resuelve_por_valor(tarifario):
    r = tarifario.buscar("998702", "SOPORTE DE SEDACION PARA CONSULTA O APOYO DIAGNOSTICO", Decimal(193200), ReglasTarifa())
    assert r.estado == "coincide" and r.metodo == "por_valor" and r.codigo_institucional == "ACD194"


def test_cups_que_no_esta(tarifario):
    assert tarifario.buscar("879201", "TAC", Decimal(1), ReglasTarifa()).estado == "no_en_tarifario"
