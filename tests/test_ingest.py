from decimal import Decimal

from fabrica import CUFE, Caso, cuv_dict, hc_pdf, rips_dict, xml_bytes

from validador.ingest import cuv, hc_parser, rips, xml_parser


def test_xml_doble_cdata_y_campos():
    x, nota = xml_parser.desde_bytes(xml_bytes(Caso()))
    v = {k: c.valor for k, c in x.campos.items()}
    assert v["cufe"] == CUFE and len(v["cufe"]) == 96
    assert v["numero_factura"] == "FE0000001"
    assert v["paciente_id"] == "CC 1000001"
    assert v["numero_autorizacion"] == "17458248"
    assert v["id_interno_paciente"] == "555001"
    assert v["valor_total"] == "214000.00"
    assert nota.campos["numero_autorizacion"].valor == "17458248"


def test_xml_codigos_dian_sin_repetir_y_sin_0000():
    x, _ = xml_parser.desde_bytes(xml_bytes(Caso()))
    assert x.extra["dian_codigo"] == "02"
    assert [c.codigo for c in x.codigos] == ["FAJ43b", "RUT01"]


def test_cups_regla_de_extraccion():
    # CUPS solo en la descripción (el ID es código interno)
    assert xml_parser.codigo_de_linea("39143", "890261 CONSULTA ...") == ("890261", "xml.descripcion")
    # CUPS en el ID
    assert xml_parser.codigo_de_linea("890244", "CONSULTA DE PRIMERA VEZ ...") == ("890244", "xml.buyers")
    # Descripción con tabulador después del código
    assert xml_parser.codigo_de_linea("39139", "890226 \tCONSULTA PREANESTÉSICA")[0] == "890226"
    # Medicamento (CUM) e insumo: no son CUPS, se usa el ID
    assert xml_parser.codigo_de_linea("20085777-3", "PALIVIZUMAB 50 MG") == ("20085777-3", "xml.buyers")
    assert xml_parser.codigo_de_linea("S55114", "MATERIAL DE SUTURA") == ("S55114", "xml.buyers")


def test_rips_paciente_no_es_el_profesional():
    doc = rips.desde_dict(rips_dict(Caso()))
    assert doc.campos["paciente_id"].valor == "CC 1000001"
    assert doc.lineas[0].codigo == "890244"
    assert doc.lineas[0].valor_total == Decimal("214000")


def test_rips_valor_total_descuenta_moderador():
    d = rips_dict(Caso())
    d["usuarios"][0]["servicios"]["consultas"][0]["valorPagoModerador"] = 4000
    assert rips.desde_dict(d).campos["valor_total"].valor == "210000.00"


def test_cuv_sin_resultados_validacion():
    doc = cuv.desde_dict(cuv_dict(Caso(), resultados=False))
    assert doc.extra["result_state"] is True and doc.codigos == []


def test_hc_encabezado_y_atenciones(tmp_path):
    ruta = tmp_path / "HEV.pdf"
    hc_pdf(ruta, Caso(), tipo_consulta="Control")
    doc = hc_parser.extraer(ruta)
    assert doc.campos["paciente_id"].valor == "CC 1000001"
    assert doc.campos["id_interno_paciente"].valor == "555001"
    assert doc.campos["paciente_nombre"].valor == "JUAN CARLOS PEREZ GOMEZ"  # sin "(a)"
    [a] = doc.atenciones
    assert a.especialidad == "ENDOCRINOLOGIA" and a.tipo_consulta == "control"
