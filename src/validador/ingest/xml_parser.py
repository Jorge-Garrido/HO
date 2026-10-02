"""AttachedDocument (UBL 2.1 DIAN): respaldo del RIPS y fuente del CUFE.

El ``Invoice`` y el ``ApplicationResponse`` vienen como texto (CDATA) dentro de
``cbc:Description``: se parsea el contenedor y luego cada documento embebido.
"""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from ..base.schemas import CodigoValidacion, DocumentoExtraido, LineaServicio
from ..data_preprocessing import normalize
from .util import sha256

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}
# Respuesta DIAN sin novedad.
CODIGOS_DIAN_SIN_NOVEDAD = {"0000"}

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def _uno(nodo, xpath: str) -> str | None:
    r = nodo.xpath(xpath, namespaces=NS)
    return r[0].strip() if r and isinstance(r[0], str) and r[0].strip() else None


def embebidos(raiz) -> dict[str, etree._Element]:
    """Devuelve {'Invoice': ..., 'ApplicationResponse': ...} a partir de los CDATA."""
    docs: dict[str, etree._Element] = {}
    for texto in raiz.xpath("//cbc:Description/text()", namespaces=NS):
        t = texto.strip()
        if not t.startswith("<"):
            continue
        try:
            x = etree.fromstring(t.encode("utf-8"), parser=_PARSER)
        except etree.XMLSyntaxError:
            continue
        docs.setdefault(etree.QName(x).localname, x)
    return docs


def codigo_de_linea(buyers: str | None, descripcion: str | None) -> tuple[str | None, str]:
    """Regla del CUPS por ítem.

    - ``BuyersItemIdentification`` de 6 dígitos: es el CUPS (con sufijo ``-N`` de variante, también).
    - Si no, y la descripción empieza con 6 dígitos: esos son el CUPS (el ID es un código interno).
    - Si no, se usa el ID tal cual (CUM de medicamentos, códigos de insumos como S55114).
    """
    b = normalize.codigo(buyers)
    if b and re.fullmatch(r"\d{6}", b):
        return b, "xml.buyers"
    if b and (m := re.fullmatch(r"(\d{6})-\d{1,3}", b)):
        return m.group(1), "xml.buyers_variante"  # CUPS con sufijo de variante interna: 890202-2
    if descripcion and (m := re.match(r"\s*(\d{6})\b", descripcion)):
        return m.group(1), "xml.descripcion"
    return b, "xml.buyers" if b else "ninguno"


def limpiar_descripcion(descripcion: str | None) -> str | None:
    """Quita el CUPS inicial. El sufijo de categoría ('-CONSULTA - INTERCON') se deja: lo maneja el comparador."""
    if not descripcion:
        return None
    return re.sub(r"^\s*\d{6}\s+", "", descripcion).strip() or None


def _notas(invoice) -> str:
    return " | ".join(n.strip() for n in invoice.xpath("/*/cbc:Note/text()", namespaces=NS))


def extraer(ruta: Path) -> list[DocumentoExtraido]:
    """Devuelve el documento 'xml' y el 'xml_nota' (autorización escrita en la nota)."""
    doc = DocumentoExtraido(fuente="xml", archivo=ruta.name, sha256=sha256(ruta))
    nota = DocumentoExtraido(fuente="xml_nota", archivo=ruta.name)
    try:
        raiz = etree.parse(str(ruta), parser=_PARSER).getroot()
    except (OSError, etree.XMLSyntaxError) as e:
        doc.errores.append(f"XML ilegible: {type(e).__name__}")
        return [doc, nota]
    return desde_raiz(raiz, doc, nota)


def desde_bytes(contenido: bytes) -> list[DocumentoExtraido]:
    raiz = etree.fromstring(contenido, parser=_PARSER)
    return desde_raiz(raiz, DocumentoExtraido(fuente="xml"), DocumentoExtraido(fuente="xml_nota"))


