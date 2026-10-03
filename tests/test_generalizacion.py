"""Generalización a otras EPS e IPS: estructuras de carpetas, zip, nombres libres, códigos internos."""

from __future__ import annotations

import json
import zipfile
from decimal import Decimal
from pathlib import Path

from fabrica import Caso, cuv_dict, rips_dict, xml_bytes
from validador.base.config import Config
from validador.base.schemas import DocumentoExtraido, LineaServicio
from validador.data_preprocessing.layout import Palabra
from validador.ingest import discover
from validador.ingest.xml_parser import codigo_de_linea
from validador.localizar import _nombre_de
from validador.pipeline import _emparejar_codigos, comparar_lineas, procesar_carpeta


def _cfg(tmp: Path) -> Config:
    return Config(db_url=f"sqlite:///{tmp / 't.db'}", ocr_habilitado=False, cache_descomprimidos=str(tmp / "cache"))


def _pdf(ruta: Path, texto: str = "x") -> None:
    import pymupdf

    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), texto, fontsize=8)
    doc.save(ruta)


def test_descubrir_estructura_tipo_coosalud(tmp_path):
    """RIPS y CUV en carpeta hermana, CUV con prefijo, soportes sin prefijo, archivos basura."""
    c = Caso(factura="FEAV100")
    lote = tmp_path / "MAYO" / "TUNJA" / "SUBS"
    (lote / "RIPS").mkdir(parents=True)
    (lote / "RIPS" / "FEAV100.json").write_text(json.dumps(rips_dict(c)), encoding="utf-8")
    (lote / "RIPS" / "CUV_FEAV100.json").write_text(json.dumps(cuv_dict(c)), encoding="utf-8")
    (lote / "PDF Y XML").mkdir()
    (lote / "PDF Y XML" / "FEAV100.xml").write_bytes(xml_bytes(c))
    for nombre in ("PDE.pdf", "HISTORIA.pdf", "ORDEN N.pdf", "OPF 2.pdf"):
        _pdf(lote / "FEAV100" / nombre)
    (lote / "FEAV100" / "desktop.ini").write_text("x")
    (tmp_path / "MAYO" / "RADICACION.xlsx").write_text("x")

    h = discover.descubrir(tmp_path / "MAYO", _cfg(tmp_path))
    assert [cf.factura for cf in h.facturas] == ["FEAV100"] and h.omitidos == []
    cf = h.facturas[0]
    assert cf.rips and cf.cuv and cf.xml
    assert set(cf.pdfs) == {"PDE", "HEV", "OPF"} and len(cf.pdfs["OPF"]) == 2
    assert cf.ruta == lote / "FEAV100"


def test_descubrir_zip_y_copias_identicas_sin_conflicto(tmp_path):
    c = Caso(factura="FEAV200")
    suelta = tmp_path / "LOTE" / "900_FEAV200"
    suelta.mkdir(parents=True)
    (suelta / "900_FEAV200.xml").write_bytes(xml_bytes(c))
    with zipfile.ZipFile(tmp_path / "LOTE" / "900_FEAV200.zip", "w") as z:
        z.writestr("900_FEAV200/900_FEAV200.xml", xml_bytes(c))  # misma factura, mismo contenido
        z.writestr("900_FEAV200/900_FEAV200.json", json.dumps(rips_dict(c)))
    h = discover.descubrir(tmp_path / "LOTE", _cfg(tmp_path))
    cf = h.facturas[0]
    assert cf.rips is not None and cf.conflictos == []
    assert not any(str(tmp_path / "LOTE") in str(p) for p in (tmp_path / "cache").rglob("*") if p.is_file())  # extraído a la caché


