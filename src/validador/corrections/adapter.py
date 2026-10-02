"""Propuestas de corrección según el documento sospechoso.

Son propuestas, no acciones automáticas. Los textos deben confirmarse con
contabilidad y con el área jurídica antes de usarlos como mensajes definitivos.
``apply()`` está deshabilitado: solo se activará por sistema, con aprobación
humana explícita y registro de auditoría.
"""

from __future__ import annotations

from typing import Protocol

from ..base.schemas import PropuestaCorreccion, ResultadoFactura

ACCIONES = {
    "xml": "El XML no se edita (CUFE validado por la DIAN): evaluar nota crédito/débito o refacturación",
    "xml_nota": "El XML no se edita (CUFE validado por la DIAN): evaluar nota crédito/débito o refacturación",
    "pdf_factura": "Regenerar la representación gráfica desde el XML validado",
    "rips": "Corregir el RIPS en el sistema de origen y volver a validarlo ante el Ministerio (nuevo CUV)",
    "hc": "Corregir en el sistema clínico con nota aclaratoria (no sobrescribir)",
    "autorizacion_digital": "Re-escanear o re-subir, corregir metadatos en el gestor documental o solicitar nueva autorización a la EPS",
    "autorizacion_scan": "Re-escanear o re-subir, corregir metadatos en el gestor documental o solicitar nueva autorización a la EPS",
}


class CorrectionAdapter(Protocol):
    def propose(self, resultado: ResultadoFactura) -> list[PropuestaCorreccion]: ...

    def apply(self, propuesta: PropuestaCorreccion, aprobado_por: str) -> None: ...


def proponer(resultado: ResultadoFactura) -> list[PropuestaCorreccion]:
    propuestas: list[PropuestaCorreccion] = []
    for campo in resultado.campos:
        if campo.resultado != "grave" or not campo.documento_sospechoso:
            continue
        for doc in campo.documento_sospechoso.split(","):
            if doc not in ACCIONES:
                continue
            propuestas.append(
                PropuestaCorreccion(
                    campo=campo.campo,
                    documento=doc,
                    valor_actual=campo.valores_por_fuente.get(doc),
                    valor_propuesto=campo.valor_final,
                    accion=ACCIONES[doc],
                )
            )
    for linea in resultado.lineas:
        if linea.resultado != "grave" or not linea.documento_sospechoso:
            continue
        for doc in linea.documento_sospechoso.split(","):
            if doc in ACCIONES:
                propuestas.append(
                    PropuestaCorreccion(
                        campo=f"linea:{linea.codigo}",
                        documento=doc,
                        valor_actual=str(linea.valor_por_fuente.get(doc)) if doc in linea.valor_por_fuente else None,
                        valor_propuesto=None,
                        accion=ACCIONES[doc],
                    )
                )
    return propuestas


class ExportAdapter:
    """Primera implementación: solo genera propuestas para exportar a CSV/JSON."""

    def propose(self, resultado: ResultadoFactura) -> list[PropuestaCorreccion]:
        return proponer(resultado)

    def apply(self, propuesta: PropuestaCorreccion, aprobado_por: str) -> None:
        raise PermissionError("apply() deshabilitado: la corrección en sistemas de origen no está habilitada")
