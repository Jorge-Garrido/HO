"""Catálogos de referencia versionados (CUPSRips, CIE10, tablas SISPRO, glosas de la 2284...).

Un catálogo es una lista de códigos con descripción y *atributos* libres. Las reglas
consultan los atributos sin saber qué catálogo es: ``relacion_catalogo`` compara
``atributos.sexo`` del CIE10 con ``usuario.codSexo`` igual que compararía cualquier otro.

Cada importación es una versión inmutable con su vigencia. Una factura se valida contra la
versión vigente en su fecha de atención, de modo que una actualización de CUPS no cambia el
resultado de facturas ya validadas.

Formato de entrada (CSV o XLSX): una columna de código, una de descripción y cualquier otra
columna pasa a ``atributos``. Los nombres de columna se pueden indicar al importar porque las
tablas de SISPRO y de MinSalud no siempre usan los mismos encabezados.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl

from ..data_preprocessing import normalize


@dataclass
class ItemCatalogo:
    codigo: str
    descripcion: str | None
    atributos: dict[str, Any] = field(default_factory=dict)


@dataclass
class VersionCatalogo:
    nombre: str
    version_id: int | None
    vigente_desde: date | None
    vigente_hasta: date | None
    items: dict[str, ItemCatalogo]
    etiqueta: str | None = None

    def vigente_en(self, fecha: date | None) -> bool:
        if fecha is None:
            return self.vigente_hasta is None
        return (self.vigente_desde is None or self.vigente_desde <= fecha) and (
            self.vigente_hasta is None or fecha <= self.vigente_hasta
        )


@dataclass
class ResultadoImportacionCatalogo:
    nombre: str
    archivo: str
    sha256: str
    items: list[ItemCatalogo] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores and bool(self.items)


class Catalogos:
    """Catálogos cargados en memoria. Para cada nombre puede haber varias versiones."""

    def __init__(self, versiones: list[VersionCatalogo] | None = None):
        self._por_nombre: dict[str, list[VersionCatalogo]] = {}
        for v in versiones or []:
            self.agregar(v)

    def agregar(self, v: VersionCatalogo) -> None:
        lista = self._por_nombre.setdefault(v.nombre.upper(), [])
        lista.append(v)
        lista.sort(key=lambda x: x.vigente_desde or date.min, reverse=True)

    @property
    def nombres(self) -> list[str]:
        return sorted(self._por_nombre)

    def version(self, nombre: str, fecha: date | None) -> VersionCatalogo | None:
        """La versión vigente en la fecha (la de vigencia más reciente si hay varias)."""
        for v in self._por_nombre.get(nombre.upper(), []):
            if v.vigente_en(fecha):
                return v
        return None


def _normalizar_codigo(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return normalize.codigo(str(valor))


def _clave(texto: Any) -> str:
    return normalize.sin_tildes(str(texto or "")).strip().lower().replace(" ", "_")


def importar(
    ruta: Path,
    nombre: str,
    col_codigo: str = "codigo",
    col_descripcion: str = "descripcion",
    hoja: str | None = None,
) -> ResultadoImportacionCatalogo:
    contenido = ruta.read_bytes()
    res = ResultadoImportacionCatalogo(nombre=nombre.upper(), archivo=ruta.name, sha256=hashlib.sha256(contenido).hexdigest())
    if ruta.suffix.lower() in (".xlsx", ".xlsm"):
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        ws = wb[hoja] if hoja else wb.worksheets[0]
        filas = [list(f) for f in ws.iter_rows(values_only=True)]
    else:
        texto = contenido.decode("utf-8-sig")
        dialecto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t|")
        filas = list(csv.reader(io.StringIO(texto), dialecto))
    if not filas:
        res.errores.append("archivo vacío")
        return res
    encabezado = [_clave(c) for c in filas[0]]
    try:
        i_cod = encabezado.index(_clave(col_codigo))
    except ValueError:
        res.errores.append(f"no hay columna de código '{col_codigo}' (columnas: {', '.join(encabezado)})")
        return res
    i_desc = encabezado.index(_clave(col_descripcion)) if _clave(col_descripcion) in encabezado else None
    if i_desc is None:
        res.avisos.append(f"sin columna de descripción '{col_descripcion}'")
    vistos: set[str] = set()
    for n, fila in enumerate(filas[1:], 2):
        if not any(c not in (None, "") for c in fila):
            continue
        codigo = _normalizar_codigo(fila[i_cod] if i_cod < len(fila) else None)
        if not codigo:
            res.avisos.append(f"fila {n}: sin código, se omite")
            continue
        if codigo in vistos:
            res.errores.append(f"fila {n}: código {codigo} repetido")
            continue
        vistos.add(codigo)
        atributos = {
            encabezado[j]: (str(v).strip() if not isinstance(v, (int, float)) else v)
            for j, v in enumerate(fila)
            if j not in (i_cod, i_desc) and j < len(encabezado) and encabezado[j] and v not in (None, "")
        }
        descripcion = str(fila[i_desc]).strip() if i_desc is not None and i_desc < len(fila) and fila[i_desc] else None
        res.items.append(ItemCatalogo(codigo=codigo, descripcion=descripcion, atributos=atributos))
    return res
