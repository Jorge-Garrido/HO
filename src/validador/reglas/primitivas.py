"""Primitivas: las operaciones genéricas con las que se escriben las reglas.

Una primitiva no sabe de normas ni de EPS: compara, verifica presencia, busca en catálogos.
Las reglas (datos) le dan los parámetros. Agregar una primitiva es código revisado y con
pruebas; usarla en una regla nueva es solo dato.

Cada primitiva declara:
- ``Params``: modelo pydantic de sus parámetros (se valida al importar la regla).
- ``rutas(params)``: qué rutas del expediente lee (se verifica que existan).
- ``evaluar(ctx, params, recursos)``: devuelve cumple / falla / no_evaluable con un detalle.

"no_evaluable" significa que falta un dato, un catálogo o un contrato. Nunca es un error de
la factura: aparece en el resumen de reglas para saber qué falta cargar o extraer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..data_preprocessing import normalize
from .catalogos import Catalogos
from .expediente import Contexto, describir_valor

EstadoEvaluacion = Literal["cumple", "falla", "no_evaluable", "no_aplica"]


@dataclass
class Evaluacion:
    estado: EstadoEvaluacion
    detalle: str = ""


@dataclass
class Recursos:
    catalogos: Catalogos
    fecha: date | None
    # nombre de catálogo -> versión usada (queda en el resumen de la factura)
    catalogos_usados: dict[str, int] = field(default_factory=dict)


def cumple() -> Evaluacion:
    return Evaluacion("cumple")


def falla(detalle: str) -> Evaluacion:
    return Evaluacion("falla", detalle)


def no_evaluable(detalle: str) -> Evaluacion:
    return Evaluacion("no_evaluable", detalle)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Primitiva:
    nombre: ClassVar[str]
    descripcion: ClassVar[str]
    Params: ClassVar[type[_Params]]

    @classmethod
    def rutas(cls, p: Any) -> set[str]:
        return set()

    @classmethod
    def evaluar(cls, ctx: Contexto, p: Any, r: Recursos) -> Evaluacion:  # pragma: no cover
        raise NotImplementedError


REGISTRO: dict[str, type[Primitiva]] = {}


def registrar(cls: type[Primitiva]) -> type[Primitiva]:
    REGISTRO[cls.nombre] = cls
    return cls


# --- conversión de tipos ------------------------------------------------------------

Tipo = Literal["fecha", "numero", "texto"]


def convertir(valor: Any, tipo: Tipo) -> Any:
    """Convierte para comparar. Devuelve None si no se puede (el dato no es del tipo)."""
    if valor is None:
        return None
    if tipo == "fecha":
        if isinstance(valor, datetime):
            return valor.date()
        if isinstance(valor, date):
            return valor
        return normalize.fecha(str(valor))
    if tipo == "numero":
        try:
            return valor if isinstance(valor, Decimal) else normalize.dinero(valor)
        except (InvalidOperation, ValueError):
            return None
    return normalize.sin_tildes(str(valor)).strip().upper()


OPERACIONES = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "empieza_por": lambda a, b: str(a).startswith(str(b)),
    "contiene": lambda a, b: b in a if isinstance(a, (list, tuple, set)) else str(b) in str(a),
}
Operador = Literal["==", "!=", "<", "<=", ">", ">=", "empieza_por", "contiene"]


# --- presencia ------------------------------------------------------------------------


@registrar
class Requerido(Primitiva):
    nombre = "requerido"
    descripcion = "Todos los campos indicados deben tener valor."

    class Params(_Params):
        campos: list[str] = Field(min_length=1)

    @classmethod
    def rutas(cls, p):
        return set(p.campos)

    @classmethod
    def evaluar(cls, ctx, p, r):
        faltan = [c for c in p.campos if ctx.get(c) is None]
        return falla(f"sin valor: {', '.join(faltan)}") if faltan else cumple()


@registrar
class Nulo(Primitiva):
    nombre = "nulo"
    descripcion = "Los campos indicados deben venir vacíos (null) en este caso."

    class Params(_Params):
        campos: list[str] = Field(min_length=1)

    @classmethod
    def rutas(cls, p):
        return set(p.campos)

    @classmethod
    def evaluar(cls, ctx, p, r):
        con_valor = [f"{c}={describir_valor(c, ctx.get(c))}" for c in p.campos if ctx.get(c) is not None]
        return falla(f"debería venir vacío: {', '.join(con_valor)}") if con_valor else cumple()


@registrar
class DocumentoPresente(Primitiva):
    nombre = "documento_presente"
    descripcion = "La carpeta trae al menos uno (o todos) de los soportes indicados por prefijo (Res. 2284)."

    class Params(_Params):
        tipos: list[str] = Field(min_length=1)
        todos: bool = False

    @classmethod
    def rutas(cls, p):
        return {"documentos.presentes"}

    @classmethod
    def evaluar(cls, ctx, p, r):
        presentes = set(ctx.get("documentos.presentes") or [])
        faltan = [t for t in p.tipos if t not in presentes]
        ok = not faltan if p.todos else len(faltan) < len(p.tipos)
        return cumple() if ok else falla(f"falta el soporte {' y '.join(faltan) if p.todos else ' o '.join(faltan)}")


# --- formato y comparación ------------------------------------------------------------


@registrar
class Formato(Primitiva):
    nombre = "formato"
    descripcion = "El campo cumple una expresión regular (sobre el texto tal cual)."

    class Params(_Params):
        campo: str
        patron: str

        @field_validator("patron")
        @classmethod
        def _compila(cls, v: str) -> str:
            re.compile(v)
            return v

    @classmethod
    def rutas(cls, p):
        return {p.campo}

    @classmethod
    def evaluar(cls, ctx, p, r):
        valor = ctx.get(p.campo)
        if valor is None:
            return no_evaluable(f"sin valor en {p.campo}")
        texto = valor.isoformat() if isinstance(valor, date) else str(valor)
        return cumple() if re.fullmatch(p.patron, texto) else falla(f"{p.campo}={describir_valor(p.campo, valor)} no tiene el formato esperado")


@registrar
class Comparar(Primitiva):
    nombre = "comparar"
    descripcion = "Compara un campo con otro campo (b) o con un valor fijo (valor)."

    class Params(_Params):
        a: str
        op: Operador
        b: str | None = None
        valor: Any = None
        tipo: Tipo = "texto"

        @model_validator(mode="after")
        def _uno(self):
            if (self.b is None) == (self.valor is None):
                raise ValueError("indique 'b' (otro campo) o 'valor' (fijo), no ambos")
            return self

    @classmethod
    def rutas(cls, p):
        return {p.a} | ({p.b} if p.b else set())

    @classmethod
    def evaluar(cls, ctx, p, r):
        crudo_a = ctx.get(p.a)
        crudo_b = ctx.get(p.b) if p.b else p.valor
        if crudo_a is None or crudo_b is None:
            return no_evaluable(f"sin valor en {p.a if crudo_a is None else p.b}")
        a, b = convertir(crudo_a, p.tipo), convertir(crudo_b, p.tipo)
        if a is None or b is None:
            return no_evaluable(f"{p.a if a is None else p.b} no es de tipo {p.tipo}")
        if OPERACIONES[p.op](a, b):
            return cumple()
        derecha = f"{p.b}={describir_valor(p.b, crudo_b)}" if p.b else describir_valor("", crudo_b)
        return falla(f"se esperaba {p.a} {p.op} {derecha}, y {p.a}={describir_valor(p.a, crudo_a)}")


@registrar
class Rango(Primitiva):
    nombre = "rango"
    descripcion = "El campo está entre 'desde' y 'hasta' (campos), ambos inclusive."

    class Params(_Params):
        valor: str
        desde: str | None = None
        hasta: str | None = None
        tipo: Literal["fecha", "numero"] = "fecha"

        @model_validator(mode="after")
        def _limite(self):
            if self.desde is None and self.hasta is None:
                raise ValueError("indique al menos 'desde' o 'hasta'")
            return self

    @classmethod
    def rutas(cls, p):
        return {p.valor} | {x for x in (p.desde, p.hasta) if x}

    @classmethod
    def evaluar(cls, ctx, p, r):
        valores = {k: ctx.get(k) for k in (p.valor, p.desde, p.hasta) if k}
        faltan = [k for k, v in valores.items() if v is None]
        if faltan:
            return no_evaluable(f"sin valor en {', '.join(faltan)}")
        v = convertir(valores[p.valor], p.tipo)
        lo = convertir(valores[p.desde], p.tipo) if p.desde else None
        hi = convertir(valores[p.hasta], p.tipo) if p.hasta else None
        if v is None:
            return no_evaluable(f"{p.valor} no es de tipo {p.tipo}")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            limites = " a ".join(describir_valor(k, valores[k]) for k in (p.desde, p.hasta) if k)
            return falla(f"{p.valor}={describir_valor(p.valor, valores[p.valor])} fuera de {limites}")
        return cumple()


@registrar
class Distintos(Primitiva):
    nombre = "distintos"
    descripcion = "Los campos con valor no se repiten entre sí (p. ej. diagnóstico principal y relacionados)."

    class Params(_Params):
        campos: list[str] = Field(min_length=2)

    @classmethod
    def rutas(cls, p):
        return set(p.campos)

    @classmethod
    def evaluar(cls, ctx, p, r):
        vistos: dict[str, str] = {}
        repetidos = []
        for c in p.campos:
            v = ctx.get(c)
            if v is None:
                continue
            clave = str(v).strip().upper()
            if clave in vistos:
                repetidos.append(f"{c} repite {vistos[clave]} ({describir_valor(c, v)})")
            else:
                vistos[clave] = c
        return falla("; ".join(repetidos)) if repetidos else cumple()


@registrar
class Consecutivo(Primitiva):
    nombre = "consecutivo"
    descripcion = "En una lista, el campo consecutivo inicia en 1, aumenta de uno en uno y no se repite."

    class Params(_Params):
        lista: str
        campo: str = "consecutivo"

    @classmethod
    def rutas(cls, p):
        return {p.lista}

    @classmethod
    def evaluar(cls, ctx, p, r):
        lista = ctx.get(p.lista)
        if not isinstance(lista, list):
            return no_evaluable(f"sin lista en {p.lista}")
        # Una lista de listas (p. ej. los servicios de cada usuario) se verifica por sublista.
        grupos = lista if lista and all(isinstance(x, list) for x in lista) else [lista]
        errores = []
        for n, grupo in enumerate(grupos, 1):
            try:
                valores = [int(x.get(p.campo)) for x in grupo]
            except (TypeError, ValueError):
                errores.append(f"grupo {n}: hay registros sin {p.campo} numérico")
                continue
            if sorted(valores) != list(range(1, len(valores) + 1)):
                errores.append(f"grupo {n}: consecutivos {valores[:10]}{'…' if len(valores) > 10 else ''}, se esperaba 1..{len(valores)}")
        return falla(f"{p.lista}: " + "; ".join(errores)) if errores else cumple()


# --- catálogos --------------------------------------------------------------------------


def _version_catalogo(r: Recursos, nombre: str):
    v = r.catalogos.version(nombre, r.fecha)
    if v is not None and v.version_id is not None:
        r.catalogos_usados[v.nombre] = v.version_id
    return v


@registrar
class EnCatalogo(Primitiva):
    nombre = "en_catalogo"
    descripcion = "El código existe en el catálogo vigente a la fecha de atención."

    class Params(_Params):
        campo: str
        catalogo: str

    @classmethod
    def rutas(cls, p):
        return {p.campo}

    @classmethod
    def evaluar(cls, ctx, p, r):
        v = _version_catalogo(r, p.catalogo)
        if v is None:
            return no_evaluable(f"catálogo {p.catalogo} no cargado o sin versión vigente")
        codigo = ctx.get(p.campo)
        if codigo is None:
            return no_evaluable(f"sin valor en {p.campo}")
        codigo = normalize.codigo(str(codigo))
        return cumple() if codigo in v.items else falla(f"{p.campo}={codigo} no existe en {v.nombre}")


class Restriccion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atributo: str  # columna del catálogo (atributos del ítem)
    op: Operador
    valor: str  # ruta del expediente
    tipo: Tipo = "texto"
    # Valores del atributo que significan "aplica a todos" (p. ej. sexo 'A' o 'Z').
    comodines: list[str] = Field(default_factory=list)


@registrar
class RelacionCatalogo(Primitiva):
    nombre = "relacion_catalogo"
    descripcion = (
        "Busca el código en el catálogo y verifica sus atributos contra el expediente "
        "(sexo, edad mínima/máxima, coberturas permitidas...). Atributo vacío o comodín: no restringe."
    )

    class Params(_Params):
        catalogo: str
        codigo: str
        restricciones: list[Restriccion] = Field(min_length=1)

    @classmethod
    def rutas(cls, p):
        return {p.codigo} | {x.valor for x in p.restricciones}

    @classmethod
    def evaluar(cls, ctx, p, r):
        v = _version_catalogo(r, p.catalogo)
        if v is None:
            return no_evaluable(f"catálogo {p.catalogo} no cargado o sin versión vigente")
        codigo = ctx.get(p.codigo)
        if codigo is None:
            return no_evaluable(f"sin valor en {p.codigo}")
        item = v.items.get(normalize.codigo(str(codigo)) or "")
        if item is None:
            return no_evaluable(f"{codigo} no está en {v.nombre} (lo verifica en_catalogo)")
        incumplidas = []
        for x in p.restricciones:
            atributo = item.atributos.get(x.atributo)
            if atributo in (None, "") or str(atributo).strip().upper() in {c.upper() for c in x.comodines}:
                continue
            crudo = ctx.get(x.valor)
            if crudo is None:
                return no_evaluable(f"sin valor en {x.valor}")
            # El atributo es el lado izquierdo: "edad_minima <= derivado.edad_anios".
            izquierda = atributo if x.op == "contiene" else convertir(atributo, x.tipo)
            derecha = convertir(crudo, x.tipo)
            if x.op == "contiene" and isinstance(izquierda, str):
                izquierda = [s.strip().upper() for s in re.split(r"[,;|]", izquierda)]
            if izquierda is None or derecha is None or not OPERACIONES[x.op](izquierda, derecha):
                incumplidas.append(f"{v.nombre}.{x.atributo}={atributo} vs {x.valor}={describir_valor(x.valor, crudo)}")
        return falla(f"{codigo}: " + "; ".join(incumplidas)) if incumplidas else cumple()


# --- texto de soportes ---------------------------------------------------------------------


@registrar
class SeccionesPresentes(Primitiva):
    nombre = "secciones_presentes"
    descripcion = (
        "El texto del soporte contiene cada sección exigida, buscada por cualquiera de sus "
        "sinónimos (sin tildes, sin distinguir mayúsculas)."
    )

    class Params(_Params):
        texto: str
        secciones: dict[str, list[str]] = Field(min_length=1)
        max_faltantes: int = 0

    @classmethod
    def rutas(cls, p):
        return {p.texto}

    @classmethod
    def evaluar(cls, ctx, p, r):
        texto = ctx.get(p.texto)
        if texto is None:
            return no_evaluable(f"sin texto en {p.texto} (documento ausente o escaneado)")
        plano = re.sub(r"\s+", " ", normalize.sin_tildes(str(texto)).upper())
        faltan = [
            nombre for nombre, sinonimos in p.secciones.items()
            if not any(re.sub(r"\s+", " ", normalize.sin_tildes(s).upper()) in plano for s in sinonimos)
        ]
        if len(faltan) > p.max_faltantes:
            return falla(f"no se encontró: {', '.join(faltan)}")
        return cumple()


def describir() -> list[dict[str, Any]]:
    """Catálogo de primitivas con el esquema de sus parámetros (para el agente y la documentación)."""
    return [
        {"nombre": c.nombre, "descripcion": c.descripcion, "params": c.Params.model_json_schema()}
        for c in sorted(REGISTRO.values(), key=lambda c: c.nombre)
    ]
