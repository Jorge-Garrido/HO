"""Cascada de OCR para verificar en el escaneo un valor que ya confirmaron las fuentes digitales.

Etapas (se avanza solo si la anterior no confirmó):
  1. paddle         PP-OCR sobre la página completa + localizador (valor junto a su etiqueta)
  2. paddle_recorte  recorte de la línea, limpio, releído con el reconocedor de PP-OCR
     tesseract       mismo recorte con Tesseract (otro motor, errores no correlacionados)
     tesseract       solo la parte numérica, restringido a dígitos
  3. digitos        la lectura tiene el largo esperado y cada posición que no cuadra tiene
                    el dígito esperado entre las alternativas de Tesseract
  4. (fuera de aquí) modelo pequeño por API con el texto crudo, y revisión humana

Resultado:
  - confirmado: el escaneo dice el valor esperado (se indica en qué etapa)
  - diferencia: al menos dos lecturas independientes coinciden en OTRO valor → el
    escaneo realmente dice otra cosa (no es error de OCR)
  - no_legible: no se pudo confirmar ni descartar → siguiente nivel (LLM/humano)
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from .. import localizar
from ..compare import levenshtein
from ..ingest.util import sha256
from ..data_preprocessing import layout as layout_mod
from ..data_preprocessing.layout import DocLayout, Palabra
from ..data_preprocessing import recorte
from . import a_layout, imagenes, motor, tesseract

VERSION_CACHE = "ppocrv6-small-sincls-1"
CAMPOS_CASCADA = {"numero_autorizacion", "paciente_id", "cups"}


@dataclass
class DocumentoEscaneado:
    layout: DocLayout
    imagenes: dict[int, np.ndarray]  # página -> imagen ya girada como la leyó el OCR
    ms_ocr: int = 0


@dataclass
class Resolucion:
    campo: str
    esperado: str
    estado: Literal["confirmado", "diferencia", "no_legible"]
    etapa: str | None = None
    leido: str | None = None
    confianza: float = 0.0
    lecturas: dict[str, str] = field(default_factory=dict)
    detalle: str = ""


# --- lectura de la página (etapa 1) con caché ------------------------------------


def leer_pdf(ruta: Path, cache_dir: Path | None = None) -> DocumentoEscaneado:
    imgs = imagenes.extraer(ruta)
    archivo = cache_dir / f"{sha256(ruta)}-{VERSION_CACHE}.json" if cache_dir else None
    if archivo and archivo.exists():
        datos = json.loads(archivo.read_text())
        res = [
            motor.ResultadoOCR([motor.LineaOCR(**l) for l in d["lineas"]], d["rotacion"], d["ancho"], d["alto"], d["ms"])
            for d in datos
        ]
    else:
        res = [motor.leer(i.imagen) for i in imgs]
        if archivo:
            archivo.parent.mkdir(parents=True, exist_ok=True)
            archivo.write_text(json.dumps([
                {"lineas": [l.__dict__ for l in r.lineas], "rotacion": r.rotacion, "ancho": r.ancho, "alto": r.alto, "ms": r.ms}
                for r in res
            ]))
    girada = {i.pagina: (np.ascontiguousarray(np.rot90(i.imagen, k=r.rotacion // 90)) if r.rotacion else i.imagen) for i, r in zip(imgs, res)}
    ocr = a_layout.a_layout(list(zip(imgs, res)))
    # Páginas con texto digital y sin imagen escaneada (autorización impresa desde un portal):
    # se leen directo, sin OCR.
    con_imagen = {i.pagina for i in imgs}
    digital = [f for f in layout_mod.desde_pdf(ruta).filas if f.pagina not in con_imagen]
    filas = sorted(ocr.filas + digital, key=lambda f: (f.pagina, f.y0))
    for n, f in enumerate(filas):
        f.indice = n
        for p in f.palabras:
            p.fila = n
    return DocumentoEscaneado(DocLayout(filas=filas), girada, sum(r.ms for r in res))


# --- utilidades --------------------------------------------------------------------


def _objetivo(campo: str, esperado: str) -> str:
    """Lo que se compara: para el documento del paciente, solo el número."""
    e = localizar._compacto(esperado)
    return re.sub(r"^[A-Z]+", "", e) if campo == "paciente_id" else e


def _partes(texto: str) -> list[tuple[str, int, int]]:
    """Partes alfanuméricas de un texto con su posición: 'P020-307952247' -> P020, 307952247, ..."""
    partes = [(localizar._compacto(m.group(0)), m.start(), m.end()) for m in re.finditer(r"[0-9A-Za-z]+", texto)]
    # también partes numéricas dentro de palabras mixtas ('CC74370558BUITRAGO')
    partes += [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\d{4,}", texto)]
    return [p for p in partes if p[0]]


def _coincide(texto: str, e: str) -> bool:
    return any(p == e for p, _, _ in _partes(texto)) or e in re.sub(r"\D", "", texto) and len(e) >= 6 and any(
        e in p and localizar._contenido_valido(p, e) for p, _, _ in _partes(texto)
    )


def _mas_cercana(texto: str, e: str) -> tuple[str, int, int, int] | None:
    mejor = None
    for p, a, b in _partes(texto):
        if abs(len(p) - len(e)) > 2:
            continue
        d = levenshtein(p, e)
        if mejor is None or d < mejor[3]:
            mejor = (p, a, b, d)
    return mejor


def _lineas(doc: DocumentoEscaneado, campo: str, v: localizar.Verificacion) -> list[tuple[int, tuple, str]]:
    """Líneas (página, caja en píxeles, texto) donde releer."""
    elegidas: list[Palabra] = []
    # Solo se relee donde el valor está junto a la etiqueta del campo. Un número encontrado
    # "sin contexto" puede ser otro dato (p. ej. la cédula en lugar de la autorización).
    if v.estado in ("diferencia", "confirmado") and v.bbox and v.pagina:
        x0, y0, x1, y1 = v.bbox
        elegidas = [p for p in doc.layout.palabras if p.pagina == v.pagina and p.x0 >= x0 - 1 and p.x1 <= x1 + 1 and p.y0 >= y0 - 1 and p.y1 <= y1 + 1]
    if not elegidas:
        for a in localizar.anclas(doc.layout, campo)[:4]:
            elegidas += list(a.palabras) + [p for p in doc.layout.palabras if localizar._cerca(a, [p]) and p not in a.palabras]
    vistas: dict[tuple, tuple[int, tuple, str]] = {}
    for p in elegidas:
        if p.linea_px:
            vistas.setdefault((p.pagina, p.linea_px), (p.pagina, p.linea_px, p.linea_texto or p.texto))
    return list(vistas.values())[:8]


# Cuánto se amplía la caja de la línea a cada lado, en altos de línea (~1-2 caracteres).
AMPLIACION_CAJA = 1.5


def _ampliar(caja: tuple, forma: tuple) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = caja
    alto = max(y1 - y0, 1)
    h, w = forma[:2]
    extra = alto * AMPLIACION_CAJA
    return (max(0.0, x0 - extra), max(0.0, y0 - alto * 0.15), min(float(w), x1 + extra), min(float(h), y1 + alto * 0.15))


def _dudoso(alternativas: list[tuple[str, float]], leido: str, esperado: str) -> bool:
    """El OCR dudó en esta posición: el dígito leído no tiene confianza alta y el esperado
    aparece como alternativa con alguna confianza."""
    conf = dict(alternativas)
    return conf.get(leido, 0) < 90 and conf.get(esperado, -1) >= 5


def _paddle_recorte(img: np.ndarray) -> str:
    color = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB) if img.ndim == 2 else img
    r = motor._motor()(color, use_det=False, use_cls=False, use_rec=True)
    return " ".join(t for t in (getattr(r, "txts", None) or []) if t)


def codigos_cups(doc: DocumentoEscaneado) -> list[str]:
    """Números de 6 dígitos que aparecen en el escaneo (candidatos a CUPS autorizados)."""
    vistos: list[str] = []
    for p in doc.layout.palabras:
        for m in re.findall(r"(?<!\d)(\d{6})(?!\d)", p.texto):
            if m not in vistos and not re.fullmatch(r"(19|20)\d{4}", m):
                vistos.append(m)
    return vistos


def leer_valor(doc: DocumentoEscaneado, campo: str) -> Resolucion | None:
    """Sin valor esperado confirmado: ¿qué número hay junto a la etiqueta en el escaneo?
    Se extrae con PP-OCR y se confirma releyendo la línea con Tesseract."""
    valor = localizar.extraer(doc.layout, campo)
    if not valor:
        return None
    e = _objetivo(campo, valor)
    lecturas = {"paddle": valor}
    v = localizar.verificar(doc.layout, campo, valor)
    for pagina, caja, _ in _lineas(doc, campo, v):
        img = doc.imagenes.get(pagina)
        if img is None or not tesseract.disponible():
            continue
        t_txt = " ".join(p.texto for p in tesseract.leer_linea(recorte.limpiar(recorte.recortar(img, caja))))
        lecturas.setdefault("tesseract", t_txt)
        if _coincide(t_txt, e):
            return Resolucion(campo, valor, "confirmado", "tesseract", valor, 0.9, lecturas, "leído en el escaneo por dos motores")
    return Resolucion(campo, valor, "no_legible", "paddle", valor, 0.6, lecturas, "leído en el escaneo por un solo motor")


# --- cascada ------------------------------------------------------------------------


def resolver(doc: DocumentoEscaneado, campo: str, esperado: str) -> Resolucion:
    e = _objetivo(campo, esperado)
    v = localizar.verificar(doc.layout, campo, esperado)
    if v.estado == "confirmado":
        return Resolucion(campo, esperado, "confirmado", "paddle", e, 0.98, {"paddle": v.valor_encontrado or ""})
    if campo == "cups" and (v.estado == "confirmado_sin_contexto" or e in codigos_cups(doc)):
        # Un CUPS exacto de 6 dígitos dentro de la autorización: no hace falta la etiqueta de la
        # tabla, y gana sobre un código parecido que esté junto al encabezado.
        return Resolucion(campo, esperado, "confirmado", "paddle", e, 0.9, {"paddle": e})
    if campo not in CAMPOS_CASCADA or not tesseract.disponible():
        return Resolucion(campo, esperado, "no_legible", None, None, 0.0, {"paddle": v.valor_encontrado or ""}, v.estado)

    # Votos por valor y por *motor*: PP-OCR (página y recorte) y Tesseract (línea y dígitos). Dos
    # lecturas del mismo motor no son independientes: confunden la misma forma (6/8, 0/Q).
    votos: Counter[str] = Counter()
    motores: dict[str, set[str]] = {}

    def votar(valor: str, motor: str) -> None:
        if e.isdigit() and not valor.isdigit():
            return  # 'P' por '0' tras una raya del escaneo: no es una lectura válida de un número
        votos[valor] += 1
        motores.setdefault(valor, set()).add(motor)

    lecturas: dict[str, str] = {}
    digitos: tuple[int, str] | None = None
    if v.valor_encontrado:
        cerca = _mas_cercana(v.valor_encontrado, e)
        if cerca:
            votar(cerca[0], "ppocr")
        lecturas["paddle"] = v.valor_encontrado

    # Cada línea se lee con su caja y, si no se confirma, con la caja ampliada: el detector a
    # veces deja por fuera el primer dígito y los dos motores leen el mismo número cortado.
    intentos = [
        (pagina, c)
        for pagina, caja, _ in _lineas(doc, campo, v)
        if doc.imagenes.get(pagina) is not None
        for c in (caja, _ampliar(caja, doc.imagenes[pagina].shape))
    ]
    for pagina, caja in intentos:
        img = doc.imagenes[pagina]
        limpio = recorte.limpiar(recorte.recortar(img, caja))

        # 2a. PP-OCR reconocedor sobre el recorte limpio
        p_txt = _paddle_recorte(limpio)
        lecturas.setdefault("paddle_recorte", p_txt)
        if _coincide(p_txt, e):
            return Resolucion(campo, esperado, "confirmado", "paddle_recorte", e, 0.9, {**lecturas, "paddle_recorte": p_txt})

        # 2b. Tesseract sobre la línea completa
        palabras = tesseract.leer_linea(limpio)
        t_txt = " ".join(p.texto for p in palabras)
        lecturas.setdefault("tesseract", t_txt)
        if _coincide(t_txt, e):
            return Resolucion(campo, esperado, "confirmado", "tesseract", e, 0.9, {**lecturas, "tesseract": t_txt})

        for motor, txt in (("ppocr", p_txt), ("tesseract", t_txt)):
            if (c := _mas_cercana(txt, e)) and c[3] <= 2:
                votar(c[0], motor)

        # 2c. Solo la parte numérica, restringida a dígitos
        mejor = None
        for pw in palabras:
            c = _mas_cercana(pw.texto, e)
            if c and (mejor is None or c[3] < mejor[1][3]):
                mejor = (pw, c)
        if mejor is None or not re.fullmatch(r"\d+", e):
            continue
        pw, (parte, a, b, _) = mejor
        ancho = max(limpio.shape[1], 1)
        n = max(len(pw.texto), 1)
        desde = (pw.x0 + (pw.x1 - pw.x0) * a / n) / ancho
        hasta = (pw.x0 + (pw.x1 - pw.x0) * b / n) / ancho
        dig = tesseract.leer_digitos(recorte.sub_recorte(limpio, desde, hasta))
        lecturas.setdefault("tesseract_digitos", dig.texto)
        if dig.texto == e:
            return Resolucion(campo, esperado, "confirmado", "tesseract", e, 0.9, {**lecturas, "tesseract_digitos": dig.texto})
        if dig.texto and abs(len(dig.texto) - len(e)) <= 1:
            votar(dig.texto, "tesseract")

        # 3. Dígito a dígito (se decide al final): cada posición distinta debe ser dudosa para el
        # OCR (confianza baja) y tener el dígito esperado entre sus alternativas.
        if len(dig.texto) == len(e) and len(dig.alternativas) == len(e):
            distintas = [i for i, (x, y) in enumerate(zip(dig.texto, e)) if x != y]
            if distintas and all(_dudoso(dig.alternativas[i], dig.texto[i], e[i]) for i in distintas):
                digitos = (len(distintas), dig.texto)

    # ¿Dos lecturas independientes coinciden en otro valor? Eso pesa más que un dígito dudoso.
    if votos:
        valor, n = votos.most_common(1)[0]
        if len(motores[valor]) >= 2 and valor != e:
            if digitos:
                # Dos motores leen otro valor, pero Tesseract duda justo en los dígitos que difieren
                # (el esperado está entre sus alternativas): fuentes de números antiguos (6/8, 2/3) o
                # rayas del escaneo. No se afirma la diferencia: revisión humana.
                return Resolucion(campo, esperado, "no_legible", None, valor, 0.5, lecturas,
                                  f"dos motores leen {valor}, pero el dígito que difiere es dudoso")
            return Resolucion(campo, esperado, "diferencia", "tesseract", valor, 0.9, lecturas,
                              f"dos motores distintos leen {valor}")
    if digitos:
        return Resolucion(campo, esperado, "confirmado", "digitos", e, 0.7, lecturas,
                          f"{digitos[0]} dígito(s) dudosos; el esperado está entre las alternativas del OCR")
    if votos:
        return Resolucion(campo, esperado, "no_legible", None, valor, 0.4, lecturas, "lecturas en desacuerdo")
    return Resolucion(campo, esperado, "no_legible", None, None, 0.0, lecturas, v.estado)