def test_versiones_distintas_se_reportan(tmp_path):
    c = Caso(factura="FEAV300")
    a, b = tmp_path / "L" / "ORIGINAL" / "FEAV300", tmp_path / "L" / "COPIA" / "FEAV300"
    a.mkdir(parents=True), b.mkdir(parents=True)
    (a / "FEAV300.xml").write_bytes(xml_bytes(c))
    (b / "FEAV300.xml").write_bytes(xml_bytes(Caso(factura="FEAV300", autorizacion="99999999")))
    (a / "FEAV300.json").write_text(json.dumps(rips_dict(c)), encoding="utf-8")
    cfg = _cfg(tmp_path)
    cf = discover.descubrir(tmp_path / "L", cfg).facturas[0]
    assert len(cf.conflictos) == 1 and cf.conflictos[0].startswith("XML")
    r = procesar_carpeta(cf, "c1", cfg)
    assert any(h.tipo == "documentos" and "versiones distintas" in h.motivo for h in r.hallazgos)
    assert r.archivos_origen  # para copiar solo sus archivos a revisión


def test_archivo_sin_numero_de_factura_se_omite(tmp_path):
    (tmp_path / "L" / "1056 NOMBRE PERSONA").mkdir(parents=True)
    _pdf(tmp_path / "L" / "1056 NOMBRE PERSONA" / "HEV_900_1056.pdf")
    h = discover.descubrir(tmp_path / "L", _cfg(tmp_path))
    assert h.facturas == [] and len(h.omitidos) == 1


def test_codigo_cups_con_sufijo_de_variante():
    assert codigo_de_linea("890202-2", "CONSULTA") == ("890202", "xml.buyers_variante")
    assert codigo_de_linea("20085777-3", "PALIVIZUMAB")[0] == "20085777-3"  # CUM: no es CUPS


def test_codigo_interno_recortado_se_empareja_con_aviso():
    agrupado = {"rips": {"P89020": (Decimal(1), Decimal(150000))}, "xml": {"P890207-1": (Decimal(1), Decimal("150000.00"))}}
    emparejados = _emparejar_codigos(agrupado, "rips")
    assert emparejados == {"P89020": {"rips": "P89020", "xml": "P890207-1"}}
    assert set(agrupado["xml"]) == {"P89020"}
    # Con distinto valor no se empareja: son servicios distintos.
    otro = {"rips": {"P89020": (Decimal(1), Decimal(150000))}, "xml": {"P890207-1": (Decimal(1), Decimal(1))}}
    assert _emparejar_codigos(otro, "rips") == {}


def test_codigo_recortado_es_una_sola_linea_grave():
    rips = DocumentoExtraido(fuente="rips", lineas=[LineaServicio(codigo="P89020", origen_codigo="rips", valor_total=Decimal(150000), fuente="rips")])
    xml = DocumentoExtraido(fuente="xml", lineas=[LineaServicio(codigo="P890207-1", origen_codigo="xml", valor_total=Decimal(150000), fuente="xml")])
    (linea,) = comparar_lineas({"rips": rips, "xml": xml}, "rips", None, Config(), (None, None))
    assert linea.resultado == "grave" and "código distinto al de la factura" in linea.motivos[0]


def _palabras(texto: str) -> list[Palabra]:
    return [Palabra(t, 10.0 * i, 0, 10.0 * i + 8, 8, 0, 1) for i, t in enumerate(texto.split())]


def test_nombre_corta_en_documento_y_rechaza_frases():
    assert _nombre_de(_palabras("PEREZ GOMEZ JUAN CC 1000001 SUBSIDIADO BENEFICIARIO")) == "PEREZ GOMEZ JUAN"
    assert _nombre_de(_palabras("MARIA DE LOS ANGELES ROJAS")) == "MARIA DE LOS ANGELES ROJAS"
    assert _nombre_de(_palabras("QUIEN ASISTE A CONSULTA DE PRIMERA VEZ POR OPTOMETRIA, SE OBSERVA ALERTA,")) is None
    assert _nombre_de(_palabras("PEREZ EDAD: 40")) is None  # una sola palabra antes de otra etiqueta


