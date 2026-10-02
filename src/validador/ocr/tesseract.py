"""Tesseract como segunda opinión sobre recortes (otro motor: sus errores no coinciden
con los de PP-OCR).

- ``leer_linea``: una línea (``--psm 7``) sin restricciones; devuelve palabras con caja.
- ``leer_digitos``: solo dígitos (whitelist) y, por cada carácter, las alternativas del
  modelo LSTM con su confianza (``lstm_choice_mode=2``): base del paso dígito a dígito.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

import cv2
import numpy as np

# Un hilo por llamada: el paralelismo se hace por procesos.
_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}


def disponible() -> bool:
    return shutil.which("tesseract") is not None


@dataclass
class PalabraT:
    texto: str
    x0: int
    x1: int
    confianza: float


@dataclass
class LecturaDigitos:
    texto: str
    confianza: float
    # alternativas[i] = [(carácter, confianza), ...] para la posición i
    alternativas: list[list[tuple[str, float]]]


def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""


def _correr(img: np.ndarray, *args: str) -> str:
    r = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "spa", "--psm", "7", *args],
        input=_png(img), capture_output=True, env=_ENV, timeout=30,
    )
    return r.stdout.decode("utf-8", errors="replace")


def leer_linea(img: np.ndarray) -> list[PalabraT]:
    tsv = _correr(img, "tsv")
    palabras = []
    for fila in tsv.splitlines()[1:]:
        c = fila.split("\t")
        if len(c) == 12 and c[11].strip():
            palabras.append(PalabraT(c[11].strip(), int(c[6]), int(c[6]) + int(c[8]), float(c[10])))
    return palabras


def leer_digitos(img: np.ndarray) -> LecturaDigitos:
    hocr = _correr(img, "-c", "tessedit_char_whitelist=0123456789", "-c", "lstm_choice_mode=2", "hocr")
    palabras = re.findall(r"<span class='ocrx_word'[^>]*x_wconf (\d+)'>([^<]*)", hocr)
    texto = "".join(html.unescape(t).strip() for _, t in palabras)
    conf = min((float(c) for c, _ in palabras), default=0.0)
    alternativas = []
    for grupo in re.findall(r"<span class='ocrx_cinfo' id='lstm_choices[^']*'>(.*?)</span></span>", hocr, re.S):
        opciones = re.findall(r"x_confs ([\d.]+)'>([^<]*)</span>", grupo)
        alternativas.append([(ch, float(cf)) for cf, ch in opciones if ch])
    return LecturaDigitos(texto=texto, confianza=conf, alternativas=alternativas)
