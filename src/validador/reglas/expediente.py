"""Expediente: la vista canónica de una factura sobre la que corren las reglas.

Las reglas nunca leen archivos: leen *rutas* del expediente (``servicio.codConsulta``,
``factura.periodo_inicio``, ``hc.texto``...). Los extractores (código) llenan el expediente;
las reglas (datos) lo consultan. Agregar un dato nuevo es código (un extractor y una entrada
en ``RUTAS``); usarlo en una regla es dato.

Los nombres de los campos RIPS se conservan tal como los define el Documento Técnico 1 de
la Res. 948 de 2026 (``codConsulta``, ``fechaInicioAtencion``...), para que una regla escrita
a partir de la norma use el mismo nombre que la norma.
"""

from __future__ import annotations

import fnmatch
from collections import ChainMap
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..base.schemas import DocumentoExtraido
from ..data_preprocessing import normalize

if TYPE_CHECKING:
    from .contratos import Contrato

# Rutas disponibles para las reglas. El agente normativo recibe esta lista: una regla que use
# una ruta que no está aquí queda como "requiere extractor" y no se puede activar.
RUTAS: dict[str, str] = {
    # Factura (XML / FEV, Res. 948/2026 DT2)
    "factura.numero": "Número de la factura (Invoice.ID)",
    "factura.fecha_emision": "Fecha de emisión (IssueDate)",
    "factura.periodo_inicio": "Inicio del periodo de facturación (InvoicePeriod/StartDate)",
    "factura.periodo_fin": "Fin del periodo de facturación (InvoicePeriod/EndDate)",
    "factura.nit_emisor": "NIT del prestador, sin dígito de verificación",
    "factura.nit_eps": "NIT del adquirente (pagador), sin dígito de verificación",
    "factura.valor_total": "PayableAmount",
    "factura.codigo_prestador": "Extensión sector salud CODIGO_PRESTADOR",
    "factura.modalidad_pago": "Extensión sector salud MODALIDAD_PAGO",
    "factura.cobertura_plan_beneficios": "Extensión sector salud COBERTURA_PLAN_BENEFICIOS",
    "factura.numero_contrato": "Extensión sector salud NUMERO_CONTRATO",
    "factura.numero_poliza": "Extensión sector salud NUMERO_POLIZA",
    "factura.tipos_servicio": "Tipos de servicio presentes en el RIPS (consultas, procedimientos...). Sin RIPS: sin valor",
    # RIPS: transacción y listas completas (DT1 §4.1)
    "rips.numDocumentoIdObligado": "T01 NIT del obligado (RIPS)",
    "rips.numFactura": "T02 número de factura (RIPS)",
    "rips.tipoNota": "T03 tipo de nota",
    "rips.numNota": "T04 número de nota",
    "rips.usuarios": "Lista de usuarios del RIPS (para reglas de consecutivo)",
    "rips.servicios.*": "Servicios de un tipo agrupados por usuario: lista de listas (rips.servicios.consultas...)",
    # RIPS: usuario (DT1 §4.2). En alcance 'servicio' es el usuario dueño del servicio.
    "usuario.*": "Campo del usuario tal como lo nombra el DT1 (codSexo, fechaNacimiento, tipoUsuario...)",
    # RIPS: servicio (DT1 §4.3). Solo en alcance 'servicio'.
    "servicio.*": "Campo del servicio tal como lo nombra el DT1 (codConsulta, codDiagnosticoPrincipal...)",
    "servicio.tipo": "Tipo de servicio RIPS: consultas, procedimientos, urgencias, hospitalizacion, recienNacidos, medicamentos, otrosServicios",
    "servicio.codigo": "Código del servicio (CUPS, CUM...) según el tipo",
    "servicio.fecha": "Fecha de atención del servicio (fechaInicioAtencion, fechaDispensAdmon...)",
    # Derivados (calculados en código)
    "derivado.edad_dias": "Edad del usuario en días a la fecha del servicio",
    "derivado.edad_anios": "Edad del usuario en años cumplidos a la fecha del servicio",
    "derivado.requiere_autorizacion": "Si el contrato exige autorización para este servicio (sin contrato: sin valor)",
    # Soportes
    "documentos.presentes": "Prefijos de los soportes de la carpeta (FEV, HEV, PDE, OPF, XML, RIPS, CUV...)",
    "hc.digital": "La historia clínica tiene texto digital (no escaneada)",
    "hc.texto": "Texto completo de la historia clínica digital",
    "cuv.codigos": "Códigos reportados por el Mecanismo Único de Validación (CUV)",
    # Contrato vigente con la EPS (si está cargado)
    "contrato.*": "Parámetros del contrato vigente (contrato.modalidad_pago, contrato.autorizacion.requiere, contrato.parametros.<x>...)",
}

