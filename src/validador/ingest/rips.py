"""RIPS en JSON (Res. 948 de 2026, Documento Técnico 1; antes Res. 2275 de 2023): fuente principal.

Ojo: ``numDocumentoIdentificacion`` dentro de cada servicio es el documento del
profesional que atendió, no el del paciente. El del paciente está en ``usuarios[]``.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from ..base.schemas import DocumentoExtraido, LineaServicio
from ..data_preprocessing import normalize
from .util import sha256

# tipo de servicio -> (campo del código, campo de la fecha, campo de la cantidad)
SERVICIOS = {
    "consultas": ("codConsulta", "fechaInicioAtencion", None),
    "procedimientos": ("codProcedimiento", "fechaInicioAtencion", None),
    "medicamentos": ("codTecnologiaSalud", "fechaDispensAdmon", "cantidadMedicamento"),
    "otrosServicios": ("codTecnologiaSalud", "fechaSuministroTecnologia", "cantidadOS"),
}


def extraer(ruta: Path) -> DocumentoExtraido:
    doc = DocumentoExtraido(fuente="rips", archivo=ruta.name, sha256=sha256(ruta))
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        doc.errores.append(f"RIPS ilegible: {type(e).__name__}")
        return doc
    return desde_dict(datos, doc)


def desde_dict(datos: dict, doc: DocumentoExtraido | None = None) -> DocumentoExtraido:
    doc = doc or DocumentoExtraido(fuente="rips")
    doc.datos = datos  # completo, para el motor de reglas (no se serializa)
    doc.poner("numero_factura", normalize.codigo(datos.get("numFactura")))
    doc.poner("nit_emisor", normalize.nit(str(datos.get("numDocumentoIdObligado") or "")))
    doc.extra["tipo_nota"] = datos.get("tipoNota")
    doc.extra["num_nota"] = datos.get("numNota")

    usuarios = datos.get("usuarios") or []
    if len(usuarios) != 1:
        doc.errores.append(f"RIPS con {len(usuarios)} usuarios (se esperaba 1)")
    if not usuarios:
        return doc
    usuario = usuarios[0]
    doc.poner(
        "paciente_id",
        normalize.documento_id(usuario.get("tipoDocumentoIdentificacion"), usuario.get("numDocumentoIdentificacion")),
    )
    doc.extra["fecha_nacimiento"] = usuario.get("fechaNacimiento")
    doc.extra["sexo"] = usuario.get("codSexo")
    nacimiento = normalize.fecha(str(usuario.get("fechaNacimiento") or ""))
    doc.poner("fecha_nacimiento", nacimiento.isoformat() if nacimiento else None)
    doc.poner("sexo", normalize.sexo(usuario.get("codSexo")))

    total = Decimal(0)
    moderador = Decimal(0)
    autorizaciones: set[str] = set()
    for u in usuarios:
        for tipo, servicios in (u.get("servicios") or {}).items():
            if tipo not in SERVICIOS:
                doc.errores.append(f"tipo de servicio RIPS no soportado: {tipo}")
                continue
            campo_codigo, campo_fecha, campo_cantidad = SERVICIOS[tipo]
            for s in servicios:
                valor = normalize.dinero(s.get("vrServicio")) or Decimal(0)
                total += valor
                moderador += normalize.dinero(s.get("valorPagoModerador")) or Decimal(0)
                aut = normalize.codigo(s.get("numAutorizacion"))
                if aut:
                    # Algunos prestadores ponen varias autorizaciones en un servicio: '123,456'.
                    autorizaciones.update(a for a in re.split(r"[,;/\s]+", aut) if a)
                cantidad = normalize.dinero(s.get(campo_cantidad)) if campo_cantidad else None
                doc.lineas.append(
                    LineaServicio(
                        codigo=normalize.codigo(s.get(campo_codigo)),
                        origen_codigo=f"rips.{campo_codigo}",
                        tipo_servicio=tipo,
                        descripcion=s.get("nomTecnologiaSalud"),
                        cantidad=cantidad or Decimal(1),
                        valor_total=valor,
                        numero_autorizacion=aut,
                        fecha=normalize.fecha(s.get(campo_fecha)),
                        fuente="rips",
                    )
                )
    doc.poner("numero_autorizacion", ",".join(sorted(autorizaciones)) or None)
    # PayableAmount del XML = suma de servicios - pagos moderadores (verificado con los casos reales).
    doc.poner("valor_total", normalize.dinero_texto(total - moderador))
    return doc
