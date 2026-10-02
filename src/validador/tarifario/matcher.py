"""Busca la tarifa de una línea facturada por CUPS + descripción.

El CUPS solo no alcanza: un mismo CUPS puede tener varias tarifas (p. ej. 998702
tiene 6 tipos de sedación). Además, cuando la factura usa una descripción genérica
("CONSULTA MEDICINA ESPECIALIZADA AMBULATORIA") el servicio se facturó con otra
tarifa que no está en esta base: en ese caso la tarifa queda "no determinable" en
lugar de marcar un falso error de valor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ..compare import similitud_tokens
from ..base.config import ReglasTarifa
from ..base.schemas import ResultadoTarifa
from .importer import ItemTarifa


def sin_categoria(descripcion: str) -> str:
    """'CONSULTA ... ENDOCRINOLOGIA-CONSULTA - INTERCON HSRT 100%' -> 'CONSULTA ... ENDOCRINOLOGIA'.

    El sufijo de categoría va pegado con un guion sin espacios; los guiones con
    espacios (' - ') son parte del nombre del servicio.
    """
    return re.split(r"(?<=\S)-(?=\S)", descripcion, maxsplit=1)[0].strip()


def puntaje(descripcion: str, item: ItemTarifa) -> float:
    return max(similitud_tokens(descripcion, item.descripcion), similitud_tokens(sin_categoria(descripcion), item.descripcion))


@dataclass
class Tarifario:
    items: list[ItemTarifa]
    version_id: int | None = None

    def __post_init__(self) -> None:
        self._por_cups: dict[str, list[ItemTarifa]] = {}
        for it in self.items:
            self._por_cups.setdefault(it.cups, []).append(it)

    def buscar(
        self, codigo: str | None, descripcion: str | None, valor_unitario: Decimal | None, reglas: ReglasTarifa
    ) -> ResultadoTarifa:
        base = {"tarifario_version_id": self.version_id, "valor_unitario_facturado": valor_unitario}
        candidatos = self._por_cups.get(codigo or "", [])
        if not candidatos:
            return ResultadoTarifa(estado="no_en_tarifario", **base)
        if not descripcion:
            return ResultadoTarifa(estado="no_determinable", candidatos=len(candidatos), **base)

        puntuados = sorted(((puntaje(descripcion, it), it) for it in candidatos), key=lambda x: x[0], reverse=True)
        mejor_score, mejor = puntuados[0]
        segundo = puntuados[1][0] if len(puntuados) > 1 else 0.0
        aceptado = mejor_score >= reglas.score_minimo and (len(puntuados) == 1 or mejor_score - segundo >= reglas.margen_minimo)
        if not aceptado:
            # Descripción ambigua: si el valor cobrado es exactamente la tarifa de un único
            # candidato con algo de parecido, se asume ese ítem (no se puede marcar error de valor).
            por_valor = [(s, it) for s, it in puntuados if s > 0 and it.valor == valor_unitario]
            if len(por_valor) == 1:
                s, it = por_valor[0]
                return ResultadoTarifa(
                    estado="coincide",
                    metodo="por_valor",
                    codigo_institucional=it.codigo_institucional,
                    descripcion_tarifa=it.descripcion,
                    valor_unitario_tarifa=it.valor,
                    score=round(s, 3),
                    candidatos=len(candidatos),
                    **base,
                )
            return ResultadoTarifa(
                estado="no_determinable", score=round(mejor_score, 3), candidatos=len(candidatos), **base
            )
        dentro = valor_unitario is not None and abs(valor_unitario - mejor.valor) <= reglas.tolerancia_pesos
        estado = "coincide" if dentro else "valor_difiere"
        return ResultadoTarifa(
            estado=estado,
            metodo="unico_por_cups" if len(candidatos) == 1 else "por_descripcion",
            codigo_institucional=mejor.codigo_institucional,
            descripcion_tarifa=mejor.descripcion,
            valor_unitario_tarifa=mejor.valor,
            score=round(mejor_score, 3),
            candidatos=len(candidatos),
            **base,
        )
