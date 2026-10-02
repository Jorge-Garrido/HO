"""Convierte la salida del OCR al mismo DocLayout que usan los PDF digitales.

Así el localizador genérico funciona igual sobre texto digital y escaneado.
Las coordenadas se pasan a puntos PDF (la misma escala de los umbrales del
localizador) usando la posición de la imagen en la página.
"""

from __future__ import annotations

import math
import re
from statistics import median

from ..data_preprocessing.layout import DocLayout, Palabra, agrupar_filas
from .imagenes import ImagenPagina
from .motor import ResultadoOCR


# Inclinación mínima que vale la pena corregir (grados).
MIN_INCLINACION = 0.3


def inclinacion(res: ResultadoOCR) -> float:
    """Ángulo mediano (grados) del borde superior de las líneas largas: la inclinación del escaneo."""
    angulos = []
    for l in res.lineas:
        (x0, y0), (x1, y1) = l.caja[0], l.caja[1]
        if x1 - x0 > 80:
            angulos.append(math.degrees(math.atan2(y1 - y0, x1 - x0)))
    return median(angulos) if len(angulos) >= 3 else 0.0


def palabras(res: ResultadoOCR, img: ImagenPagina) -> list[Palabra]:
    x0p, y0p, x1p, y1p = img.bbox
    # Si la imagen se giró 90/270, su ancho en píxeles corresponde al alto del rectángulo.
    ancho_pt, alto_pt = (x1p - x0p, y1p - y0p) if res.rotacion in (0, 180) else (y1p - y0p, x1p - x0p)
    sx, sy = ancho_pt / res.ancho, alto_pt / res.alto
    out: list[Palabra] = []
    # Escaneo inclinado: se rotan las coordenadas (no la imagen) para que las filas queden horizontales.
    ang = inclinacion(res)
    cx, cy = res.ancho / 2, res.alto / 2
    cos, sin = math.cos(math.radians(-ang)), math.sin(math.radians(-ang))

    def enderezar(x: float, y: float) -> tuple[float, float]:
        if abs(ang) < MIN_INCLINACION:
            return x, y
        dx, dy = x - cx, y - cy
        return cx + dx * cos - dy * sin, cy + dx * sin + dy * cos

    for linea in res.lineas:
        puntos = [enderezar(x, y) for x, y in linea.caja]
        xs = [p[0] for p in puntos]
        ys = [p[1] for p in puntos]
        caja_px = (min(p[0] for p in linea.caja), min(p[1] for p in linea.caja), max(p[0] for p in linea.caja), max(p[1] for p in linea.caja))
        lx0, lx1, ly0, ly1 = min(xs), max(xs), min(ys), max(ys)
        texto = linea.texto
        # El OCR entrega líneas; se reparten en palabras en proporción a los caracteres.
        n = max(len(texto), 1)
        for m in re.finditer(r"\S+", texto):
            wx0 = lx0 + (lx1 - lx0) * m.start() / n
            wx1 = lx0 + (lx1 - lx0) * m.end() / n
            out.append(
                Palabra(
                    texto=m.group(0),
                    x0=wx0 * sx,
                    y0=ly0 * sy,
                    x1=wx1 * sx,
                    y1=ly1 * sy,
                    pagina=img.pagina,
                    linea_px=caja_px,  # en la imagen real (sin enderezar), para recortar
                    linea_texto=texto,
                    span=(m.start(), m.end()),
                )
            )
    return out


def a_layout(resultados: list[tuple[ImagenPagina, ResultadoOCR]]) -> DocLayout:
    filas = []
    for img, res in resultados:
        filas.extend(agrupar_filas(palabras(res, img), img.pagina))
    orden = 0
    for i, f in enumerate(filas):
        f.indice = i
        for p in f.palabras:
            p.fila = i
            p.orden = orden
            orden += 1
    return DocLayout(filas=filas)