# tipo de servicio RIPS -> (campo del código, campo de la fecha)
# Urgencias, hospitalización y recién nacidos no traen un código de tecnología propio.
SERVICIOS_RIPS: dict[str, tuple[str | None, str]] = {
    "consultas": ("codConsulta", "fechaInicioAtencion"),
    "procedimientos": ("codProcedimiento", "fechaInicioAtencion"),
    "urgencias": (None, "fechaInicioAtencion"),
    "hospitalizacion": (None, "fechaInicioAtencion"),
    "recienNacidos": (None, "fechaNacimiento"),
    "medicamentos": ("codTecnologiaSalud", "fechaDispensAdmon"),
    "otrosServicios": ("codTecnologiaSalud", "fechaSuministroTecnologia"),
}

# Campos que son fechas en el DT1: se convierten a date para comparar.
CAMPOS_FECHA = {"fechaNacimiento", "fechaInicioAtencion", "fechaEgreso", "fechaDispensAdmon", "fechaSuministroTecnologia"}


def ruta_conocida(ruta: str) -> bool:
    return ruta in RUTAS or any("*" in patron and fnmatch.fnmatchcase(ruta, patron) for patron in RUTAS)


def _vacio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


class Contexto:
    """Rutas → valores para una evaluación. Un valor vacío ('' o None) cuenta como ausente."""

    def __init__(self, *capas: dict[str, Any]):
        self._valores = ChainMap(*capas)

    def get(self, ruta: str) -> Any:
        valor = self._valores.get(ruta)
        return None if _vacio(valor) else valor

    def con(self, capa: dict[str, Any]) -> "Contexto":
        return Contexto(capa, *self._valores.maps)


def _plano(prefijo: str, datos: dict[str, Any]) -> dict[str, Any]:
    """{"a": {"b": 1}} -> {"prefijo.a.b": 1}. Las listas se dejan tal cual."""
    salida: dict[str, Any] = {}
    for clave, valor in datos.items():
        ruta = f"{prefijo}.{clave}"
        if isinstance(valor, dict):
            salida.update(_plano(ruta, valor))
        else:
            salida[ruta] = valor
    return salida


def _convertir(campo: str, valor: Any) -> Any:
    if campo in CAMPOS_FECHA and isinstance(valor, str):
        return normalize.fecha(valor) or valor
    return valor


def edad(nacimiento: date, fecha: date) -> tuple[int, int]:
    """(días, años cumplidos)."""
    anios = fecha.year - nacimiento.year - ((fecha.month, fecha.day) < (nacimiento.month, nacimiento.day))
    return (fecha - nacimiento).days, anios


@dataclass
class Expediente:
    factura: Contexto
    # Un contexto por servicio RIPS (incluye factura + su usuario + el servicio + derivados).
    servicios: list[Contexto] = field(default_factory=list)
    fecha_referencia: date | None = None


