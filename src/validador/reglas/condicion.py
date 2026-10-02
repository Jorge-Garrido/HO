"""Condiciones de las reglas (campo ``cuando``): un JSONLogic reducido, sin ``eval``.

Una condición es un literal o un diccionario con un solo operador::

    {"var": "servicio.valorPagoModerador"}                 valor de una ruta del expediente
    {">": [{"var": "servicio.valorPagoModerador"}, 0]}     comparación (==, !=, <, <=, >, >=)
    {"in": [{"var": "servicio.tipo"}, ["consultas"]]}      pertenencia a una lista
    {"and": [...]}, {"or": [...]}, {"!": cond}             lógica
    {"presente": "contrato.autorizacion.requiere"}         la ruta tiene valor
    {"ausente": "servicio.numAutorizacion"}                la ruta no tiene valor

Lógica de tres valores: si una ruta no tiene valor (fuera de ``presente``/``ausente``) la
condición es *indeterminada* y la regla queda "no evaluable", nunca "falla".
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .expediente import Contexto

OPERADORES = {"var", "==", "!=", "<", "<=", ">", ">=", "in", "and", "or", "!", "presente", "ausente"}


class Indeterminado(Exception):
    """La condición depende de un dato que no está en el expediente."""

    def __init__(self, ruta: str):
        super().__init__(ruta)
        self.ruta = ruta


def rutas(cond: Any) -> set[str]:
    """Rutas del expediente que usa una condición (para validar la regla al importarla)."""
    if isinstance(cond, dict):
        (op, arg), = cond.items()
        if op in ("var", "presente", "ausente"):
            return {arg}
        return set().union(*(rutas(a) for a in (arg if isinstance(arg, list) else [arg])))
    if isinstance(cond, list):
        return set().union(*(rutas(a) for a in cond)) if cond else set()
    return set()


def validar(cond: Any) -> None:
    """Lanza ValueError si la condición no está bien formada."""
    if isinstance(cond, dict):
        if len(cond) != 1:
            raise ValueError(f"cada nodo de la condición debe tener un solo operador: {cond}")
        (op, arg), = cond.items()
        if op not in OPERADORES:
            raise ValueError(f"operador desconocido '{op}' (permitidos: {', '.join(sorted(OPERADORES))})")
        if op in ("var", "presente", "ausente"):
            if not isinstance(arg, str):
                raise ValueError(f"'{op}' recibe una ruta de texto")
            return
        if op == "!":
            validar(arg)
            return
        if not isinstance(arg, list):
            raise ValueError(f"'{op}' recibe una lista de argumentos")
        if op in ("==", "!=", "<", "<=", ">", ">=", "in") and len(arg) != 2:
            raise ValueError(f"'{op}' recibe exactamente dos argumentos")
        for a in arg:
            validar(a)
    elif isinstance(cond, list):
        for a in cond:
            validar(a)


def _comparable(a: Any, b: Any) -> tuple[Any, Any]:
    """Iguala tipos: números como Decimal, fechas como date, el resto como texto."""
    if isinstance(a, datetime):
        a = a.date()
    if isinstance(b, datetime):
        b = b.date()
    if isinstance(a, date) or isinstance(b, date):
        return a, b
    try:
        if isinstance(a, (int, float, Decimal)) or isinstance(b, (int, float, Decimal)):
            return Decimal(str(a)), Decimal(str(b))
    except InvalidOperation:
        pass
    return str(a), str(b)


_COMPARACIONES: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def evaluar(cond: Any, ctx: Contexto) -> Any:
    """Evalúa la condición. Lanza Indeterminado si falta un dato necesario."""
    if not isinstance(cond, dict):
        if isinstance(cond, list):
            return [evaluar(c, ctx) for c in cond]
        return cond
    (op, arg), = cond.items()
    if op == "var":
        valor = ctx.get(arg)
        if valor is None:
            raise Indeterminado(arg)
        return valor
    if op == "presente":
        return ctx.get(arg) is not None
    if op == "ausente":
        return ctx.get(arg) is None
    if op == "!":
        return not evaluar(arg, ctx)
    if op == "and":
        pendiente: Indeterminado | None = None
        for a in arg:
            try:
                if not evaluar(a, ctx):
                    return False
            except Indeterminado as e:
                pendiente = pendiente or e
        if pendiente:
            raise pendiente
        return True
    if op == "or":
        pendiente = None
        for a in arg:
            try:
                if evaluar(a, ctx):
                    return True
            except Indeterminado as e:
                pendiente = pendiente or e
        if pendiente:
            raise pendiente
        return False
    if op == "in":
        valor, lista = evaluar(arg[0], ctx), evaluar(arg[1], ctx)
        return any(_comparable(valor, x)[0] == _comparable(valor, x)[1] for x in lista)
    a, b = _comparable(evaluar(arg[0], ctx), evaluar(arg[1], ctx))
    return _COMPARACIONES[op](a, b)
