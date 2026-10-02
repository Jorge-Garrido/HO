"""Campos de un PDF digital con el localizador genérico (sin reglas de plantilla).

Principio: los valores ya vienen de las fuentes estructuradas (RIPS/XML). En cada
PDF se **verifica** si esos valores están junto a la etiqueta del campo; si no están,
se **extrae** lo que haya junto a la etiqueta para poder mostrar la diferencia.
"""

from __future__ import annotations

from pathlib import Path

from .. import localizar
from ..base.schemas import CampoExtraido, DocumentoExtraido
from ..data_preprocessing import layout, normalize

CAMPOS_POR_DOCUMENTO = {
    "pdf_factura": [
        "numero_factura", "cufe", "nit_emisor", "nit_eps", "paciente_id", "paciente_nombre",
        "numero_autorizacion", "id_interno_paciente", "valor_total", "fecha_ingreso", "fecha_egreso",
    ],
    "hc": ["paciente_id", "paciente_nombre", "id_interno_paciente"],
    "autorizacion_digital": ["paciente_id", "paciente_nombre", "id_interno_paciente"],
}
# El encabezado de la HC y de la autorización se repite en cada página: basta la primera.
MAX_PAGINAS = {"pdf_factura": None, "hc": 1, "autorizacion_digital": 1}


def normalizar(campo: str, texto: str | None) -> str | None:
    if not texto:
        return None
    tipo = localizar.TIPOS[campo]
    if campo.startswith("nit"):
        return normalize.nit(texto)
    if campo == "cufe":
        return localizar._compacto(texto).lower()
    if tipo == "codigo":
        return localizar._compacto(texto)
    if tipo == "documento":
        return normalize.documento_id_texto(texto)
    if tipo == "nombre":
        return normalize.nombre(texto)
    if tipo == "dinero":
        return normalize.dinero_texto(normalize.dinero(texto))
    if tipo == "fecha":
        f = normalize.fecha(texto.replace("/", "-")) if texto[:4].isdigit() else normalize.fecha(texto)
        return f.isoformat() if f else None
    return texto


def campo(doc: layout.DocLayout, nombre: str, fuente: str, esperados: set[str]) -> CampoExtraido | None:
    base = {"campo": nombre, "fuente": fuente}
    diferencias: list[localizar.Verificacion] = []
    sin_contexto: list[tuple[str, localizar.Verificacion]] = []
    for esperado in sorted(esperados):
        v = localizar.verificar(doc, nombre, esperado)
        if v.estado == "confirmado":
            return CampoExtraido(valor=esperado, valor_crudo=v.valor_encontrado, pagina=v.pagina, bbox=list(v.bbox), **base)
        if v.estado == "diferencia":
            diferencias.append(v)
        elif v.estado == "confirmado_sin_contexto":
            sin_contexto.append((esperado, v))
    if diferencias:
        v = min(diferencias, key=lambda d: d.distancia or 0)
        return CampoExtraido(
            valor=normalizar(nombre, v.valor_encontrado), valor_crudo=v.valor_encontrado,
            pagina=v.pagina, bbox=list(v.bbox) if v.bbox else None, confianza=0.9, **base,
        )
    extraido = localizar.extraer(doc, nombre)
    if extraido:
        return CampoExtraido(valor=normalizar(nombre, extraido), valor_crudo=extraido, confianza=0.8, **base)
    if sin_contexto:
        # El valor está en el documento pero lejos de cualquier etiqueta del campo.
        esperado, v = sin_contexto[0]
        return CampoExtraido(valor=esperado, valor_crudo=v.valor_encontrado, pagina=v.pagina, confianza=0.5, **base)
    return None


def completar(doc_extraido: DocumentoExtraido, ruta: Path, esperados: dict[str, set[str]]) -> DocumentoExtraido:
    """Reemplaza los campos del documento por los del localizador genérico."""
    fuente = doc_extraido.fuente
    if doc_extraido.requiere_ocr and not doc_extraido.campos and fuente != "autorizacion_digital":
        return doc_extraido  # PDF escaneado: pendiente de OCR
    lay = layout.desde_pdf(ruta, MAX_PAGINAS.get(fuente))
    campos = {}
    for nombre in CAMPOS_POR_DOCUMENTO.get(fuente, []):
        if c := campo(lay, nombre, fuente, esperados.get(nombre, set())):
            campos[nombre] = c
    doc_extraido.campos = campos
    return doc_extraido
