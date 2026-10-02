"""Contrato (acuerdo de voluntades) con una EPS, como parámetros y no como código.

Las reglas son las mismas para todas las EPS; lo que cambia entre contratos son estos
parámetros. Una regla los lee por ruta (``contrato.autorizacion.vigencia_dias``) o por un
derivado (``derivado.requiere_autorizacion``). Para algo que de verdad solo tenga una EPS, el
contrato puede activar un plugin por nombre (``plugins``), sin tocar el motor.

Un contrato se carga desde JSON o YAML y se guarda versionado e inmutable. Se elige por NIT
de la EPS y vigencia; si la factura trae NUMERO_CONTRATO y hay un contrato con ese número,
ese tiene prioridad.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .modelos import Vigencia


class PoliticaAutorizacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # todos: todo servicio requiere autorización salvo las excepciones; ninguno: ninguno la
    # requiere salvo cups_requieren; lista: solo cups_requieren y tipos_servicio_requieren.
    requiere: Literal["todos", "ninguno", "lista"] = "todos"
    cups_requieren: list[str] = Field(default_factory=list)
    cups_excentos: list[str] = Field(default_factory=list)
    tipos_servicio_requieren: list[str] = Field(default_factory=list)
    tipos_servicio_excentos: list[str] = Field(default_factory=list)
    vigencia_dias: int | None = None

    def requiere_para(self, cups: str, tipo_servicio: str | None) -> bool:
        if cups in self.cups_excentos or tipo_servicio in self.tipos_servicio_excentos:
            return False
        if cups in self.cups_requieren or tipo_servicio in self.tipos_servicio_requieren:
            return True
        return self.requiere == "todos"


class PoliticaOrdenMedica(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Res. 2284/2023: sin autorización, la orden o prescripción (OPF) soporta la factura.
    requerida_sin_autorizacion: bool = True
    vigencia_dias: int | None = None


class PoliticaTarifa(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Cada método es un plugin de cálculo. Hoy solo existe el tarifario institucional (UVB).
    metodo: Literal["tarifario_institucional", "soat", "iss2001", "propio"] = "tarifario_institucional"
    tarifario_version_id: int | None = None
    ajuste_pct: Decimal = Decimal(0)


class Contrato(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eps_nit: str
    eps_nombre: str
    numero_contrato: str | None = None
    vigencia: Vigencia
    modalidad_pago: str | None = None
    autorizacion: PoliticaAutorizacion = PoliticaAutorizacion()
    orden_medica: PoliticaOrdenMedica = PoliticaOrdenMedica()
    tarifa: PoliticaTarifa = PoliticaTarifa()
    # Soportes exigidos por tipo de servicio (prefijos de la Res. 2284: HEV, PDE, OPF, EPI...).
    soportes_requeridos: dict[str, list[str]] = Field(default_factory=dict)
    # Parámetros libres para reglas: contrato.parametros.<nombre>.
    parametros: dict[str, Any] = Field(default_factory=dict)
    plugins: list[str] = Field(default_factory=list)
    notas: str | None = None

    @field_validator("eps_nit")
    @classmethod
    def _nit(cls, v: str) -> str:
        v = v.replace(".", "").split("-")[0].strip()
        if not v.isdigit():
            raise ValueError("eps_nit debe ser numérico (sin dígito de verificación)")
        return v


def leer(ruta: Path) -> tuple[Contrato, str]:
    """Lee y valida un contrato desde JSON o YAML. Devuelve el contrato y el sha256 del archivo."""
    contenido = ruta.read_bytes()
    datos = yaml.safe_load(contenido) if ruta.suffix.lower() in (".yaml", ".yml") else json.loads(contenido)
    return Contrato.model_validate(datos), hashlib.sha256(contenido).hexdigest()


class Contratos:
    def __init__(self, contratos: list[tuple[Contrato, int | None]] | None = None):
        self._lista = contratos or []

    def __len__(self) -> int:
        return len(self._lista)

    def buscar(self, nit_eps: str | None, numero: str | None, fecha: date | None) -> tuple[Contrato, int | None] | None:
        candidatos = [
            (c, i) for c, i in self._lista
            if nit_eps and c.eps_nit == nit_eps and (fecha is None or c.vigencia.contiene(fecha))
        ]
        if numero:
            exacto = [(c, i) for c, i in candidatos if c.numero_contrato == numero]
            if exacto:
                return exacto[0]
        genericos = [(c, i) for c, i in candidatos if c.numero_contrato is None]
        return (genericos or candidatos or [None])[0]