def test_medicamento_con_codigo_interno_y_cum_se_empareja_por_descripcion():
    agrupado = {"rips": {"52033-4": (Decimal(2), Decimal(9000))}, "xml": {"00100010": (Decimal(2), Decimal(9000))}}
    desc = {"rips": {"52033-4": "BUPIVACAINA CLORHIDRATO AMPOLLA X 0,5"}, "xml": {"00100010": "BUPIVACAINA CLORHIDRATO AMPOLLA 0.5"}}
    assert _emparejar_codigos(agrupado, "rips", desc) == {"52033-4": {"rips": "52033-4", "xml": "00100010"}}
    # Descripciones distintas y dos candidatos con el mismo valor: ambiguo, no se empareja.
    distinto = {"rips": {"52033-4": "DICLOFENACO AMPOLLA"}, "xml": {"00100010": "BUPIVACAINA CLORHIDRATO", "00100011": "LIDOCAINA"}}
    agrupado = {"rips": {"52033-4": (Decimal(2), Decimal(9000))},
                "xml": {"00100010": (Decimal(2), Decimal(9000)), "00100011": (Decimal(2), Decimal(9000))}}
    assert _emparejar_codigos(agrupado, "rips", distinto) == {}


def test_autorizacion_parcial_es_aviso_no_grave():
    from validador.base.schemas import CampoExtraido
    from validador.severity import evaluar_campo

    cfg = Config()
    ext = [CampoExtraido(campo="numero_autorizacion", valor="111111111,222222222", fuente="rips"),
           CampoExtraido(campo="numero_autorizacion", valor="111111111", fuente="pdf_factura")]
    r = evaluar_campo("numero_autorizacion", ext, cfg)
    assert r.resultado == "aviso" and r.documento_sospechoso == "pdf_factura" and r.valor_final == "111111111,222222222"
    ext[1] = CampoExtraido(campo="numero_autorizacion", valor="333333333", fuente="pdf_factura")
    assert evaluar_campo("numero_autorizacion", ext, cfg).resultado == "grave"  # contradicción real


def test_nombre_corta_en_etiqueta_vecina_y_tipo_documento_alias():
    from validador.data_preprocessing import normalize

    assert _nombre_de(_palabras("PEREZ GOMEZ JUAN CARLOS NO. DE IDENTIFICACION")) == "PEREZ GOMEZ JUAN CARLOS"
    assert normalize.documento_id("PEP", "123") == "PE 123"


def test_palabra_paciente_en_prosa_no_es_etiqueta_de_nombre():
    from validador.data_preprocessing.layout import DocLayout, Fila
    from validador.localizar import extraer

    def fila(i, texto, x=10.0):
        ps = [Palabra(t, x + 60 * j, 20 * i, x + 60 * j + 50, 20 * i + 8, 1, i) for j, t in enumerate(texto.split())]
        return Fila(1, i, ps)

    prosa = DocLayout(filas=[fila(0, "FIRMA DEL PACIENTE CANCELA C.MODERADORA"), fila(1, "OTRO TEXTO")])
    assert extraer(prosa, "paciente_nombre") is None
    tabla = DocLayout(filas=[fila(0, "PACIENTE"), fila(1, "PEREZ GOMEZ JUAN")])
    assert extraer(tabla, "paciente_nombre") == "PEREZ GOMEZ JUAN"


def test_empareja_por_valor_unico_y_no_si_es_ambiguo():
    def caso(valores_xml):
        agrupado = {"rips": {"20014297-1": (Decimal(1), Decimal(61600)), "30114-1": (Decimal(1), Decimal(92100))},
                    "xml": {f"0010005{i}": (Decimal(1), Decimal(v)) for i, v in enumerate(valores_xml)}}
        desc = {"rips": {"20014297-1": "MOXIFLOXACINA SOLUCI�N", "30114-1": "ACETIL COLINA"},
                "xml": {f"0010005{i}": d for i, d in enumerate(["MOXIFLOXACINO SOLUCION 0.05%", "OTRO PRODUCTO X 2 ML"])}}
        return _emparejar_codigos(agrupado, "rips", desc)
    assert set(caso([61600, 92100])) == {"20014297-1", "30114-1"}
    assert "30114-1" not in caso([61600, 61600])  # sin valor único para 92100: no se inventa pareja