def construir(
    docs: dict[str, DocumentoExtraido],
    presentes: dict[str, list[str]],
    contrato: "Contrato | None" = None,
) -> Expediente:
    x = docs.get("xml")
    rips = docs.get("rips")
    hc = docs.get("hc")
    cuv = docs.get("cuv")
    datos_rips = (rips.datos if rips else None) or {}
    usuarios = datos_rips.get("usuarios") or []

    def campo_xml(nombre: str) -> str | None:
        return x.campos[nombre].valor if x and nombre in x.campos else None

    factura: dict[str, Any] = {
        "factura.numero": campo_xml("numero_factura"),
        "factura.fecha_emision": normalize.fecha(campo_xml("fecha_emision")),
        "factura.nit_emisor": campo_xml("nit_emisor"),
        "factura.nit_eps": campo_xml("nit_eps"),
        "factura.valor_total": normalize.dinero(campo_xml("valor_total")),
        "factura.tipos_servicio": sorted({t for u in usuarios for t in (u.get("servicios") or {})}) or None,
        "documentos.presentes": sorted(presentes),
        "hc.digital": bool(hc and hc.texto),
        "hc.texto": hc.texto if hc else None,
        "cuv.codigos": sorted({c.codigo for c in cuv.codigos}) if cuv else None,
    }
    if x:
        factura["factura.periodo_inicio"] = normalize.fecha(x.extra.get("periodo_inicio"))
        factura["factura.periodo_fin"] = normalize.fecha(x.extra.get("periodo_fin"))
        for nombre in ("codigo_prestador", "modalidad_pago", "cobertura_plan_beneficios", "numero_contrato", "numero_poliza"):
            valor = x.extra.get(nombre)
            factura[f"factura.{nombre}"] = None if valor in (None, "None") else valor
    for campo in ("numDocumentoIdObligado", "numFactura", "tipoNota", "numNota"):
        factura[f"rips.{campo}"] = datos_rips.get(campo)
    if usuarios:
        factura["rips.usuarios"] = usuarios
        for tipo in SERVICIOS_RIPS:
            por_usuario = [(u.get("servicios") or {}).get(tipo) or [] for u in usuarios]
            if any(por_usuario):
                factura[f"rips.servicios.{tipo}"] = [g for g in por_usuario if g]
    if contrato is not None:
        factura.update(_plano("contrato", contrato.model_dump(mode="python", exclude={"vigencia"})))

    def capa_usuario(u: dict) -> dict[str, Any]:
        return {f"usuario.{k}": _convertir(k, v) for k, v in u.items() if k != "servicios"}

    base = Contexto(capa_usuario(usuarios[0]) if usuarios else {}, factura)
    servicios: list[Contexto] = []
    fechas: list[date] = []
    for u in usuarios:
        cu = capa_usuario(u)
        for tipo, lista in (u.get("servicios") or {}).items():
            campo_codigo, campo_fecha = SERVICIOS_RIPS.get(tipo, (None, None))
            for indice, s in enumerate(lista, 1):
                capa = {f"servicio.{k}": _convertir(k, v) for k, v in s.items()}
                fecha = normalize.fecha(str(s.get(campo_fecha) or "")) if campo_fecha else None
                codigo = normalize.codigo(s.get(campo_codigo)) if campo_codigo else None
                capa.update({"servicio.tipo": tipo, "servicio.codigo": codigo, "servicio.fecha": fecha, "servicio.indice": indice})
                nacimiento = cu.get("usuario.fechaNacimiento")
                if isinstance(nacimiento, date) and fecha:
                    capa["derivado.edad_dias"], capa["derivado.edad_anios"] = edad(nacimiento, fecha)
                if contrato is not None and codigo:
                    capa["derivado.requiere_autorizacion"] = contrato.autorizacion.requiere_para(codigo, tipo)
                if fecha:
                    fechas.append(fecha)
                servicios.append(Contexto(capa, cu, factura))

    # Fecha que decide qué versión de cada regla, catálogo y contrato aplica: la primera
    # atención; sin RIPS, el inicio del periodo facturado; si no, la emisión.
    referencia = min(fechas) if fechas else factura.get("factura.periodo_inicio") or factura.get("factura.fecha_emision")
    return Expediente(factura=base, servicios=servicios, fecha_referencia=referencia)


def describir_valor(ruta: str, valor: Any) -> str:
    """Valor para mostrar en un hallazgo. Los datos del usuario (fecha de nacimiento, documento...)
    no se muestran: el hallazgo nombra el campo y el revisor lo mira en el RIPS."""
    if ruta.startswith("usuario.") or ruta in ("hc.texto",):
        return ruta
    if isinstance(valor, Decimal):
        return f"{valor:,.0f}".replace(",", ".")
    return str(valor)
