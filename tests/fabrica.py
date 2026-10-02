"""Documentos sintéticos para pruebas. Todos los datos son inventados."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pymupdf

CUFE = "a" * 90 + "b1c2d3"
NIT_IPS = "800000001"
NIT_EPS = "900000002"


@dataclass
class Caso:
    factura: str = "FE0000001"
    tipo_id: str = "CC"
    numero_id: str = "1000001"
    nombre: str = "PEREZ GOMEZ JUAN CARLOS"
    autorizacion: str = "17458248"
    id_interno: str = "555001"
    fecha: str = "2026-07-31"
    # (codigo_buyers, descripcion, cantidad, valor_total, tipo_rips, cups_rips)
    lineas: list[tuple] = field(
        default_factory=lambda: [
            ("39143", "890244 CONSULTA DE PRIMERA VEZ POR ESPECIALISTA EN ENDOCRINOLOGIA-CONSULTA - INTERCON", 1, 214000, "consultas", "890244")
        ]
    )
    dian: list[str] = field(default_factory=lambda: ["0000", "FAJ43b", "RUT01", "RUT01"])


def rips_dict(c: Caso, **cambios) -> dict:
    servicios: dict[str, list] = {}
    for _, _, cantidad, valor, tipo, cups in c.lineas:
        for _ in range(int(cantidad)):
            campo = {"consultas": "codConsulta", "procedimientos": "codProcedimiento"}[tipo]
            servicios.setdefault(tipo, []).append(
                {
                    campo: cups,
                    "fechaInicioAtencion": f"{c.fecha} 10:00",
                    "numAutorizacion": cambios.get("autorizacion", c.autorizacion),
                    "tipoDocumentoIdentificacion": "CC",
                    "numDocumentoIdentificacion": "9999999",  # profesional, no paciente
                    "vrServicio": valor / cantidad,
                    "valorPagoModerador": 0,
                }
            )
    return {
        "numDocumentoIdObligado": NIT_IPS,
        "numFactura": c.factura,
        "tipoNota": None,
        "numNota": None,
        "usuarios": [
            {
                "tipoDocumentoIdentificacion": c.tipo_id,
                "numDocumentoIdentificacion": cambios.get("numero_id", c.numero_id),
                "servicios": servicios,
            }
        ],
    }


def xml_bytes(c: Caso, **cambios) -> bytes:
    numero_id = cambios.get("numero_id", c.numero_id)
    total = sum(l[3] for l in c.lineas)
    lineas = "".join(
        f"""<cac:InvoiceLine><cbc:ID>{i}</cbc:ID><cbc:InvoicedQuantity>{q:.2f}</cbc:InvoicedQuantity>
        <cbc:LineExtensionAmount currencyID="COP">{v:.2f}</cbc:LineExtensionAmount>
        <cac:Item><cbc:Description>{d}</cbc:Description>
        <cac:BuyersItemIdentification><cbc:ID>{b}</cbc:ID></cac:BuyersItemIdentification></cac:Item></cac:InvoiceLine>"""
        for i, (b, d, q, v, _, _) in enumerate(c.lineas, 1)
    )
    invoice = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
<cbc:ID>{c.factura}</cbc:ID><cbc:UUID>{CUFE}</cbc:UUID><cbc:IssueDate>{c.fecha}</cbc:IssueDate>
<cbc:Note>3_SUBS S-TOTAL| | CONSECUTIVO INTERNO: {c.id_interno}-1|| AUTORIZACIÓN:  {cambios.get("autorizacion_nota", c.autorizacion)}| FECHA INGRESO: {c.fecha}| FECHA EGRESO: {c.fecha}| PACIENTE: {c.nombre} | ID: {c.tipo_id}  {numero_id}| TELÉFONO: 3000000000</cbc:Note>
<cac:AdditionalDocumentReference><cbc:ID>{cambios.get("autorizacion", c.autorizacion)}</cbc:ID><cbc:DocumentTypeCode>AAJ</cbc:DocumentTypeCode></cac:AdditionalDocumentReference>
<cac:AccountingSupplierParty><cac:Party><cac:PartyTaxScheme><cbc:CompanyID schemeID="0">{NIT_IPS}</cbc:CompanyID></cac:PartyTaxScheme></cac:Party></cac:AccountingSupplierParty>
<cac:AccountingCustomerParty><cac:Party><cac:PartyTaxScheme><cbc:CompanyID schemeID="2">{NIT_EPS}</cbc:CompanyID></cac:PartyTaxScheme></cac:Party></cac:AccountingCustomerParty>
<cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="COP">{total:.2f}</cbc:PayableAmount></cac:LegalMonetaryTotal>
{lineas}
</Invoice>"""
    respuestas = "".join(
        f"<cac:LineResponse><cac:Response><cbc:ResponseCode>{cod}</cbc:ResponseCode><cbc:Description>d</cbc:Description></cac:Response></cac:LineResponse>"
        for cod in c.dian
    )
    app = f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationResponse xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
