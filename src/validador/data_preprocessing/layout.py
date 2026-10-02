"""Representación común de cualquier documento: palabras con posición, agrupadas en filas.

Sirve igual para PDF digitales (PyMuPDF) y, más adelante, para la salida del OCR.
El orden del texto que trae el PDF no se usa: el orden de lectura se reconstruye
con las coordenadas (filas por altura, de izquierda a derecha).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import normalize


@dataclass
class Palabra:
    texto: str
    x0: float
    y0: float
    x1: float
    y1: float
    pagina: int
    fila: int = -1
    orden: int = -1  # posición global en el orden de lectura
    # Solo para OCR: caja de la línea en píxeles de la imagen (ya girada) y posición de la
    # palabra dentro del texto de la línea. Sirven para recortar y releer.
    linea_px: tuple[float, float, float, float] | None = None
    linea_texto: str | None = None
    span: tuple[int, int] | None = None

    @property
    def norm(self) -> str:
        return normalize.texto(self.texto) or ""

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def alto(self) -> float:
        return self.y1 - self.y0


@dataclass
class Fila:
    pagina: int
    indice: int
    palabras: list[Palabra] = field(default_factory=list)

    @property
    def y0(self) -> float:
        return min(p.y0 for p in self.palabras)

    @property
    def y1(self) -> float:
        return max(p.y1 for p in self.palabras)


@dataclass
class DocLayout:
    filas: list[Fila]
    paginas_sin_texto: list[int] = field(default_factory=list)

    @property
    def palabras(self) -> list[Palabra]:
        return [p for f in self.filas for p in f.palabras]


def agrupar_filas(palabras: list[Palabra], pagina: int) -> list[Fila]:
    """Agrupa por altura: dos palabras están en la misma fila si sus centros verticales
    están a menos de la mitad de la altura de la palabra más baja."""
    filas: list[Fila] = []
    for p in sorted(palabras, key=lambda w: (w.yc, w.x0)):
        if filas:
            ultima = filas[-1]
            ref = sum(w.yc for w in ultima.palabras) / len(ultima.palabras)
            alto = min(p.alto, min(w.alto for w in ultima.palabras)) or 1
            if abs(p.yc - ref) <= alto * 0.5:
                ultima.palabras.append(p)
                continue
        filas.append(Fila(pagina=pagina, indice=len(filas), palabras=[p]))
    for f in filas:
        f.palabras.sort(key=lambda w: w.x0)
    return filas


def desde_pdf(ruta: Path, max_paginas: int | None = None) -> DocLayout:
    filas: list[Fila] = []
    sin_texto: list[int] = []
    with pymupdf.open(ruta) as doc:
        for pagina in doc:
            if max_paginas is not None and pagina.number >= max_paginas:
                break
            n = pagina.number + 1
            palabras = [
                Palabra(texto=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3], pagina=n)
                for w in pagina.get_text("words")
                if w[4].strip()
            ]
            if sum(len(p.texto) for p in palabras) < 50:
                sin_texto.append(n)
            filas.extend(agrupar_filas(palabras, n))
    orden = 0
    for i, f in enumerate(filas):
        f.indice = i
        for p in f.palabras:
            p.fila = i
            p.orden = orden
            orden += 1
    return DocLayout(filas=filas, paginas_sin_texto=sin_texto)
