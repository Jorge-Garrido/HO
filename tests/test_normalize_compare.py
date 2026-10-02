from decimal import Decimal

import pytest

from validador import compare
from validador.data_preprocessing import normalize
from validador.attribution import votar


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("214.000,00", Decimal("214000.00")),  # plantilla 1 del PDF
        ("79,000.00", Decimal("79000.00")),  # plantilla 2 del PDF
        ("214000.00", Decimal("214000.00")),  # XML
        ("1,00", Decimal("1.00")),
        ("3.00", Decimal("3.00")),
        ("214.000", Decimal("214000")),
        (555000, Decimal("555000")),
    ],
)
def test_dinero(entrada, esperado):
    assert normalize.dinero(entrada) == esperado


def test_redondeo_como_excel():
    # 15 UVB x 12110 = 181650: Excel redondea a 181700; round() de Python daría 181600.
    assert normalize.redondear_centena(Decimal("15") * 12110) == Decimal(181700)
    assert normalize.redondear_centena(Decimal("17.67") * 12110) == Decimal(214000)


def test_nit_y_documento():
    assert normalize.nit("891.800.231-0") == "891800231"
    assert normalize.nit("900156264-2") == "900156264"
    assert normalize.documento_id_texto("CC  10.000.001") == "CC 10000001"


def test_codigos_como_texto():
    assert normalize.codigo("\xa0072502") == "072502"
    assert normalize.cups(53105) == "053105"
    assert normalize.codigo("601T01") == "601T01"


def test_nombre_mismo_con_otro_orden():
    a = normalize.nombre("RAMIREZ TORRES ANA")
    b = normalize.nombre(" ANA  RAMÍREZ TORRES (a)")
    assert compare.mismos_tokens(a, b)


def test_distancias():
    assert compare.distancia_digitos("17458248", "17468248") == 1  # Hamming
    assert compare.distancia_digitos("200100300", "20010300") == 1  # Levenshtein
    # 5<->6 es confusión típica de OCR: pesa menos
    assert compare.distancia_ocr("17458248", "17468248") < compare.distancia_ocr("17458248", "17418248")


def test_votacion_mayoria_y_empate():
    v = votar({"xml": "1", "pdf_factura": "1", "rips": "2"}, "exacta")
    assert v.valor_mayoria == "1" and v.sospechosos == ["rips"]
    v = votar({"xml": "1", "rips": "2"}, "exacta")
    assert v.empate and v.sospechosos == []