<cac:DocumentResponse><cac:Response><cbc:ResponseCode>02</cbc:ResponseCode><cbc:Description>Documento validado por la DIAN</cbc:Description></cac:Response>
{respuestas}</cac:DocumentResponse></ApplicationResponse>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<AttachedDocument xmlns="urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
<cbc:ID>{c.factura}</cbc:ID>
<cac:Attachment><cac:ExternalReference><cbc:Description><![CDATA[{invoice}]]></cbc:Description></cac:ExternalReference></cac:Attachment>
<cac:ParentDocumentLineReference><cac:DocumentReference><cac:Attachment><cac:ExternalReference>
<cbc:Description><![CDATA[{app}]]></cbc:Description></cac:ExternalReference></cac:Attachment></cac:DocumentReference></cac:ParentDocumentLineReference>
</AttachedDocument>""".encode("utf-8")


def cuv_dict(c: Caso, resultados: bool = True) -> dict:
    d = {
        "ResultState": True,
        "ProcesoId": 1,
        "NumFactura": c.factura,
        "CodigoUnicoValidacion": "f" * 96,
        "FechaRadicacion": "2026-08-01T10:00:00-05:00",
        "RutaArchivos": None,
    }
    if resultados:
        d["ResultadosValidacion"] = [
            {"Clase": "NOTIFICACION", "Codigo": "FED137", "Descripcion": "contrato", "Observaciones": "", "PathFuente": "", "Fuente": "FacturaElectronica"}
        ]
    return d


def hc_pdf(ruta: Path, c: Caso, numero_id: str | None = None, tipo_consulta: str = "Primera vez", fecha: str = "31/07/2026") -> None:
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((243, 52), "Tipo y número de identificación:", fontsize=8)
    p.insert_text((374, 52), f"{c.tipo_id} {numero_id or c.numero_id}", fontsize=8)
    # En la HC el nombre viene en otro orden y con "(a)".
    partes = c.nombre.split()
    p.insert_text((243, 64), "Paciente:", fontsize=8)
    p.insert_text((288, 64), " ".join(partes[2:] + partes[:2]) + " (a)", fontsize=8)
    p.insert_text((243, 112), "Identificador único:", fontsize=8)
    p.insert_text((321, 112), c.id_interno, fontsize=8)
    p.insert_text((40, 200), f"Fecha: {fecha} 09:25 - Ambulatoria - Servicio: CONSULTA EXTERNA", fontsize=8)
    p.insert_text((40, 212), "Consulta médica - ENDOCRINOLOGIA", fontsize=8)
    p.insert_text((40, 224), f"Tipo de valoración: Tipo de consulta: {tipo_consulta}", fontsize=8)
    p.insert_text((40, 236), "Texto de la nota médica con suficiente contenido para no parecer escaneo.", fontsize=8)
    doc.save(ruta)


def carpeta(
    tmp: Path, c: Caso, rips: bool = True, xml: bool = True, cuv: bool = True, hc: dict | None = None,
    cambios_rips: dict | None = None, cambios_xml: dict | None = None,
) -> Path:
    d = tmp / c.factura
    d.mkdir(parents=True)
    if rips:
        (d / f"{c.factura}.json").write_text(json.dumps(rips_dict(c, **(cambios_rips or {}))), encoding="utf-8")
    if xml:
        (d / f"{c.factura}.xml").write_bytes(xml_bytes(c, **(cambios_xml or {})))
    if cuv:
        (d / f"{c.factura}_CUV.json").write_text(json.dumps(cuv_dict(c)), encoding="utf-8")
    if hc is not None:
        hc_pdf(d / f"HEV_{NIT_IPS}_{c.factura}.pdf", c, **hc)
    return d


def tarifario_xlsx(ruta: Path, filas: list[tuple], uvb: int = 12110) -> None:
    """Imita el Excel real: título, fila vacía, encabezado con el valor de la UVB como nombre de columna."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Anexo - Consultas"
    ws.append([None])
    ws.append(["ANEXO TECNICO No 1. TARIFARIO INSTITUCIONAL"])
    ws.append([None])
    ws.append(["CÓDIGO INSTITUCIONAL", "CUPS", "DESCRIPCION", "\nTARIFA EN UVB", uvb, "REDONDEADO"])
    for f in filas:
        ws.append(list(f))
    wb.save(ruta)