def test_escaneo_recortado_o_parcial_no_es_grave():
    from validador.base.schemas import CampoExtraido
    from validador.severity import evaluar_campo

    cfg = Config()

    def campo(scan, rips, pdf):
        return evaluar_campo("numero_autorizacion", [
            CampoExtraido(campo="numero_autorizacion", valor=rips, fuente="rips"),
            CampoExtraido(campo="numero_autorizacion", valor=pdf, fuente="pdf_factura"),
            CampoExtraido(campo="numero_autorizacion", valor=scan, fuente="autorizacion_scan", confianza=0.9, etapa="tesseract"),
        ], cfg)

    r = campo("7196996", "17196996", "17196996")
    assert r.resultado == cfg.si_escaneo_recortado == "aviso" and r.valor_final == "17196996"
    r = campo("15825694", "15825694,15836151", "15825694,15836151")
    assert r.resultado == "aviso" and r.documento_sospechoso == "autorizacion_scan"
    # Un dígito sustituido que el OCR lee con seguridad (dos motores, sin duda): grave. La duda por
    # dígito se resuelve en la cascada (ver test_ocr), no aquí.
    assert campo("27196996", "17196996", "17196996").resultado == "grave"


def test_descripcion_identica_no_se_confunde_con_codigo_recortado():
    criterios: dict[str, str] = {}
    agrupado = {"rips": {"19934768-22": (Decimal(1), Decimal(5000))}, "xml": {"00100025": (Decimal(1), Decimal(5000))}}
    desc = {"rips": {"19934768-22": "DICLOFENACO AMPOLLA X 75 MG"}, "xml": {"00100025": "DICLOFENACO AMPOLLA X 75 MG"}}
    _emparejar_codigos(agrupado, "rips", desc, criterios)
    assert criterios == {"19934768-22": "otro_sistema"}


def test_rips_con_varias_autorizaciones_en_un_servicio():
    from fabrica import rips_dict
    from validador.ingest import rips

    datos = rips_dict(Caso(autorizacion="20076906,20076944"))
    assert rips.desde_dict(datos).campos["numero_autorizacion"].valor == "20076906,20076944"


def test_autorizaciones_repartidas_entre_documentos_son_aviso():
    from validador.base.schemas import CampoExtraido
    from validador.severity import evaluar_campo

    r = evaluar_campo("numero_autorizacion", [
        CampoExtraido(campo="numero_autorizacion", valor="17983717,17983759", fuente="rips"),
        CampoExtraido(campo="numero_autorizacion", valor="17983759", fuente="pdf_factura"),
        CampoExtraido(campo="numero_autorizacion", valor="17983717", fuente="autorizacion_scan", confianza=0.98),
    ], Config())
    assert r.resultado == "aviso" and r.valor_final == "17983717,17983759"


def test_prefijo_de_factura_se_aprende_de_los_xml(tmp_path):
    """FOMAG factura con prefijo FCOB: no hay que configurarlo, se aprende de los nombres de XML/RIPS."""
    c = Caso(factura="FCOB900")
    base = tmp_path / "L"
    (base / "X_RIPS" / "FCOB900").mkdir(parents=True)
    (base / "X_RIPS" / "FCOB900" / "FCOB900.xml").write_bytes(xml_bytes(c))
    (base / "X_RIPS" / "FCOB900" / "CUV_FCOB900.json").write_text(json.dumps(cuv_dict(c)), encoding="utf-8")
    _pdf(base / "X_FACTURAS" / "FCOB900" / "FEV_800_FCOB900.pdf")
    h = discover.descubrir(base, _cfg(tmp_path))
    assert [cf.factura for cf in h.facturas] == ["FCOB900"] and h.omitidos == []
    assert h.facturas[0].xml and h.facturas[0].cuv and h.facturas[0].pdf("FEV")


def test_resolucion_dian_no_es_numero_de_autorizacion():
    from validador.data_preprocessing.layout import DocLayout, Fila
    from validador.localizar import extraer

    def fila(i, texto):
        ps = [Palabra(t, 10 + 60 * j, 20 * i, 60 + 60 * j, 20 * i + 8, 1, i) for j, t in enumerate(texto.split())]
        return Fila(1, i, ps)

    doc = DocLayout(filas=[fila(0, "AUTORIZACION DE NUMERACION DE FACTURACION No. 18764099682897"),
                           fila(1, "NUMERO DE AUTORIZACION: 16097492")])
    assert extraer(doc, "numero_autorizacion") == "16097492"


