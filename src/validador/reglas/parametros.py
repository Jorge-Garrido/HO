"""Parámetros de lectura de documentos, versionados en la base y con alcance.

Lo que cambia entre EPS, IPS y formatos (alias de soportes, etiquetas que busca el localizador,
contextos que invalidan una etiqueta, vocabulario) es dato, no código. Cada parámetro tiene un
alcance y el más específico gana::

    eps:<nit>  >  prestador:<nit>  >  global

Dónde se aplica cada tipo:

- ``alias_soporte``, ``patron_factura``, ``ignorar_archivo``: en el descubrimiento de facturas,
  que ocurre antes de leer el XML y por eso solo usa el alcance global.
- ``etiqueta_campo``, ``etiqueta_negativa``, ``etiqueta_generica``, ``vocabulario``: en la lectura
  de los PDF (localizador), que ocurre cuando ya se conocen la EPS y el prestador.

Ver documentos/PLAN_PARAMETROS_EN_BD.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TipoParametro = Literal[
    "alias_soporte", "patron_factura", "ignorar_archivo",
    "etiqueta_campo", "etiqueta_negativa", "etiqueta_generica", "vocabulario",
]
VOCABULARIOS = {"palabras_etiqueta", "tipos_documento", "alias_tipo_documento"}


class Parametro(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: TipoParametro
    clave: str
    valor: Any
    alcance: str = Field(default="global", pattern=r"^(global|prestador:\d+|eps:\d+)$")
    notas: str | None = None

    @model_validator(mode="after")
    def _valor(self) -> "Parametro":
        v = self.valor
        if self.tipo == "alias_soporte" and not (isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v)):
            raise ValueError(f"alias_soporte {self.clave}: el valor debe ser un prefijo de 3 letras (HEV, PDE…)")
        if self.tipo == "patron_factura":
            re.compile(str(v))
        if self.tipo in ("ignorar_archivo", "etiqueta_campo", "etiqueta_negativa", "etiqueta_generica"):
            if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                raise ValueError(f"{self.tipo} {self.clave}: el valor debe ser una lista de textos")
        if self.tipo == "vocabulario" and self.clave not in VOCABULARIOS:
            raise ValueError(f"vocabulario desconocido '{self.clave}' (válidos: {', '.join(sorted(VOCABULARIOS))})")
        return self


class ConjuntoParametros(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = "parametros"
    descripcion: str = ""
    parametros: list[Parametro]

    @model_validator(mode="after")
    def _unicos(self) -> "ConjuntoParametros":
        vistos: set[tuple[str, str, str]] = set()
        for p in self.parametros:
            k = (p.tipo, p.clave, p.alcance)
            if k in vistos:
                raise ValueError(f"parámetro repetido: {p.tipo} {p.clave} ({p.alcance})")
            vistos.add(k)
        return self


def leer(ruta: Path) -> tuple[ConjuntoParametros, str]:
    contenido = ruta.read_bytes()
    datos = yaml.safe_load(contenido) if ruta.suffix.lower() in (".yaml", ".yml") else json.loads(contenido)
    return ConjuntoParametros.model_validate(datos), hashlib.sha256(contenido).hexdigest()


def huella(conjunto: ConjuntoParametros) -> str:
    canonico = json.dumps(conjunto.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# --- vista resuelta ---------------------------------------------------------------------------


@dataclass
class Vocabulario:
    """Lo que usa el localizador para una factura (ya resuelto por alcance)."""

    etiquetas: dict[str, list[str]]
    negativas: dict[str, set[str]]
    genericas: dict[str, set[str]]
    palabras_etiqueta: set[str]
    tipos_documento: set[str]


@dataclass
class Parametros:
    conjunto: ConjuntoParametros
    version_id: int | None = None
    _indice: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        for p in self.conjunto.parametros:
            self._indice.setdefault((p.tipo, p.clave), {})[p.alcance] = p.valor

    def _resolver(self, tipo: str, eps: str | None, prestador: str | None) -> dict[str, Any]:
        orden = [f"eps:{eps}" if eps else None, f"prestador:{prestador}" if prestador else None, "global"]
        salida: dict[str, Any] = {}
        for (t, clave), por_alcance in self._indice.items():
            if t != tipo:
                continue
            for alcance in orden:
                if alcance and alcance in por_alcance:
                    salida[clave] = por_alcance[alcance]
                    break
        return salida

    def descubrimiento(self) -> dict[str, Any]:
        """Campos de Config que reemplaza (alcance global)."""
        cambios: dict[str, Any] = {}
        if alias := self._resolver("alias_soporte", None, None):
            cambios["alias_soportes"] = alias
        if patron := self._resolver("patron_factura", None, None):
            cambios["patron_factura"] = next(iter(patron.values()))
        if ignorar := self._resolver("ignorar_archivo", None, None):
            cambios["ignorar_archivos"] = [x for lista in ignorar.values() for x in lista]
        return cambios

    def vocabulario(self, eps: str | None = None, prestador: str | None = None) -> Vocabulario:
        voc = self._resolver("vocabulario", eps, prestador)
        return Vocabulario(
            etiquetas={k: list(v) for k, v in self._resolver("etiqueta_campo", eps, prestador).items()},
            negativas={k: set(v) for k, v in self._resolver("etiqueta_negativa", eps, prestador).items()},
            genericas={k: set(v) for k, v in self._resolver("etiqueta_generica", eps, prestador).items()},
            palabras_etiqueta=set(voc.get("palabras_etiqueta", [])),
            tipos_documento=set(voc.get("tipos_documento", [])),
        )

    def alias_tipo_documento(self) -> dict[str, str]:
        return dict(self._resolver("vocabulario", None, None).get("alias_tipo_documento", {}))


# Vocabulario activo para la factura que se está leyendo (None = valores por defecto del código).
VOCABULARIO_ACTIVO: ContextVar[Vocabulario | None] = ContextVar("vocabulario_activo", default=None)
