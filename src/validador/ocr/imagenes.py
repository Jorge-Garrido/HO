"""Imágenes escaneadas dentro de un PDF, con su posición en la página.

Se usa la imagen incrustada a su resolución original (no un render de la página):
es más nítida y más rápida. Si la página no trae imagen, se renderiza a 300 dpi.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf

# Solo interesan imágenes que cubren buena parte de la página (no logos).
MIN_COBERTURA = 0.2
DPI_RENDER = 300


@dataclass
class ImagenPagina:
    pagina: int
    imagen: np.ndarray  # RGB, alto x ancho x 3
    # Rectángulo de la imagen en la página (puntos PDF): para convertir píxeles a puntos.
    bbox: tuple[float, float, float, float]


def _a_rgb(pix: pymupdf.Pixmap) -> np.ndarray:
    if pix.n - pix.alpha != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()


def extraer(ruta: Path, max_paginas: int | None = None) -> list[ImagenPagina]:
    resultado: list[ImagenPagina] = []
    with pymupdf.open(ruta) as doc:
        for pagina in doc:
            if max_paginas is not None and pagina.number >= max_paginas:
                break
            area = pagina.rect.width * pagina.rect.height
            infos = [
                i for i in pagina.get_image_info(xrefs=True)
                if i["xref"] and (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]) / area >= MIN_COBERTURA
            ]
            if infos:
                info = max(infos, key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))
                pix = pymupdf.Pixmap(doc, info["xref"])
                resultado.append(ImagenPagina(pagina.number + 1, _a_rgb(pix), tuple(info["bbox"])))
            elif len(pagina.get_text().strip()) < 50:
                pix = pagina.get_pixmap(dpi=DPI_RENDER)
                resultado.append(ImagenPagina(pagina.number + 1, _a_rgb(pix), tuple(pagina.rect)))
    return resultado
