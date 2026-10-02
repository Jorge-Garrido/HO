"""Importa el tarifario desde el Excel que mantiene facturación.

El Excel es solo formato de entrada: se valida, se normaliza y se guarda versionado
en la base de datos. En ejecución nunca se lee el Excel.

Estructura esperada por hoja (anexo): filas de título, luego un encabezado con
CÓDIGO INSTITUCIONAL | CUPS | DESCRIPCIÓN | TARIFA EN UVB | <valor UVB> | REDONDEADO.
La columna sin redondear tiene como encabezado el valor de la UVB (p. ej. 12110).
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import openpyxl

from ..data_preprocessing import normalize


@dataclass
class ItemTarifa:
    anexo: str
    codigo_institucional: str
    cups: str
    descripcion: str
    tarifa_uvb: Decimal
    valor: Decimal
    fila: int


@dataclass
class ResultadoImportacion:
    archivo: str
    sha256: str
    uvb_valor: Decimal | None
    items: list[ItemTarifa] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


def _columna(encabezado: list, *claves: str) -> int | None:
    for i, v in enumerate(encabezado):
        t = normalize.texto(str(v)) if isinstance(v, str) else None
        if t and any(c in t for c in claves):
            return i
    return None


def importar(ruta: Path, uvb_valor: Decimal | None = None) -> ResultadoImportacion:
    contenido = ruta.read_bytes()
    res = ResultadoImportacion(archivo=ruta.name, sha256=hashlib.sha256(contenido).hexdigest(), uvb_valor=uvb_valor)
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    uvb_hojas: set[Decimal] = set()
    for ws in wb.worksheets:
        anexo = ws.title.strip()
        encabezado = None
        cols: dict[str, int | None] = {}
        for n, fila in enumerate(ws.iter_rows(values_only=True), start=1):
            fila = list(fila)
            if encabezado is None:
                if _columna(fila, "CODIGO INSTITUCIONAL") is not None:
                    encabezado = fila
                    cols = {
                        "codigo": _columna(fila, "CODIGO INSTITUCIONAL"),
                        "cups": _columna(fila, "CUPS"),
                        "descripcion": _columna(fila, "DESCRIPCION"),
                        "uvb": _columna(fila, "UVB"),
                        "valor": _columna(fila, "REDONDEADO"),
                    }
                    faltan = [k for k, v in cols.items() if v is None]
                    if faltan:
                        res.errores.append(f"{anexo}: faltan columnas {faltan}")
                        break
                    uvb_hdr = next((v for v in fila if isinstance(v, (int, float))), None)
                    if uvb_hdr is not None:
                        uvb_hojas.add(Decimal(str(uvb_hdr)))
                continue
            if not any(v not in (None, "") for v in fila):
                continue
            _fila(res, anexo, n, fila, cols)
        if encabezado is None:
            res.errores.append(f"{anexo}: no se encontró el encabezado 'CÓDIGO INSTITUCIONAL'")
    wb.close()

    if res.uvb_valor is None:
        if len(uvb_hojas) == 1:
            res.uvb_valor = uvb_hojas.pop()
        else:
            res.errores.append(f"no se pudo determinar el valor de la UVB (encontrados: {sorted(uvb_hojas)})")
    _verificar(res)
    return res


def _fila(res: ResultadoImportacion, anexo: str, n: int, fila: list, cols: dict[str, int | None]) -> None:
    def celda(clave: str):
        i = cols[clave]
        return fila[i] if i is not None and i < len(fila) else None

    codigo = normalize.codigo(celda("codigo"))
    cups = normalize.cups(celda("cups"))
    descripcion = normalize.texto(celda("descripcion"))
    uvb = normalize.dinero(celda("uvb"))
    valor = normalize.dinero(celda("valor"))
    donde = f"{anexo} fila {n}"
    if not codigo:
        res.errores.append(f"{donde}: sin código institucional")
        return
    if not cups:
        res.errores.append(f"{donde}: sin CUPS")
        return
    if valor is None or uvb is None:
        res.errores.append(f"{donde}: tarifa no numérica")
        return
    if not re.fullmatch(r"[0-9A-Z]{6}", cups):
        res.avisos.append(f"{donde}: CUPS con formato no estándar '{cups}'")
    res.items.append(ItemTarifa(anexo, codigo, cups, descripcion or "", uvb, valor, n))


def _verificar(res: ResultadoImportacion) -> None:
    vistos: dict[tuple[str, str], int] = {}
    por_codigo: dict[str, list[ItemTarifa]] = {}
    for it in res.items:
        clave = (it.anexo, it.codigo_institucional)
        if clave in vistos:
            res.errores.append(
                f"{it.anexo}: código institucional {it.codigo_institucional} repetido (filas {vistos[clave]} y {it.fila})"
            )
        vistos[clave] = it.fila
        por_codigo.setdefault(it.codigo_institucional, []).append(it)
        if res.uvb_valor is not None:
            esperado = normalize.redondear_centena(it.tarifa_uvb * res.uvb_valor)
            if esperado != it.valor:
                res.avisos.append(
                    f"{it.anexo} fila {it.fila}: REDONDEADO {it.valor} no coincide con UVB x {res.uvb_valor} = {esperado}"
                )
    for codigo, items in por_codigo.items():
        if len({i.anexo for i in items}) > 1:
            filas = ", ".join(f"{i.anexo} fila {i.fila}" for i in items)
            res.avisos.append(f"código institucional {codigo} usado en varios anexos para servicios distintos ({filas})")


def exportar_csv(res: ResultadoImportacion, destino: Path) -> None:
    """Copia normalizada (texto plano, CUPS como texto) para pruebas y carga inicial."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["anexo", "codigo_institucional", "cups", "descripcion", "tarifa_uvb", "valor"])
        for it in res.items:
            w.writerow([it.anexo, it.codigo_institucional, it.cups, it.descripcion, it.tarifa_uvb, it.valor])
