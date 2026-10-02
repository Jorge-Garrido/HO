"""Parámetros de lectura en la base: validación, alcance, aplicación por factura y ciclo de vida."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from validador import localizar
from validador.data_preprocessing.layout import DocLayout, Fila, Palabra
from validador.reglas.parametros import VOCABULARIO_ACTIVO, ConjuntoParametros, Parametros, leer
from validador.store import db, referencia

SEMILLA = Path(__file__).parent.parent / "referencia" / "parametros" / "global.yaml"


def test_semilla_reproduce_los_valores_del_codigo():
    conjunto, _ = leer(SEMILLA)
    voc = Parametros(conjunto).vocabulario()
    assert voc.etiquetas == localizar.ETIQUETAS
    assert voc.negativas == localizar.ETIQUETAS_NEGATIVAS
    assert voc.genericas == localizar.ETIQUETAS_GENERICAS
    assert voc.palabras_etiqueta == localizar.PALABRAS_ETIQUETA and voc.tipos_documento == localizar.TIPOS_DOCUMENTO


def test_parametro_invalido_no_entra():
    with pytest.raises(ValidationError, match="prefijo de 3 letras"):
        ConjuntoParametros.model_validate({"parametros": [{"tipo": "alias_soporte", "clave": "HISTORIA", "valor": "historia"}]})
    with pytest.raises(ValidationError):
        ConjuntoParametros.model_validate({"parametros": [{"tipo": "alias_soporte", "clave": "X", "valor": "HEV", "alcance": "eps:abc"}]})
    with pytest.raises(ValidationError, match="repetido"):
        ConjuntoParametros.model_validate({"parametros": [{"tipo": "alias_soporte", "clave": "X", "valor": "HEV"}] * 2})


def test_alcance_mas_especifico_gana():
    p = Parametros(ConjuntoParametros.model_validate({"parametros": [
        {"tipo": "etiqueta_campo", "clave": "paciente_nombre", "valor": ["NOMBRE"]},
        {"tipo": "etiqueta_campo", "clave": "paciente_nombre", "valor": ["BENEFICIARIO"], "alcance": "prestador:800"},
        {"tipo": "etiqueta_campo", "clave": "paciente_nombre", "valor": ["TITULAR"], "alcance": "eps:900"},
    ]}))
    assert p.vocabulario().etiquetas["paciente_nombre"] == ["NOMBRE"]
    assert p.vocabulario(prestador="800").etiquetas["paciente_nombre"] == ["BENEFICIARIO"]
    assert p.vocabulario(eps="900", prestador="800").etiquetas["paciente_nombre"] == ["TITULAR"]
    assert p.vocabulario(eps="111").etiquetas["paciente_nombre"] == ["NOMBRE"]  # otra EPS: no la afecta


def test_vocabulario_activo_cambia_la_lectura_solo_mientras_esta_activo():
    doc = DocLayout(filas=[Fila(1, 0, [Palabra(t, 10 + 60 * j, 0, 60 + 60 * j, 8, 1, 0) for j, t in enumerate("TITULAR: PEREZ GOMEZ JUAN".split())])])
    assert localizar.extraer(doc, "paciente_nombre") is None  # 'TITULAR' no es etiqueta por defecto
    conjunto, _ = leer(SEMILLA)
    datos = conjunto.model_dump()
    datos["parametros"].append({"tipo": "etiqueta_campo", "clave": "paciente_nombre", "valor": ["TITULAR"], "alcance": "eps:900"})
    p = Parametros(ConjuntoParametros.model_validate(datos))
    token = VOCABULARIO_ACTIVO.set(p.vocabulario(eps="900"))
    try:
        assert localizar.extraer(doc, "paciente_nombre") == "PEREZ GOMEZ JUAN"
    finally:
        VOCABULARIO_ACTIVO.reset(token)
    assert localizar.extraer(doc, "paciente_nombre") is None


def test_ciclo_de_vida_de_parametros(tmp_path):
    engine = db.conectar(f"sqlite:///{tmp_path / 'p.db'}")
    conjunto, _ = leer(SEMILLA)
    pid, nuevo = referencia.guardar_parametros(engine, conjunto, "ana", "archivo")
    assert nuevo and referencia.guardar_parametros(engine, conjunto, "ana", "archivo") == (pid, False)
    with pytest.raises(referencia.ErrorReferencia, match="no se puede pasar"):
        referencia.cambiar_estado(engine, pid, "vigente", "luis", entidad="parametro_conjunto")
    for estado in ("en_prueba", "aprobada", "vigente"):
        referencia.cambiar_estado(engine, pid, estado, "luis", entidad="parametro_conjunto")
    assert referencia.parametros_vigentes_id(engine) == pid
    cargados = referencia.cargar_parametros(engine)
    assert cargados.version_id == pid and cargados.vocabulario().etiquetas == localizar.ETIQUETAS
    acciones = [e["accion"] for e in referencia.bitacora(engine, "parametro_conjunto", pid)]
    assert acciones == ["creada", "prueba_sombra", "aprobada", "vigente"]
