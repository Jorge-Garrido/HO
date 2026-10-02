"""Motor de reglas normativas: modelos, primitivas, motor, ciclo de vida en base y agente."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from fabrica import Caso, carpeta
from validador.base.config import Config
from validador.base.schemas import CodigoValidacion, DocumentoExtraido
from validador.ingest import rips
from validador.pipeline import procesar_carpeta
from validador.reglas import condicion, expediente, importador
from validador.reglas.catalogos import Catalogos, ItemCatalogo, VersionCatalogo
from validador.reglas.contratos import Contrato, Contratos
from validador.reglas.modelos import ConjuntoReglas, Regla
from validador.reglas.motor import ConjuntoCargado, MotorReglas
from validador.store import db, referencia

RAIZ = Path(__file__).parent.parent
SEMILLA = RAIZ / "referencia" / "reglas" / "normativa.yaml"
NIT_EPS = "900000002"


def rips_base(**servicio) -> dict:
    s = {
        "codPrestador": "150010038701", "fechaInicioAtencion": "2026-07-31 10:00", "numAutorizacion": "17458248",
        "codConsulta": "890244", "codDiagnosticoPrincipal": "E119", "codDiagnosticoRelacionado1": None,
        "codDiagnosticoRelacionado2": None, "codDiagnosticoRelacionado3": None, "vrServicio": 214000,
        "valorPagoModerador": 0, "numFEVPagoModerador": None, "consecutivo": 1,
    }
    s.update(servicio)
    return {
        "numDocumentoIdObligado": "800000001", "numFactura": "FE0000001",
        "usuarios": [{
            "tipoDocumentoIdentificacion": "CC", "numDocumentoIdentificacion": "1000001", "fechaNacimiento": "1980-01-01",
            "codSexo": "M", "codPaisResidencia": "170", "codZonaTerritorialResidencia": "01", "tipoUsuario": "04",
            "consecutivo": 1, "servicios": {"consultas": [s]},
        }],
    }


def docs_de(datos_rips: dict, cuv: list[str] | None = None, hc_texto: str | None = "ANAMNESIS EXAMEN FISICO") -> dict:
    x = DocumentoExtraido(fuente="xml")
    x.poner("nit_eps", NIT_EPS)
    x.poner("fecha_emision", "2026-08-01")
    x.extra.update({"periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-31", "codigo_prestador": "1500100387",
                    "modalidad_pago": "Pago por evento"})
    docs = {"rips": rips.desde_dict(copy.deepcopy(datos_rips)), "xml": x}
    if cuv is not None:
        docs["cuv"] = DocumentoExtraido(fuente="cuv", codigos=[CodigoValidacion(origen="cuv", codigo=c) for c in cuv])
    if hc_texto is not None:
        docs["hc"] = DocumentoExtraido(fuente="hc", texto=hc_texto)
    return docs


PRESENTES = {"FEV": ["x"], "HEV": ["x"], "XML": ["x"], "RIPS": ["x"]}


def regla(**cambios) -> dict:
    base = {
        "id": "PRUEBA-001", "nombre": "prueba", "norma": {"documento": "Norma de prueba"}, "vigencia": {"desde": "2026-01-01"},
        "alcance": "servicio", "primitiva": "rango",
        "params": {"valor": "servicio.fecha", "desde": "factura.periodo_inicio", "hasta": "factura.periodo_fin"},
        "resultado": {"tipo": "rechazo_muv", "severidad": "grave", "mensaje": "fuera del periodo"},
    }
    base.update(cambios)
    return base


def motor(*reglas: dict, catalogos: Catalogos | None = None, contratos: Contratos | None = None) -> MotorReglas:
    conjunto = ConjuntoReglas(nombre="prueba", reglas=[Regla.model_validate(r) for r in reglas])
    return MotorReglas([ConjuntoCargado(conjunto, 7)], catalogos, contratos)


def estado(m: MotorReglas, docs: dict, regla_id: str = "PRUEBA-001") -> str:
    _, res = m.evaluar(docs, PRESENTES)
    return next(e for e, ids in res.por_estado.items() if regla_id in ids)


# --- modelos e importación -------------------------------------------------------------------


def test_semilla_carga_y_todas_sus_rutas_existen():
    conjunto, sha = importador.leer(SEMILLA)
    assert len(conjunto.reglas) >= 20 and len(sha) == 64
    for r in conjunto.reglas:
        assert importador.rutas_desconocidas(r) == [], r.id


def test_regla_con_primitiva_o_parametros_invalidos_no_entra():
    with pytest.raises(ValidationError, match="primitiva desconocida"):
        Regla.model_validate(regla(primitiva="inventada"))
    with pytest.raises(ValidationError):
        Regla.model_validate(regla(params={"valor": "servicio.fecha"}))  # rango sin límites
    with pytest.raises(ValidationError, match="operador desconocido"):
        Regla.model_validate(regla(cuando={"eval": "1"}))


def test_ruta_desconocida_se_rechaza_al_importar():
    with pytest.raises(importador.ErrorReglas, match="rutas desconocidas"):
        importador.validar_conjunto({"nombre": "prueba", "reglas": [regla(params={"valor": "servicio.fecha", "desde": "factura.no_existe"})]})


def test_versiones_de_una_regla_conviven_solo_sin_cruce_de_vigencias():
    antigua = regla(version=1, vigencia={"desde": "2024-01-01", "hasta": "2026-05-13"})
    nueva = regla(version=2, vigencia={"desde": "2026-05-14"})
    ConjuntoReglas.model_validate({"nombre": "prueba", "reglas": [antigua, nueva]})
    with pytest.raises(ValidationError, match="se cruzan"):
        ConjuntoReglas.model_validate({"nombre": "prueba", "reglas": [regla(version=1), regla(version=2)]})


def test_condicion_tres_valores():
    ctx = expediente.Contexto({"a": 5, "b": None})
    assert condicion.evaluar({">": [{"var": "a"}, 3]}, ctx) is True
    assert condicion.evaluar({"ausente": "b"}, ctx) is True
    with pytest.raises(condicion.Indeterminado):
        condicion.evaluar({"==": [{"var": "b"}, 1]}, ctx)
    # or con una rama verdadera no depende del dato faltante
    assert condicion.evaluar({"or": [{"==": [{"var": "b"}, 1]}, {"==": [{"var": "a"}, 5]}]}, ctx) is True


# --- motor -------------------------------------------------------------------------------------------


def test_regla_cumple_y_falla_con_referencia_normativa():
    m = motor(regla(resultado={"tipo": "rechazo_muv", "severidad": "grave", "codigo_glosa": "S06101", "mensaje": "fuera"}))
    assert estado(m, docs_de(rips_base())) == "cumple"
    hallazgos, res = m.evaluar(docs_de(rips_base(fechaInicioAtencion="2026-08-05 10:00")), PRESENTES)
    assert res.por_estado["falla"] == ["PRUEBA-001"] and res.conjuntos == {"prueba": 7}
    h = hallazgos[0]
    assert h.tipo == "norma" and h.resultado == "grave"
    assert h.regla.id == "PRUEBA-001" and h.regla.codigo_glosa == "S06101" and h.regla.conjunto_version_id == 7
    assert "consultas #1 (890244)" in h.motivo


def test_vigencia_por_fecha_de_atencion():
    m = motor(regla(vigencia={"desde": "2026-08-01"}))
    assert estado(m, docs_de(rips_base(fechaInicioAtencion="2026-08-05 10:00"))) == "falla"
    assert estado(m, docs_de(rips_base())) == "no_aplica"  # atención del 31-jul: la regla aún no regía


def test_falla_ya_reportada_por_el_muv_no_se_duplica():
    r = regla(resultado={"tipo": "rechazo_muv", "severidad": "grave", "mensaje": "x", "cuv_equivalente": ["RVC999"]})
    docs = docs_de(rips_base(fechaInicioAtencion="2026-08-05 10:00"), cuv=["RVC999"])
    hallazgos, res = motor(r).evaluar(docs, PRESENTES)
    assert hallazgos == [] and res.por_estado["reportada_muv"] == ["PRUEBA-001"]


def test_sin_dato_es_no_evaluable_y_no_error():
    m = motor(regla(cuando={"==": [{"var": "derivado.requiere_autorizacion"}, True]}, primitiva="requerido",
                    params={"campos": ["servicio.numAutorizacion"]}))
    hallazgos, res = m.evaluar(docs_de(rips_base(numAutorizacion=None)), PRESENTES)
    assert hallazgos == [] and "derivado.requiere_autorizacion" in res.no_evaluables["PRUEBA-001"]


def test_sin_rips_las_reglas_por_servicio_no_son_evaluables():
    docs = docs_de(rips_base())
    del docs["rips"]
    assert estado(motor(regla()), docs) == "no_evaluable"


def test_catalogo_cargado_hace_evaluable_la_regla():
    r = regla(primitiva="relacion_catalogo", params={
        "catalogo": "CIE10", "codigo": "servicio.codDiagnosticoPrincipal",
        "restricciones": [{"atributo": "sexo", "op": "==", "valor": "usuario.codSexo", "comodines": ["A"]},
                          {"atributo": "edad_minima", "op": "<=", "valor": "derivado.edad_anios", "tipo": "numero"}]})
    docs = docs_de(rips_base(codDiagnosticoPrincipal="O800"))
    assert estado(motor(r), docs) == "no_evaluable"
    cie = VersionCatalogo("CIE10", 3, date(2026, 1, 1), None, {
        "O800": ItemCatalogo("O800", "Parto", {"sexo": "F", "edad_minima": 10}),
        "E119": ItemCatalogo("E119", "Diabetes", {"sexo": "A"}),
    })
    m = motor(r, catalogos=Catalogos([cie]))
    hallazgos, res = m.evaluar(docs, PRESENTES)
    assert res.por_estado["falla"] == ["PRUEBA-001"] and res.catalogos == {"CIE10": 3}
    assert "sexo=F" in hallazgos[0].motivo and "1980" not in hallazgos[0].motivo  # no expone la fecha de nacimiento
    assert estado(m, docs_de(rips_base())) == "cumple"  # E119: sexo A (ambos)


def test_contrato_define_si_se_requiere_autorizacion():
    r = regla(cuando={"==": [{"var": "derivado.requiere_autorizacion"}, True]}, primitiva="requerido",
              params={"campos": ["servicio.numAutorizacion"]})
    contrato = Contrato.model_validate({
        "eps_nit": NIT_EPS, "eps_nombre": "EPS", "vigencia": {"desde": "2026-01-01"},
        "autorizacion": {"requiere": "todos", "cups_excentos": ["890201"]},
    })
    m = motor(r, contratos=Contratos([(contrato, 11)]))
    hallazgos, res = m.evaluar(docs_de(rips_base(numAutorizacion=None)), PRESENTES)
    assert res.por_estado["falla"] == ["PRUEBA-001"] and res.contrato_version_id == 11
    assert estado(m, docs_de(rips_base(numAutorizacion=None, codConsulta="890201"))) == "no_aplica"  # exento
    otra_eps = docs_de(rips_base(numAutorizacion=None))
    otra_eps["xml"].poner("nit_eps", "811111111")
    assert estado(m, otra_eps) == "no_evaluable"  # sin contrato para esa EPS


def test_semilla_sobre_caso_correcto_no_produce_hallazgos():
    conjunto, _ = importador.leer(SEMILLA)
    texto = "ANAMNESIS REVISION POR SISTEMAS ANTECEDENTES EXAMEN FISICO ANALISIS DIAGNOSTICO PLAN DE MANEJO"
    hallazgos, res = MotorReglas([ConjuntoCargado(conjunto, None)]).evaluar(docs_de(rips_base(), hc_texto=texto), PRESENTES)
    assert hallazgos == [], [h.motivo for h in hallazgos]
    assert "RIPS-FECHA-PERIODO" in res.por_estado["cumple"]
    assert "CIE10-EN-CATALOGO" in res.no_evaluables


def test_semilla_detecta_hc_sin_contenido_minimo():
    conjunto, _ = importador.leer(SEMILLA)
    hallazgos, _ = MotorReglas([ConjuntoCargado(conjunto, None)]).evaluar(docs_de(rips_base(), hc_texto="CONSENTIMIENTO INFORMADO"), PRESENTES)
    h = next(h for h in hallazgos if h.regla.id == "HC-CONTENIDO-MINIMO")
    assert h.regla.codigo_glosa == "S00201" and "examen_fisico" in h.motivo


# --- pipeline -------------------------------------------------------------------------------------------


def test_pipeline_guarda_resumen_de_reglas_sin_datos_internos(tmp_path):
    cfg = Config(db_url=f"sqlite:///{tmp_path / 't.db'}", ocr_habilitado=False)
    r = procesar_carpeta(carpeta(tmp_path, Caso()), "c1", cfg, motor=motor(regla()))
    assert r.reglas is not None and r.reglas.conjuntos == {"prueba": 7}
    texto = r.model_dump_json()
    assert '"datos"' not in texto and '"texto"' not in texto  # el RIPS completo y la HC no se serializan


# --- base de datos: ciclo de vida ----------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    return db.conectar(f"sqlite:///{tmp_path / 'ref.db'}")


def test_ciclo_de_vida_y_auditoria(engine):
    c1 = ConjuntoReglas(nombre="normativa", reglas=[Regla.model_validate(regla())])
    id1, nuevo = referencia.guardar_conjunto(engine, c1, "ana", "archivo")
    assert nuevo and referencia.guardar_conjunto(engine, c1, "ana", "archivo") == (id1, False)  # mismo contenido

    # De borrador no se salta a aprobada ni a vigente: primero la prueba en sombra.
    for salto in ("aprobada", "vigente"):
        with pytest.raises(referencia.ErrorReferencia, match="no se puede pasar"):
            referencia.cambiar_estado(engine, id1, salto, "luis")
    referencia.cambiar_estado(engine, id1, "en_prueba", "ana", {"facturas": 3})
    with pytest.raises(referencia.ErrorReferencia, match="distinto"):
        referencia.cambiar_estado(engine, id1, "aprobada", "ana", cuatro_ojos=True)
    referencia.cambiar_estado(engine, id1, "aprobada", "luis", cuatro_ojos=True)
    referencia.cambiar_estado(engine, id1, "vigente", "luis")
    assert referencia.vigente_id(engine, "normativa") == id1

    c2 = ConjuntoReglas(nombre="normativa", reglas=[Regla.model_validate(regla(version=2))])
    id2, _ = referencia.guardar_conjunto(engine, c2, "ana", "archivo", base_id=id1)
    for e in ("en_prueba", "aprobada", "vigente"):
        referencia.cambiar_estado(engine, id2, e, "luis")
    estados = {f["id"]: f["estado"] for f in referencia.listar_conjuntos(engine)}
    assert estados == {id1: "retirada", id2: "vigente"}  # una sola vigente por conjunto
    assert [e["accion"] for e in referencia.bitacora(engine, "regla_conjunto", id1)] == ["creada", "prueba_sombra", "aprobada", "vigente", "retirada"]
    assert [h["version"] for h in referencia.historia_regla(engine, "PRUEBA-001")] == [1, 2]

    m = referencia.cargar_motor(engine)
    assert [c.version_id for c in m.conjuntos] == [id2]

    # Volver atrás: el contenido retirado se puede crear de nuevo como versión nueva.
    id3, nuevo = referencia.guardar_conjunto(engine, c1, "ana", "restauracion", base_id=id2)
    assert nuevo and id3 not in (id1, id2)


def test_catalogo_y_contrato_versionados(engine, tmp_path):
    from validador.reglas import catalogos, contratos

    csv = tmp_path / "cie.csv"
    csv.write_text("codigo;descripcion;sexo\nO800;Parto;F\nE119;Diabetes;A\n", encoding="utf-8")
    res = catalogos.importar(csv, "cie10")
    vid, nuevo = referencia.guardar_catalogo(engine, res, "ana", "prueba", None, date(2026, 1, 1), None)
    assert nuevo and referencia.guardar_catalogo(engine, res, "ana", "prueba", None, None, None) == (vid, False)
    cat = referencia.cargar_catalogos(engine)
    assert cat.version("CIE10", date(2026, 7, 1)).items["O800"].atributos == {"sexo": "F"}
    assert cat.version("CIE10", date(2025, 7, 1)) is None  # antes de su vigencia

    contrato, sha = contratos.leer(RAIZ / "referencia" / "contratos" / "EJEMPLO_contrato.yaml")
    c1, _ = referencia.guardar_contrato(engine, contrato, sha, "ana")
    cambiado = contrato.model_copy(update={"modalidad_pago": "Capitación"})
    c2, _ = referencia.guardar_contrato(engine, cambiado, "otro-sha", "ana")
    activos = [f["id"] for f in referencia.listar_contratos(engine) if f["activo"]]
    assert activos == [c2] and c1 != c2


# --- agente normativo --------------------------------------------------------------------------------------


def _propuesta(**cambio) -> importador.PropuestaAgente:
    base = {"accion": "crear", "regla_id": "NUEVA-001", "regla": regla(id="NUEVA-001"), "justificacion": "artículo nuevo",
            "cita": {"documento": "Res. X", "texto_literal": "La fecha de atención debe estar dentro del periodo facturado."}}
    base.update(cambio)
    return importador.PropuestaAgente(norma="Res. X", resumen="prueba", cambios=[base])


def test_agente_cita_verificada_se_aplica():
    base = ConjuntoReglas(nombre="normativa", reglas=[Regla.model_validate(regla())])
    norma = "Artículo 3. La fecha de atención debe estar\ndentro del periodo facturado. Artículo 4..."
    nuevo, informe = importador.aplicar_propuesta(base, _propuesta(), norma)
    assert [r.id for r in nuevo.reglas] == ["PRUEBA-001", "NUEVA-001"] and informe["rechazados"] == []


def test_agente_cita_inventada_o_ruta_inexistente_se_rechaza():
    base = ConjuntoReglas(nombre="normativa", reglas=[Regla.model_validate(regla())])
    nuevo, informe = importador.aplicar_propuesta(base, _propuesta(), "otro texto que no contiene la cita")
    assert len(nuevo.reglas) == 1 and "no aparece" in informe["rechazados"][0]["motivo"]

    sin_ruta = regla(id="NUEVA-001", params={"valor": "hc.profesional_documento", "desde": "factura.periodo_inicio"})
    nuevo, informe = importador.aplicar_propuesta(base, _propuesta(regla=sin_ruta), None)
    assert len(nuevo.reglas) == 1 and informe["requiere_extractor"][0]["rutas"] == ["hc.profesional_documento"]


def test_agente_deroga_sin_borrar_historia():
    base = ConjuntoReglas(nombre="normativa", reglas=[Regla.model_validate(regla())])
    cita = {"documento": "Res. X", "texto_literal": "Deróguese la obligación de reportar el periodo facturado."}
    nuevo, informe = importador.aplicar_propuesta(
        base, _propuesta(accion="derogar", regla_id="PRUEBA-001", regla=None, vigencia_hasta="2026-12-31", cita=cita), None
    )
    assert informe["aplicados"] and nuevo.reglas[0].vigencia.hasta == date(2026, 12, 31)
