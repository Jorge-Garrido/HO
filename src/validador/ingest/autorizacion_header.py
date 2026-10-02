"""Encabezado digital de la autorización (PDE).

El encabezado (identificador único, identificación, paciente) es texto digital; el
cuerpo con el número de autorización y el CUPS es una imagen y queda para el OCR
(Fase 2). Algunos PDE son escaneo completo, sin encabezado digital.
"""

from __future__ import annotations

from pathlib import Path

from ..base.schemas import DocumentoExtraido
from ..data_preprocessing import normalize
from . import pdf_text
from .util import sha256

MAX_Y_ENCABEZADO = 140


def extraer(ruta: Path) -> DocumentoExtraido:
    doc = DocumentoExtraido(fuente="autorizacion_digital", archivo=ruta.name, sha256=sha256(ruta))
    try:
        pdf = pdf_text.leer(ruta)
    except Exception as e:
        doc.errores.append(f"PDF ilegible: {type(e).__name__}")
        return doc
    return desde_pdf(pdf, doc)


def desde_pdf(pdf: pdf_text.PdfTexto, doc: DocumentoExtraido) -> DocumentoExtraido:
    # El número de autorización siempre está en la imagen.
    doc.requiere_ocr = True
    doc.paginas_sin_texto = pdf.paginas_sin_texto
    if not pdf.paginas or pdf.todo_escaneado:
        doc.extra["encabezado_digital"] = False
        return doc
    primera = pdf.paginas[0]
    if l := pdf_text.valor_a_la_derecha(primera, "Identificación:", MAX_Y_ENCABEZADO):
        doc.poner("paciente_id", normalize.documento_id_texto(l.texto), l.texto, 1)
    if l := pdf_text.valor_a_la_derecha(primera, "Paciente:", MAX_Y_ENCABEZADO):
        doc.poner("paciente_nombre", normalize.nombre(l.texto), l.texto, 1)
    if l := pdf_text.valor_a_la_derecha(primera, "Identificador Único:", MAX_Y_ENCABEZADO):
        doc.poner("id_interno_paciente", normalize.codigo(l.texto), l.texto, 1)
    doc.extra["encabezado_digital"] = bool(doc.campos)
    return doc
