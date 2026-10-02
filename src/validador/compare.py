"""Comparadores: exacta, Hamming, Levenshtein ponderado para OCR, tokens y numérica."""

from __future__ import annotations

from decimal import Decimal

from .data_preprocessing import normalize

# Confusiones típicas del OCR: pesan menos porque son más probablemente errores de lectura.
CONFUSIONES_OCR = {frozenset(p) for p in ("56", "17", "08", "38", "49", "25")}
PESO_CONFUSION = 0.5


def hamming(a: str, b: str) -> int:
    if len(a) != len(b):
        raise ValueError("hamming requiere cadenas del mismo largo")
    return sum(x != y for x, y in zip(a, b))


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (ca != cb)))
        previa = actual
    return previa[-1]


def distancia_digitos(a: str, b: str) -> int:
    """Hamming si tienen el mismo largo, Levenshtein si no."""
    return hamming(a, b) if len(a) == len(b) else levenshtein(a, b)


def distancia_ocr(a: str, b: str) -> float:
    """Como distancia_digitos, pero las confusiones típicas del OCR cuentan la mitad."""
    if len(a) != len(b):
        return float(levenshtein(a, b))
    total = 0.0
    for x, y in zip(a, b):
        if x != y:
            total += PESO_CONFUSION if frozenset((x, y)) in CONFUSIONES_OCR else 1.0
    return total


def similitud_tokens(a: str | None, b: str | None) -> float:
    """Coeficiente de Dice sobre conjuntos de tokens (el orden no importa)."""
    ta = {normalize.raiz(t) for t in normalize.tokens(a)}
    tb = {normalize.raiz(t) for t in normalize.tokens(b)}
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


def mismos_tokens(a: str | None, b: str | None) -> bool:
    return normalize.tokens(a, quitar_stopwords=False) == normalize.tokens(b, quitar_stopwords=False)


def iguales(comparador: str, a: str, b: str) -> bool:
    if comparador == "tokens":
        return mismos_tokens(a, b)
    if comparador == "numerica":
        da, db = normalize.dinero(a), normalize.dinero(b)
        return da is not None and db is not None and Decimal(da) == Decimal(db)
    if comparador == "conjunto":
        return set(a.split(",")) == set(b.split(","))
    return a == b
