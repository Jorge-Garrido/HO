"""OCR con RapidOCR (modelos PP-OCR sobre ONNX Runtime, CPU, sin GPU ni Paddle).

Devuelve líneas con caja y confianza. Si el texto viene girado (90/180/270°), se
prueba cada orientación y se queda la que lee más texto con más confianza.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

# Por debajo de esto se prueba otra orientación.
MIN_CONFIANZA_ORIENTACION = 0.75
MIN_LINEAS_ORIENTACION = 3
# Otra orientación solo reemplaza a la anterior si lee claramente mejor. A 180° el
# clasificador endereza cada línea y la calidad sale casi igual, pero el orden de las
# filas queda invertido: ante empate gana la orientación original.
MEJORA_MINIMA = 1.15


@dataclass
class LineaOCR:
    texto: str
    caja: list[tuple[float, float]]  # 4 puntos (x, y) en píxeles de la imagen ya girada
    confianza: float


@dataclass
class ResultadoOCR:
    lineas: list[LineaOCR]
    rotacion: int  # grados aplicados a la imagen original (0, 90, 180, 270)
    ancho: int
    alto: int
    ms: int

    @property
    def fraccion_horizontal(self) -> float:
        """Fracción del texto (por caracteres) en cajas más anchas que altas."""
        total = sum(len(l.texto) for l in self.lineas) or 1
        horiz = 0
        for l in self.lineas:
            xs = [p[0] for p in l.caja]
            ys = [p[1] for p in l.caja]
            if (max(xs) - min(xs)) >= (max(ys) - min(ys)):
                horiz += len(l.texto)
        return horiz / total

    @property
    def calidad(self) -> float:
        """Caracteres leídos ponderados por confianza y por qué tan horizontal quedó el texto.

        El detector puede leer texto girado en cajas verticales; eso no sirve para
        reconstruir filas, así que se prefiere la orientación con texto horizontal."""
        return sum(len(l.texto) * l.confianza for l in self.lineas) * self.fraccion_horizontal


@lru_cache(maxsize=1)
def _motor():
    from rapidocr import RapidOCR

    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    # Sin clasificador de ángulo por línea: si enderezara cada línea, una página al revés
    # se leería "bien" con las filas en orden invertido. La orientación se decide por página.
    params = {"Global.log_level": "warning", "Global.use_cls": False}
    if hilos := os.environ.get("VALIDADOR_OCR_HILOS"):
        params["EngineConfig.onnxruntime.intra_op_num_threads"] = int(hilos)
    return RapidOCR(params=params)


def _leer(img: np.ndarray) -> list[LineaOCR]:
    # Parámetros explícitos: RapidOCR recuerda los de la llamada anterior (p. ej. use_det=False).
    r = _motor()(img, use_det=True, use_cls=False, use_rec=True)
    if r.boxes is None or r.txts is None:
        return []
    return [
        LineaOCR(texto=t, caja=[(float(x), float(y)) for x, y in caja], confianza=float(s))
        for caja, t, s in zip(r.boxes, r.txts, r.scores)
        if t and t.strip()
    ]


def leer(img: np.ndarray, probar_orientaciones: bool = True) -> ResultadoOCR:
    t0 = time.perf_counter()
    mejor: ResultadoOCR | None = None
    for rot in (0, 90, 270, 180):
        girada = np.ascontiguousarray(np.rot90(img, k=rot // 90)) if rot else img
        lineas = _leer(girada)
        r = ResultadoOCR(lineas, rot, girada.shape[1], girada.shape[0], 0)
        if mejor is None or r.calidad > mejor.calidad * MEJORA_MINIMA:
            mejor = r
        confianza = np.median([l.confianza for l in lineas]) if lineas else 0.0
        if not probar_orientaciones or (
            len(lineas) >= MIN_LINEAS_ORIENTACION
            and confianza >= MIN_CONFIANZA_ORIENTACION
            and r.fraccion_horizontal >= 0.8
        ):
            break
    mejor.ms = round((time.perf_counter() - t0) * 1000)
    return mejor
