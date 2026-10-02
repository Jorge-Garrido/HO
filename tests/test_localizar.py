"""Localizador genérico: solo posiciones + diccionario de etiquetas (sin plantillas)."""

import pymupdf
import pytest

from validador import localizar
from validador.data_preprocessing import layout


@pytest.fixture
def doc(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    t = lambda x, y, s: p.insert_text((x, y), s, fontsize=8)  # noqa: E731
    t(20, 40, "NIT. 800.000.001-0")
    t(20, 60, "CLIENTE: EMPRESA PROMOTORA DE SALUD")
    t(233, 60, "PACIENTE: PEREZ DE LA CRUZ ANA")
    t(435, 60, "FECHA INGRESO: 2026-07-31")
    t(233, 70, "ID: CC  1000001")
    t(233, 80, "AUTORIZACIÓN: 17458248")
    t(20, 80, "DOCUMENTO: 03-2053623-01")
    t(20, 100, "FACTURA ELECTRÓNICA DE VENTA No.FE0000001")
    t(460, 200, "TOTAL A PAGAR")
    t(560, 200, "214.000,00")
    # Nombre en dos líneas con la etiqueta centrada entre ellas
    t(300, 300, "RAMIREZ MORA HELENA")
    t(263, 305, "Paciente:")
    t(300, 310, "ANDREA")
    ruta = tmp_path / "doc.pdf"
    d.save(ruta)
    return layout.desde_pdf(ruta)


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("numero_autorizacion", "17458248"),
        ("paciente_id", "CC 1000001"),
        ("paciente_nombre", "ANA PEREZ DE LA CRUZ"),  # otro orden
        ("numero_factura", "FE0000001"),  # etiqueta pegada 'No.FE...'
        ("nit_emisor", "800000001"),  # sin dígito de verificación
        ("valor_total", "214000.00"),
        ("fecha_ingreso", "2026-07-31"),
    ],
)
def test_confirma_valor_correcto(doc, campo, valor):
    assert localizar.verificar(doc, campo, valor).estado == "confirmado"


def test_un_digito_distinto_es_diferencia_no_confirmacion(doc):
    v = localizar.verificar(doc, "numero_autorizacion", "17468248")
    assert v.estado == "diferencia" and v.distancia == 1 and "17458248" in v.valor_encontrado


def test_numero_ajeno_no_se_confirma(doc):
    assert localizar.verificar(doc, "numero_autorizacion", "99887766").estado == "no_encontrado"


def test_numero_presente_pero_sin_etiqueta_no_es_confirmado_pleno(doc):
    # '2053623' está en el documento, pero junto a DOCUMENTO:, no junto a AUTORIZACIÓN:
    assert localizar.verificar(doc, "numero_autorizacion", "2053623").estado == "confirmado_sin_contexto"


def test_nombre_con_una_letra_distinta(doc):
    v = localizar.verificar(doc, "paciente_nombre", "PEREZ DE LA CRUS ANA")
    assert v.estado == "diferencia"


def test_extraccion_sin_valor_esperado(doc):
    assert localizar.extraer(doc, "numero_autorizacion") == "17458248"
    assert localizar.extraer(doc, "paciente_id") == "CC 1000001"
    assert localizar.extraer(doc, "numero_factura") == "FE0000001"
    assert localizar.extraer(doc, "valor_total") == "214000.00"
    # no se come la etiqueta vecina 'FECHA INGRESO:'
    assert localizar.extraer(doc, "paciente_nombre") == "PEREZ DE LA CRUZ ANA"


def test_nombre_multilinea_con_etiqueta_centrada(doc):
    v = localizar.verificar(doc, "paciente_nombre", "RAMIREZ MORA HELENA ANDREA")
    assert v.estado == "confirmado"
