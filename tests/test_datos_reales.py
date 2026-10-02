"""Pruebas sobre las carpetas reales locales. Se omiten si la carpeta no existe.

No imprimen ni comparan valores personales: solo verifican consistencia.
"""

from pathlib import Path

import pytest

from validador.base.config import Config
from validador.ingest import discover, factura_pdf_parser, rips, xml_parser
from validador.pipeline import procesar_carpeta

RAIZ = Path(__file__).parents[1] / "nuevaeps" / "NUEVA EPS EXITOSA"
TARIFARIO = Path(__file__).parents[1] / "documentos" / "base_cups.xlsx"

pytestmark = [
    pytest.mark.datos_reales,
    pytest.mark.skipif(not RAIZ.exists(), reason="sin datos reales locales"),
]


def _agrupar(lineas):
    return sorted((l.codigo, l.cantidad, l.valor_total) for l in lineas)


@pytest.fixture(scope="module")
def carpetas():
    return [discover.clasificar(d) for d in discover.carpetas_de_facturas(RAIZ)]


def test_rips_y_xml_coinciden_linea_a_linea(carpetas):
    distintas = []
    for cf in carpetas:
        if not (cf.rips and cf.xml):
            continue
        r = rips.extraer(cf.rips)
        x, _ = xml_parser.extraer(cf.xml)
        agregado = {}
        for l in r.lineas:
            c, v = agregado.get(l.codigo, (0, 0))
            agregado[l.codigo] = (c + l.cantidad, v + l.valor_total)
        agregado_x = {}
        for l in x.lineas:
            c, v = agregado_x.get(l.codigo, (0, 0))
            agregado_x[l.codigo] = (c + l.cantidad, v + l.valor_total)
        if agregado != agregado_x:
            distintas.append(cf.factura)
    assert distintas == []


def test_pdf_factura_coincide_con_xml(carpetas):
    campos = ["cufe", "numero_factura", "nit_emisor", "nit_eps", "paciente_id", "valor_total"]
    for cf in carpetas:
        x, _ = xml_parser.extraer(cf.xml)
        p = factura_pdf_parser.extraer(cf.pdf("FEV"))
        for c in campos:
            assert p.campos[c].valor == x.campos[c].valor, (cf.factura, c)
        assert _agrupar(p.lineas) == _agrupar(x.lineas), cf.factura


def test_pipeline_completo_no_falla(carpetas):
    cfg = Config()
    estados = {}
    for cf in carpetas:
        r = procesar_carpeta(cf.ruta, "prueba", cfg)
        assert r.cufe and r.factura == cf.factura
        estados[r.estado] = estados.get(r.estado, 0) + 1
    # La mayoría de las facturas no deben ser graves (control de falsas alarmas).
    assert estados.get("grave", 0) / len(carpetas) < 0.15
