"""Modelo de una regla normativa y de un conjunto de reglas.

Una regla es un dato: una *primitiva* genérica (código, ver ``primitivas.py``) con sus
parámetros, una condición, una vigencia legal y la cita de la norma de la que sale. El
mismo modelo valida las reglas escritas a mano (YAML), las que vienen de la base de datos y
las que propone el agente normativo: una regla que no pasa esta validación no entra.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..base.config import Severidad

TipoResultado = Literal["rechazo_muv", "notificacion", "validacion", "glosa_potencial"]
EstadoConjunto = Literal["borrador", "en_prueba", "aprobada", "vigente", "retirada"]


class Cita(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documento: str  # "Res. 948/2026 DT1"
    numeral: str | None = None
    pagina: str | None = None
    # Texto literal de la norma. Obligatorio en las reglas que propone el agente: se verifica
    # que aparezca en el documento fuente para descartar citas inventadas.
    texto_literal: str | None = None
    url: str | None = None

    def __str__(self) -> str:
        partes = [self.documento, self.numeral, f"p. {self.pagina}" if self.pagina else None]
        return " ".join(p for p in partes if p)


class Vigencia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desde: date
    hasta: date | None = None

    @model_validator(mode="after")
    def _orden(self) -> "Vigencia":
        if self.hasta and self.hasta < self.desde:
            raise ValueError("vigencia.hasta es anterior a vigencia.desde")
        return self

    def contiene(self, fecha: date) -> bool:
        return self.desde <= fecha and (self.hasta is None or fecha <= self.hasta)

    def se_cruza(self, otra: "Vigencia") -> bool:
        return self.desde <= (otra.hasta or date.max) and otra.desde <= (self.hasta or date.max)


class Consecuencia(BaseModel):
    """Qué produce la regla cuando falla."""

    model_config = ConfigDict(extra="forbid")

    tipo: TipoResultado
    severidad: Severidad
    codigo_glosa: str | None = None
    # Códigos del Mecanismo Único de Validación que reportan lo mismo: si el CUV ya los trae,
    # la regla no se repite como hallazgo (el Ministerio ya lo validó).
    cuv_equivalente: list[str] = Field(default_factory=list)
    documento: str | None = None  # documento sospechoso: rips, xml, hc, autorizacion...
    mensaje: str  # qué pasa, en lenguaje claro; el detalle técnico se agrega al final
    verificar: str | None = None
    accion: str | None = None


class Regla(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{2,60}$")
    version: int = Field(default=1, ge=1)
    nombre: str
    # Ids de la matriz SEMCOL (hoja 06) que esta regla cubre, para el reporte de cobertura.
    matriz: list[str] = Field(default_factory=list)
    norma: Cita
    vigencia: Vigencia
    alcance: Literal["factura", "servicio"] = "servicio"
    tipos_servicio: list[str] = Field(default_factory=list)  # vacío: todos (alcance servicio)
    cuando: Any = None
    primitiva: str
    params: dict[str, Any] = Field(default_factory=dict)
    resultado: Consecuencia
    activa: bool = True
    notas: str | None = None

    @model_validator(mode="after")
    def _validar(self) -> "Regla":
        from . import condicion, primitivas  # import diferido: primitivas usa este módulo

        if self.cuando is not None:
            condicion.validar(self.cuando)
        primitiva = primitivas.REGISTRO.get(self.primitiva)
        if primitiva is None:
            raise ValueError(f"primitiva desconocida '{self.primitiva}' (disponibles: {', '.join(sorted(primitivas.REGISTRO))})")
        primitiva.Params.model_validate(self.params)
        if self.alcance == "factura" and self.tipos_servicio:
            raise ValueError("tipos_servicio solo aplica con alcance 'servicio'")
        return self

    def rutas(self) -> set[str]:
        """Rutas del expediente que usa la regla (condición + parámetros)."""
        from . import condicion, primitivas

        p = primitivas.REGISTRO[self.primitiva]
        return condicion.rutas(self.cuando) | p.rutas(p.Params.model_validate(self.params))

    def aplica_en(self, fecha: date | None) -> bool:
        return self.activa and (fecha is None or self.vigencia.contiene(fecha))


class ConjuntoReglas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(pattern=r"^[a-z0-9_]{3,60}$")
    descripcion: str = ""
    reglas: list[Regla]

    @model_validator(mode="after")
    def _unicas(self) -> "ConjuntoReglas":
        vistos: dict[str, list[Regla]] = {}
        for r in self.reglas:
            for otra in vistos.get(r.id, []):
                if otra.version == r.version:
                    raise ValueError(f"regla {r.id} v{r.version} repetida")
                # Varias versiones de una regla conviven solo si sus vigencias no se cruzan
                # (p. ej. la versión de la norma derogada y la de la norma nueva).
                if otra.vigencia.se_cruza(r.vigencia) and otra.activa and r.activa:
                    raise ValueError(f"regla {r.id}: las versiones {otra.version} y {r.version} tienen vigencias que se cruzan")
            vistos.setdefault(r.id, []).append(r)
        return self
