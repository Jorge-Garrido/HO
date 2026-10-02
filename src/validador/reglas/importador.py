"""Entrada de reglas al sistema: archivo YAML/JSON, propuestas del agente normativo y
reporte de cobertura contra la matriz SEMCOL.

Nada de esto escribe en la base: produce un ``ConjuntoReglas`` validado que ``store``
guarda como versión nueva (borrador). La activación es un paso aparte y humano.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

import openpyxl
import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..data_preprocessing import normalize
from .expediente import ruta_conocida
from .modelos import Cita, ConjuntoReglas, Regla


class ErrorReglas(ValueError):
    pass


def rutas_desconocidas(regla: Regla) -> list[str]:
    return sorted(r for r in regla.rutas() if not ruta_conocida(r))


def validar_conjunto(datos: dict) -> ConjuntoReglas:
    conjunto = ConjuntoReglas.model_validate(datos)
    errores = [f"{r.id}: rutas desconocidas {rutas_desconocidas(r)}" for r in conjunto.reglas if rutas_desconocidas(r)]
    if errores:
        raise ErrorReglas("; ".join(errores))
    return conjunto


def leer(ruta: Path) -> tuple[ConjuntoReglas, str]:
    """Lee un conjunto de reglas (YAML o JSON) y lo valida completo. Devuelve también su sha256."""
    contenido = ruta.read_bytes()
    datos = yaml.safe_load(contenido) if ruta.suffix.lower() in (".yaml", ".yml") else json.loads(contenido)
    return validar_conjunto(datos), hashlib.sha256(contenido).hexdigest()


def huella(conjunto: ConjuntoReglas) -> str:
    """sha256 del contenido canónico (independiente del formato del archivo de origen)."""
    canonico = json.dumps(conjunto.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# --- cobertura contra la matriz SEMCOL (hoja 06) ---------------------------------------------


def leer_matriz(ruta: Path, hoja: str = "06_Matriz_Cruces") -> list[dict[str, str]]:
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb[hoja]
    filas = []
    encabezado = None
    for fila in ws.iter_rows(values_only=True):
        if fila and fila[0] == "Regla":
            encabezado = [str(c or "").strip() for c in fila]
            continue
        if encabezado and fila and fila[0] and re.match(r"^[A-Z]+-\d{3}$", str(fila[0])):
            filas.append({encabezado[i]: str(v or "").strip() for i, v in enumerate(fila) if i < len(encabezado) and encabezado[i]})
    return filas


# Reglas de la matriz que ya cubre el cruce entre documentos (severity.py / pipeline.py): no
# son reglas del motor, son comparaciones campo a campo y línea a línea entre fuentes.
CUBIERTAS_POR_CRUCE: dict[str, str] = {
    "CONS-001": "campo numero_factura (RIPS, XML, CUV, PDF, carpeta)",
    "CONS-002": "campo nit_emisor (RIPS, XML, PDF)",
    "CONS-005": "campo paciente_id (RIPS, XML, PDF, HC, autorización)",
    "CONS-009": "campo numero_autorizacion (RIPS, XML, PDF, autorización OCR)",
    "CONS-011": "CUPS facturado vs autorización escaneada (OCR)",
    "CONS-013": "líneas por código: cantidad y valor (RIPS, XML, PDF)",
    "CONS-022": "tarifario institucional (CUPS + descripción)",
    "CONS-028": "paciente_id y nombre en HC y autorización",
}


def cobertura(conjunto: ConjuntoReglas, matriz: list[dict[str, str]]) -> list[dict[str, str]]:
    """Por cada regla de la matriz: qué reglas ejecutables la cubren y qué cubre el cruce."""
    por_id: dict[str, list[str]] = {}
    for r in conjunto.reglas:
        for m in r.matriz:
            por_id.setdefault(m, []).append(r.id)
    return [
        {
            "matriz": f["Regla"],
            "campo": f.get("Campo lógico", ""),
            "glosa": f.get("Glosa potencial", ""),
            "reglas": ", ".join(sorted(set(por_id.get(f["Regla"], [])))),
            "cruce": CUBIERTAS_POR_CRUCE.get(f["Regla"], ""),
        }
        for f in matriz
    ]


# --- propuestas del agente normativo ---------------------------------------------------------


class CampoFaltante(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruta: str
    descripcion: str
    documento: str


class CambioRegla(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accion: Literal["crear", "modificar", "derogar"]
    regla_id: str
    regla: dict | None = None  # para crear/modificar: una Regla completa
    vigencia_hasta: date | None = None  # para derogar
    justificacion: str
    cita: Cita


class PropuestaAgente(BaseModel):
    """Lo que devuelve el agente normativo (ver documentos/prompts/agente_normativo.md)."""

    model_config = ConfigDict(extra="forbid")

    norma: str
    resumen: str
    cambios: list[CambioRegla]
    campos_faltantes: list[CampoFaltante] = Field(default_factory=list)


def _plano(texto: str) -> str:
    texto = re.sub(r"-\s*\n\s*", "", texto)  # palabras cortadas al final de línea
    return re.sub(r"\s+", " ", normalize.sin_tildes(texto).upper()).strip()


def cita_verificada(cita: Cita, texto_norma: str | None) -> str | None:
    """None si la cita es verificable en el texto de la norma; si no, el motivo."""
    if not cita.texto_literal or len(cita.texto_literal.strip()) < 20:
        return "sin texto literal de al menos 20 caracteres"
    if texto_norma is None:
        return None
    if _plano(cita.texto_literal) not in _plano(texto_norma):
        return "el texto literal citado no aparece en el documento de la norma"
    return None


def aplicar_propuesta(base: ConjuntoReglas, propuesta: PropuestaAgente, texto_norma: str | None) -> tuple[ConjuntoReglas, dict]:
    """Aplica los cambios válidos sobre una copia del conjunto base.

    Un cambio se rechaza si su cita no se verifica, si la regla no pasa la validación o si usa
    rutas que el expediente no tiene (estas quedan en ``requiere_extractor``: es trabajo de
    código antes de poder usarlas).
    """
    reglas = [r.model_copy(deep=True) for r in base.reglas]
    informe: dict[str, list] = {"aplicados": [], "rechazados": [], "requiere_extractor": [c.model_dump() for c in propuesta.campos_faltantes]}

    for cambio in propuesta.cambios:
        if motivo := cita_verificada(cambio.cita, texto_norma):
            informe["rechazados"].append({"regla_id": cambio.regla_id, "accion": cambio.accion, "motivo": motivo})
            continue
        existentes = [r for r in reglas if r.id == cambio.regla_id]
        respaldo = [r.model_copy(deep=True) for r in reglas]
        try:
            if cambio.accion == "derogar":
                vigentes = [r for r in existentes if r.vigencia.hasta is None]
                if not vigentes or cambio.vigencia_hasta is None:
                    raise ErrorReglas("no hay versión vigente que derogar o falta vigencia_hasta")
                for r in vigentes:
                    r.vigencia = r.vigencia.model_copy(update={"hasta": cambio.vigencia_hasta})
                    r.notas = f"{r.notas or ''} Derogada por {propuesta.norma}: {cambio.justificacion}".strip()
            else:
                if cambio.regla is None:
                    raise ErrorReglas("falta la regla")
                nueva = Regla.model_validate({**cambio.regla, "id": cambio.regla_id})
                if desconocidas := rutas_desconocidas(nueva):
                    informe["requiere_extractor"].append({"regla_id": nueva.id, "rutas": desconocidas})
                    raise ErrorReglas(f"usa rutas que el expediente no tiene: {desconocidas}")
                if cambio.accion == "crear" and existentes:
                    nueva = nueva.model_copy(update={"version": max(r.version for r in existentes) + 1})
                if cambio.accion == "modificar":
                    if not existentes:
                        raise ErrorReglas("no existe la regla a modificar")
                    reemplazada = next((r for r in existentes if r.vigencia.se_cruza(nueva.vigencia)), None)
                    if reemplazada is None:
                        raise ErrorReglas("ninguna versión existente se cruza con la vigencia propuesta; use 'crear'")
                    reglas.remove(reemplazada)
                    nueva = nueva.model_copy(update={"version": max(r.version for r in existentes) + 1})
                reglas.append(nueva)
            # Cada cambio deja el conjunto válido (p. ej. sin vigencias cruzadas); si no, se revierte.
            ConjuntoReglas.model_validate({"nombre": base.nombre, "reglas": [r.model_dump(mode="json") for r in reglas]})
            informe["aplicados"].append({"regla_id": cambio.regla_id, "accion": cambio.accion, "cita": str(cambio.cita)})
        except (ValueError, ErrorReglas) as e:
            reglas = respaldo
            informe["rechazados"].append({"regla_id": cambio.regla_id, "accion": cambio.accion, "motivo": str(e)[:500]})

    nuevo = validar_conjunto({"nombre": base.nombre, "descripcion": base.descripcion, "reglas": [r.model_dump(mode="json") for r in reglas]})
    return nuevo, informe
