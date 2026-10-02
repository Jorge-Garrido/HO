"""Respuesta de validación del Ministerio (CUV). ``ResultadosValidacion`` puede no venir."""

from __future__ import annotations

import json
from pathlib import Path

from ..base.schemas import CodigoValidacion, DocumentoExtraido
from ..data_preprocessing import normalize
from .util import sha256


def extraer(ruta: Path) -> DocumentoExtraido:
    doc = DocumentoExtraido(fuente="cuv", archivo=ruta.name, sha256=sha256(ruta))
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        doc.errores.append(f"CUV ilegible: {type(e).__name__}")
        return doc
    return desde_dict(datos, doc)


def desde_dict(datos: dict, doc: DocumentoExtraido | None = None) -> DocumentoExtraido:
    doc = doc or DocumentoExtraido(fuente="cuv")
    doc.poner("numero_factura", normalize.codigo(datos.get("NumFactura")))
    doc.extra["result_state"] = datos.get("ResultState")
    doc.extra["cuv"] = datos.get("CodigoUnicoValidacion")
    doc.extra["fecha_radicacion"] = datos.get("FechaRadicacion")
    doc.extra["proceso_id"] = str(datos["ProcesoId"]) if datos.get("ProcesoId") is not None else None
    for r in datos.get("ResultadosValidacion") or []:
        doc.codigos.append(
            CodigoValidacion(
                origen="cuv",
                codigo=str(r.get("Codigo")),
                clase=r.get("Clase"),
                descripcion=r.get("Descripcion"),
                # PathFuente no es consistente (procedimiento/procedimientos): solo informativo.
                ruta=r.get("PathFuente") or None,
            )
        )
    return doc
