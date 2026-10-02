"""Recorte y limpieza de una zona de la imagen antes de releerla.

Lo que le quita el ruido al recorte es este preprocesamiento (no el motor):
escala de grises, ampliar hasta un alto de letra cómodo, binarizar (Otsu), borrar
líneas de tabla largas y dejar un margen blanco.
"""

from __future__ import annotations

import cv2
import numpy as np

ALTO_OBJETIVO = 64  # alto de la línea recortada después de ampliar (píxeles)
MARGEN = 12


def recortar(img: np.ndarray, caja: tuple[float, float, float, float], margen: int = 6) -> np.ndarray:
    x0, y0, x1, y1 = caja
    h, w = img.shape[:2]
    return img[max(0, int(y0) - margen) : min(h, int(y1) + margen), max(0, int(x0) - margen) : min(w, int(x1) + margen)]


def limpiar(recorte: np.ndarray) -> np.ndarray:
    gris = cv2.cvtColor(recorte, cv2.COLOR_RGB2GRAY) if recorte.ndim == 3 else recorte
    alto = max(gris.shape[0], 1)
    factor = min(4.0, max(1.0, ALTO_OBJETIVO / alto))
    if factor > 1:
        gris = cv2.resize(gris, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    gris = cv2.GaussianBlur(gris, (3, 3), 0)
    _, binaria = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Borrar líneas de tabla: trazos horizontales/verticales mucho más largos que una letra.
    tinta = 255 - binaria
    largo_h = max(30, binaria.shape[1] // 6)
    largo_v = max(30, binaria.shape[0] - 4)
    h_lineas = cv2.morphologyEx(tinta, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (largo_h, 1)))
    v_lineas = cv2.morphologyEx(tinta, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, largo_v)))
    tinta = cv2.subtract(tinta, cv2.bitwise_or(h_lineas, v_lineas))
    limpia = 255 - cv2.medianBlur(tinta, 3)
    return cv2.copyMakeBorder(limpia, MARGEN, MARGEN, MARGEN, MARGEN, cv2.BORDER_CONSTANT, value=255)


def sub_recorte(limpio: np.ndarray, desde: float, hasta: float) -> np.ndarray:
    """Parte horizontal [desde, hasta] (fracciones 0-1) de un recorte ya limpio."""
    w = limpio.shape[1]
    x0 = max(0, int(w * desde) - MARGEN)
    x1 = min(w, int(w * hasta) + MARGEN)
    parte = limpio[:, x0:x1]
    return cv2.copyMakeBorder(parte, 0, 0, MARGEN, MARGEN, cv2.BORDER_CONSTANT, value=255)
