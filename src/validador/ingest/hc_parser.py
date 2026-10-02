"""Historia clínica (HEV): identidad del paciente y notas de atención.

No se exige igualdad de CUPS con la HC (sus órdenes suelen ser de servicios futuros).
Se extrae: identidad, fecha de cada nota, especialidad y tipo de consulta.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..base.schemas import Atencion, DocumentoExtraido
from ..data_preprocessing import normalize
from . import pdf_text
from .util import sha256

# El encabezado de identificación está arriba de la página.
MAX_Y_ENCABEZADO = 140


def extraer(ruta: Path) -> DocumentoExtraido:
    doc = DocumentoExtraido(fuente="hc", archivo=ruta.name, sha256=sha256(ruta))
    try:
        pdf = pdf_text.leer(ruta)
    except Exception as e:
        doc.errores.append(f"PDF ilegible: {type(e).__name__}")
        return doc
    return desde_pdf(pdf, doc)


def desde_pdf(pdf: pdf_text.PdfTexto, doc: DocumentoExtraido) -> DocumentoExtraido:
    doc.paginas_sin_texto = pdf.paginas_sin_texto
    if pdf.todo_escaneado:
        doc.requiere_ocr = True
        return doc
    primera = next(p for p in pdf.paginas if not p.sin_texto)
    if l := pdf_text.valor_a_la_derecha(primera, "Tipo y número de identificación:", MAX_Y_ENCABEZADO):
        doc.poner("paciente_id", normalize.documento_id_texto(l.texto), l.texto, primera.numero)
    if l := pdf_text.valor_a_la_derecha(primera, "Paciente:", MAX_Y_ENCABEZADO):
        doc.poner("paciente_nombre", normalize.nombre(l.texto), l.texto, primera.numero)
    if l := pdf_text.valor_a_la_derecha(primera, "Identificador único:", MAX_Y_ENCABEZADO):
        doc.poner("id_interno_paciente", normalize.codigo(l.texto), l.texto, primera.numero)
    doc.atenciones = atenciones(pdf)
    return doc


def atenciones(pdf: pdf_text.PdfTexto) -> list[Atencion]:
    """Cada nota empieza con 'Fecha: dd/mm/aaaa hh:mm - ...' seguida de 'Tipo de nota - ESPECIALIDAD'."""
    resultado: list[Atencion] = []
    for pagina in pdf.paginas:
        lineas = [l.strip() for l in pagina.texto.splitlines() if l.strip()]
        for i, linea in enumerate(lineas):
            if m := re.match(r"Fecha:\s*(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}\s+-", linea):
                tipo_nota = especialidad = None
                if i + 1 < len(lineas) and " - " in lineas[i + 1]:
                    partes = [p.strip() for p in lineas[i + 1].split(" - ")]
                    tipo_nota, especialidad = partes[0], partes[-1]
                resultado.append(
                    Atencion(
                        fecha=normalize.fecha(m.group(1)),
                        tipo_nota=tipo_nota,
                        especialidad=normalize.texto(especialidad),
                        pagina=pagina.numero,
                    )
                )
            elif m := re.search(r"Tipo de consulta:\s*(Primera vez|Control)", linea, re.I):
                if resultado:
                    resultado[-1].tipo_consulta = "primera_vez" if m.group(1).lower().startswith("primera") else "control"
    return resultado
