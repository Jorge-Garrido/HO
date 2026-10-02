"""Autorización escaneada (PDE) leída con la cascada de OCR.

Para cada campo se intenta, en orden:
  1. confirmar alguno de los valores esperados (los de RIPS/XML);
  2. si no, tomar el valor que dos lecturas independientes coinciden en ver (diferencia real);
  3. si no, extraer lo que hay junto a la etiqueta del campo.
Los CUPS facturados se verifican uno por uno.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..data_preprocessing import normalize
from ..compare import levenshtein, similitud_tokens
from ..tarifario.matcher import sin_categoria
from ..ocr import cascada
from ..base.schemas import CampoExtraido, DocumentoExtraido

CAMPOS = ("numero_autorizacion", "paciente_id")
ETAPA = {"paddle": "paddle", "paddle_recorte": "paddle", "tesseract": "tesseract", "digitos": "digitos"}


def _formato(campo: str, leido: str, esperados: set[str]) -> str:
    if campo == "paciente_id":
        tipo = next((e.split()[0] for e in esperados if " " in e), "")
        numero = re.sub(r"^[A-Z]+", "", leido.replace(" ", ""))
        return normalize.documento_id(tipo, numero) or leido
    return leido


def extraer(ruta: Path, esperados: dict[str, set[str]], cups: dict[str, str | None], cache_dir: Path | None) -> DocumentoExtraido:
    doc = DocumentoExtraido(fuente="autorizacion_scan", archivo=ruta.name)
    try:
        esc = cascada.leer_pdf(ruta, cache_dir)
    except Exception as e:  # imagen corrupta, etc.
        doc.errores.append(f"OCR falló: {type(e).__name__}")
        doc.requiere_ocr = True
        return doc
    doc.extra["ocr_ms"] = str(esc.ms_ocr)
    for campo in CAMPOS:
        valores = {v for v in esperados.get(campo, set()) if v}
        resoluciones = [cascada.resolver(esc, campo, v) for v in sorted(valores)]
        elegido = next((r for r in resoluciones if r.estado == "confirmado"), None)
        if elegido is None:
            elegido = next((r for r in resoluciones if r.estado == "diferencia" and _plausible(campo, r)), None)
        if elegido is None and campo == "numero_autorizacion":
            # Ninguna fuente digital coincide: se lee lo que hay junto a la etiqueta.
            # (Para el documento del paciente esta extracción es muy ruidosa y no se usa.)
            elegido = cascada.leer_valor(esc, campo)
        if elegido is None or not elegido.leido:
            doc.extra[f"no_legible:{campo}"] = True
            continue
        valor = elegido.esperado if elegido.estado == "confirmado" and elegido.esperado in valores else _formato(campo, elegido.leido, valores)
        doc.campos[campo] = CampoExtraido(
            campo=campo,
            valor=valor,
            valor_crudo=" | ".join(f"{k}: {v}" for k, v in elegido.lecturas.items())[:500],
            fuente="autorizacion_scan",
            confianza=elegido.confianza,
            etapa=ETAPA.get(elegido.etapa or "paddle", "paddle"),
        )
        doc.extra[f"detalle:{campo}"] = elegido.detalle or elegido.estado
        if elegido.estado == "no_legible":
            doc.extra[f"no_concluyente:{campo}"] = True  # un solo motor: se reporta pero va a revisión
    for codigo in sorted(cups):
        estado = cascada.resolver(esc, "cups", codigo).estado
        if estado != "confirmado" and _por_descripcion(esc, cups[codigo]):
            estado = "confirmado"  # la autorización nombra el servicio sin código (p. ej. portal de la EPS)
            doc.extra[f"cups_por_descripcion:{codigo}"] = True
        doc.extra[f"cups:{codigo}"] = estado
    doc.extra["cups_escaneo"] = ",".join(cascada.codigos_cups(esc))
    return doc


def _por_descripcion(esc: cascada.DocumentoEscaneado, descripcion: str | None, minimo: float = 0.75) -> bool:
    """¿Alguna línea de la autorización nombra el mismo servicio? (sin el sufijo de categoría)."""
    if not descripcion:
        return False
    objetivo = sin_categoria(descripcion)
    for fila in esc.layout.filas:
        texto = " ".join(p.texto for p in fila.palabras)
        if len(texto) >= 15 and similitud_tokens(objetivo, texto) >= minimo:
            return True
    return False


def _plausible(campo: str, r: cascada.Resolucion) -> bool:
    """Una lectura distinta del documento del paciente solo cuenta si está cerca del esperado
    (a más distancia suele ser otro número de la página, no el documento mal digitado)."""
    if campo != "paciente_id" or not r.leido:
        return True
    return levenshtein(cascada._objetivo(campo, r.esperado), r.leido) <= 2