def desde_raiz(raiz, doc: DocumentoExtraido, nota: DocumentoExtraido) -> list[DocumentoExtraido]:
    docs = embebidos(raiz)
    invoice = docs.get("Invoice")
    if invoice is None:
        if etree.QName(raiz).localname == "Invoice":
            invoice = raiz
        else:
            doc.errores.append("no se encontró el Invoice embebido")
            return [doc, nota]

    doc.poner("numero_factura", normalize.codigo(_uno(invoice, "/*/cbc:ID/text()")))
    cufe = _uno(invoice, "/*/cbc:UUID/text()")
    doc.poner("cufe", cufe.lower() if cufe else None, cufe)
    doc.poner("fecha_emision", _uno(invoice, "/*/cbc:IssueDate/text()"))
    nit_e = _uno(invoice, "//cac:AccountingSupplierParty//cac:PartyTaxScheme/cbc:CompanyID/text()")
    nit_c = _uno(invoice, "//cac:AccountingCustomerParty//cac:PartyTaxScheme/cbc:CompanyID/text()")
    doc.poner("nit_emisor", normalize.nit(nit_e), nit_e)
    doc.poner("nit_eps", normalize.nit(nit_c), nit_c)
    pagar = normalize.dinero(_uno(invoice, "//cac:LegalMonetaryTotal/cbc:PayableAmount/text()"))
    doc.poner("valor_total", normalize.dinero_texto(pagar))

    aaj = invoice.xpath(
        "/*/cac:AdditionalDocumentReference[cbc:DocumentTypeCode='AAJ']/cbc:ID/text()", namespaces=NS
    )
    doc.poner("numero_autorizacion", ",".join(sorted({normalize.codigo(a) for a in aaj if a.strip()})) or None)

    texto_notas = _notas(invoice)
    if m := re.search(r"PACIENTE:\s*([^|]+)", texto_notas):
        doc.poner("paciente_nombre", normalize.nombre(m.group(1)), m.group(1).strip())
    if m := re.search(r"(?<![A-Za-z])ID:\s*([A-Z]{2,3})\s+([0-9A-Z]+)", texto_notas):
        doc.poner("paciente_id", normalize.documento_id(m.group(1), m.group(2)), m.group(0))
    if m := re.search(r"CONSECUTIVO INTERNO:\s*(\d+)(?:-\d+)?", texto_notas):
        doc.poner("id_interno_paciente", m.group(1), m.group(0))
    if m := re.search(r"FECHA INGRESO:\s*(\d{4}-\d{2}-\d{2})", texto_notas):
        doc.poner("fecha_ingreso", m.group(1))
    if m := re.search(r"FECHA EGRESO:\s*(\d{4}-\d{2}-\d{2})", texto_notas):
        doc.poner("fecha_egreso", m.group(1))
    if m := re.search(r"AUTORIZACI[ÓO]N:\s*([^|]*)", texto_notas):
        valores = re.findall(r"[0-9A-Za-z\-]+", m.group(1))
        nota.poner("numero_autorizacion", ",".join(sorted({normalize.codigo(v) for v in valores})) or None, m.group(1))

    doc.extra["periodo_inicio"] = _uno(invoice, "/*/cac:InvoicePeriod/cbc:StartDate/text()")
    doc.extra["periodo_fin"] = _uno(invoice, "/*/cac:InvoicePeriod/cbc:EndDate/text()")

    # Extensión del sector salud (Name/Value).
    for info in invoice.xpath("//*[local-name()='AdditionalInformation']"):
        nombre = _uno(info, "*[local-name()='Name']/text()")
        valor = _uno(info, "*[local-name()='Value']/text()")
        if nombre:
            doc.extra[nombre.lower()] = valor

    for linea in invoice.xpath("//cac:InvoiceLine", namespaces=NS):
        buyers = _uno(linea, ".//cac:BuyersItemIdentification/cbc:ID/text()") or _uno(
            linea, ".//cac:StandardItemIdentification/cbc:ID/text()"
        )
        descripcion = _uno(linea, ".//cac:Item/cbc:Description/text()")
        codigo, origen = codigo_de_linea(buyers, descripcion)
        doc.lineas.append(
            LineaServicio(
                codigo=codigo,
                origen_codigo=origen,
                descripcion=limpiar_descripcion(descripcion),
                cantidad=normalize.dinero(_uno(linea, "cbc:InvoicedQuantity/text()")) or 1,
                valor_total=normalize.dinero(_uno(linea, "cbc:LineExtensionAmount/text()")) or 0,
                fuente="xml",
            )
        )

    respuesta = docs.get("ApplicationResponse")
    if respuesta is not None:
        doc.extra["dian_codigo"] = _uno(respuesta, "//cac:DocumentResponse/cac:Response/cbc:ResponseCode/text()")
        vistos: set[str] = set()
        for r in respuesta.xpath("//cac:LineResponse/cac:Response", namespaces=NS):
            cod = _uno(r, "cbc:ResponseCode/text()")
            # RUT01 viene repetido en la misma respuesta: se cuenta una vez por factura.
            if not cod or cod in CODIGOS_DIAN_SIN_NOVEDAD or cod in vistos:
                continue
            vistos.add(cod)
            doc.codigos.append(
                CodigoValidacion(origen="dian", codigo=cod, descripcion=_uno(r, "cbc:Description/text()"))
            )
    else:
        doc.errores.append("sin ApplicationResponse de la DIAN")
    return [doc, nota]
