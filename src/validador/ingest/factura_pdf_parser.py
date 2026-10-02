"""Representación gráfica de la factura (FEV): campos del encabezado y tabla de ítems."""

from __future__ import annotations

import re
from pathlib import Path

from ..base.schemas import DocumentoExtraido, LineaServicio
from ..data_preprocessing import normalize
from . import pdf_text
from .util import sha256
from .xml_parser import codigo_de_linea, limpiar_descripcion


def extraer(ruta: Path) -> DocumentoExtraido:
    doc = DocumentoExtraido(fuente="pdf_factura", archivo=ruta.name, sha256=sha256(ruta))
    try:
        pdf = pdf_text.leer(ruta)
    except Exception as e:  # PDF corrupto o protegido
        doc.errores.append(f"PDF ilegible: {type(e).__name__}")
        return doc
    return desde_pdf(pdf, doc)


def desde_pdf(pdf: pdf_text.PdfTexto, doc: DocumentoExtraido) -> DocumentoExtraido:
    doc.paginas_sin_texto = pdf.paginas_sin_texto
    if pdf.todo_escaneado:
        doc.requiere_ocr = True
        doc.errores.append("factura PDF sin texto digital")
        return doc
    primera = pdf.paginas[0].texto
    t = pdf.texto

    if m := re.search(r"FACTURA ELECTR[ÓO]NICA DE VENTA No\.\s*(\S+)", primera):
        doc.poner("numero_factura", normalize.codigo(m.group(1)), pagina=1)
    if m := re.search(r"CUFE\s*:\s*([0-9a-fA-F\s]{96,140})", primera):
        cufe = re.sub(r"\s", "", m.group(1))[:96].lower()
        doc.poner("cufe", cufe, m.group(0).strip(), pagina=1)
    if m := re.search(r"\bNIT\.?\s+(\d[\d.]+-?\d?)", primera):
        doc.poner("nit_emisor", normalize.nit(m.group(1)), m.group(1), pagina=1)
    if m := re.search(r"NIT/ID:\s*([\d.\-]+)", primera):
        doc.poner("nit_eps", normalize.nit(m.group(1)), m.group(1), pagina=1)
    if m := re.search(r"CONSECUTIVO INTERNO:\s*(\d+)(?:-\d+)?", primera):
        doc.poner("id_interno_paciente", m.group(1), m.group(0), pagina=1)
    if m := re.search(r"PACIENTE:\s*([^\n]+)", primera):
        doc.poner("paciente_nombre", normalize.nombre(m.group(1)), m.group(1).strip(), pagina=1)
    if m := re.search(r"(?<![A-Za-z])ID:\s*([A-Z]{2,3})\s+([0-9A-Z]+)", primera):
        doc.poner("paciente_id", normalize.documento_id(m.group(1), m.group(2)), m.group(0), pagina=1)
    if m := re.search(r"AUTORIZACI[ÓO]N:\s*([^\n]*)", primera):
        valores = re.findall(r"[0-9A-Za-z\-]+", m.group(1))
        doc.poner("numero_autorizacion", ",".join(sorted({normalize.codigo(v) for v in valores})) or None, m.group(1), 1)
    if m := re.search(r"FECHA INGRESO:\s*(\d{4}-\d{2}-\d{2})", primera):
        doc.poner("fecha_ingreso", m.group(1), pagina=1)
    if m := re.search(r"FECHA EGRESO:\s*(\d{4}-\d{2}-\d{2})", primera):
        doc.poner("fecha_egreso", m.group(1), pagina=1)

    total = pdf_text.valor_a_la_derecha(pdf.paginas[0], "TOTAL A PAGAR")
    if total:
        doc.poner("valor_total", normalize.dinero_texto(normalize.dinero(total.texto)), total.texto, pagina=1)

    doc.lineas = _items(pdf)
    if not doc.lineas and "CODIGO" in t:
        doc.errores.append("no se pudieron leer los ítems de la tabla")
    return doc


def _items(pdf: pdf_text.PdfTexto) -> list[LineaServicio]:
    """Lee la tabla ITEM | CODIGO | DESCRIPCIÓN | CANTIDAD | VALOR UNITARIO | %IMP | VALOR TOTAL por posición."""
    lineas: list[LineaServicio] = []
    for pagina in pdf.paginas:
        cab = {(normalize.texto(l.texto) or "").replace(" ", ""): l for l in pagina.lineas}
        codigo_h, cant_h, unit_h, imp_h, tot_h = (
            cab.get("CODIGO"), cab.get("CANTIDAD"), cab.get("VALORUNITARIO"), cab.get("%IMP"), cab.get("VALORTOTAL")
        )
        obs = cab.get("OBSERVACIONES:")
        if not (codigo_h and cant_h and unit_h and imp_h and tot_h):
            continue
        y_ini = codigo_h.bbox[3]
        y_fin = obs.y0 if obs else float("inf")
        celdas = sorted(
            (l for l in pagina.lineas if y_ini <= l.y0 < y_fin and not re.match(r"\s*TOTAL L[ÍI]NEAS", l.texto)),
            key=lambda l: (round(l.y0), l.x0),
        )

        def columna(l: pdf_text.Linea) -> str:
            if l.x0 < codigo_h.x0 - 5:
                return "item"
            if l.x0 < codigo_h.bbox[2] + 10:
                return "codigo"
            if l.x0 < cant_h.x0 - 5:
                return "descripcion"
            if l.x0 < unit_h.x0:
                return "cantidad"
            if l.x0 < imp_h.x0:
                return "unitario"
            if l.x0 < tot_h.x0:
                return "imp"
            return "total"

        filas: list[dict[str, list[str]]] = []
        for l in celdas:
            col = columna(l)
            if col == "item" and re.fullmatch(r"\s*\d+\s*", l.texto):
                filas.append({})
            if not filas:
                continue
            filas[-1].setdefault(col, []).append(l.texto.strip())
        for f in filas:
            descripcion = " ".join(f.get("descripcion", []))
            codigo_crudo = " ".join(f.get("codigo", [])) or None
            codigo, origen = codigo_de_linea(codigo_crudo, descripcion)
            valor = normalize.dinero((f.get("total") or [None])[0])
            if valor is None:
                continue
            lineas.append(
                LineaServicio(
                    codigo=codigo,
                    origen_codigo=origen.replace("xml.", "pdf_factura."),
                    descripcion=limpiar_descripcion(descripcion),
                    cantidad=normalize.dinero((f.get("cantidad") or ["1"])[0]) or 1,
                    valor_total=valor,
                    fuente="pdf_factura",
                )
            )
    return lineas
