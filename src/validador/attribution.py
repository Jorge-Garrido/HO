"""Votación por mayoría para decidir qué documento es el sospechoso."""

from __future__ import annotations

from dataclasses import dataclass

from .compare import iguales


@dataclass
class Voto:
    valor_mayoria: str | None
    sospechosos: list[str]
    empate: bool


def votar(valores: dict[str, str], comparador: str) -> Voto:
    """Agrupa las fuentes por valor equivalente.

    Hay mayoría si un grupo es estrictamente el más grande y tiene al menos 2
    fuentes; las fuentes fuera de ese grupo son las sospechosas. Si no (por
    ejemplo dos fuentes con valores distintos), es empate y no se atribuye.
    """
    grupos: list[tuple[str, list[str]]] = []
    for fuente, valor in valores.items():
        for representante, fuentes in grupos:
            if iguales(comparador, representante, valor):
                fuentes.append(fuente)
                break
        else:
            grupos.append((valor, [fuente]))
    if len(grupos) <= 1:
        return Voto(grupos[0][0] if grupos else None, [], False)
    grupos.sort(key=lambda g: len(g[1]), reverse=True)
    mayor, segundo = len(grupos[0][1]), len(grupos[1][1])
    if mayor >= 2 and mayor > segundo:
        sospechosos = [f for _, fuentes in grupos[1:] for f in fuentes]
        return Voto(grupos[0][0], sospechosos, False)
    return Voto(None, [], True)