def test_rango_de_numeracion_dian_y_nombre_del_funcionario():
    from validador.data_preprocessing.layout import DocLayout, Fila
    from validador.localizar import extraer

    def fila(i, texto, x=10):
        ps = [Palabra(t, x + 60 * j, 20 * i, x + 50 + 60 * j, 20 * i + 8, 1, i) for j, t in enumerate(texto.split())]
        return Fila(1, i, ps)

    fev = DocLayout(filas=[fila(0, "Modalidad: Electrónica - Número de Autorización: 18764099682897 desde FCOB1 hasta FCOB9999")])
    assert extraer(fev, "numero_autorizacion") is None
    pde = DocLayout(filas=[fila(0, "Nombre Paciente Sexo"), fila(1, "PEREZ GOMEZ JUAN"), fila(2, "Funcionario Nombre: LOPEZ RUIZ ANA")])
    assert extraer(pde, "paciente_nombre") == "PEREZ GOMEZ JUAN"


def test_encabezado_generico_nombre_no_es_el_paciente():
    from validador.data_preprocessing.layout import DocLayout, Fila
    from validador.localizar import extraer

    def fila(i, palabras):
        return Fila(1, i, [Palabra(t, x, 20 * i, x + 40, 20 * i + 8, 1, i) for t, x in palabras])

    impuestos = DocLayout(filas=[fila(0, [("Nombre", 10), ("Base", 200), ("Porcentaje", 300)]),
                                 fila(1, [("SERVICIOS", 10), ("GENERALES", 55), ("100", 200)])])
    assert extraer(impuestos, "paciente_nombre") is None


def test_desempate_de_identidad_por_fecha_de_nacimiento_y_sexo():
    from validador.base.schemas import ResultadoCampo
    from validador.pipeline import _desempatar_identidad

    def docs(fecha_hc, sexo_hc):
        rips = DocumentoExtraido(fuente="rips")
        rips.poner("fecha_nacimiento", "1980-01-01")
        rips.poner("sexo", "F")
        hc = DocumentoExtraido(fuente="hc")
        hc.poner("fecha_nacimiento", fecha_hc)
        hc.poner("sexo", sexo_hc)
        return {"rips": rips, "hc": hc}

    def campo():
        return [ResultadoCampo(campo="paciente_id", valor_final="CC 1", valores_por_fuente={"rips": "CC 1", "hc": "CC 2"},
                               resultado="grave", motivo="hc difiere", documento_sospechoso="hc")]

    c = campo(); _desempatar_identidad(c, docs("1980-01-01", "F"), Config())
    assert c[0].resultado == "aviso" and "misma persona" in c[0].motivo
    c = campo(); _desempatar_identidad(c, docs("2015-06-30", "M"), Config())
    assert c[0].resultado == "grave" and "S00202" in c[0].motivo
    c = campo(); _desempatar_identidad(c, docs(None, None), Config())
    assert c[0].resultado == "grave" and c[0].motivo == "hc difiere"  # sin datos no se decide


def test_documento_del_acompanante_no_es_el_del_paciente():
    from validador.data_preprocessing.layout import DocLayout, Fila
    from validador.localizar import extraer

    def fila(i, texto):
        return Fila(1, i, [Palabra(t, 10 + 60 * j, 20 * i, 60 + 60 * j, 20 * i + 8, 1, i) for j, t in enumerate(texto.split())])

    doc = DocLayout(filas=[fila(0, "ACOMPAÑANTE: MADRE IDENTIFICACION: CC 52000000"), fila(1, "IDENTIFICACION: TI 1000001")])
    assert extraer(doc, "paciente_id") == "TI 1000001"
    from validador.data_preprocessing import normalize
    assert normalize.sexo("Femenino") == "F" and normalize.sexo("MASCULINO") == "M" and normalize.sexo("otro") is None
