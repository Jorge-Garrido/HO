"""Normalización de valores antes de comparar."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

STOPWORDS = {
    "A", "AL", "CON", "DE", "DEL", "EL", "EN", "LA", "LAS", "LOS", "O", "POR", "PARA", "SIN", "U", "UN", "UNA", "Y",
}


def sin_tildes(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def texto(valor: str | None) -> str | None:
    """Mayúsculas, sin tildes, espacios colapsados (incluye NBSP y tabuladores)."""
    if valor is None:
        return None
    limpio = sin_tildes(str(valor)).upper()
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return limpio or None


def codigo(valor: object) -> str | None:
    """Código (CUPS, CUM, institucional) como texto: nunca como número."""
    if valor is None:
        return None
    s = str(valor).replace("\xa0", " ").strip().upper()
    s = re.sub(r"\s+", "", s)
    return s or None


def cups(valor: object) -> str | None:
    """CUPS numérico de 5 dígitos que perdió el cero inicial al pasar por Excel -> 6 dígitos."""
    s = codigo(valor)
    if s and re.fullmatch(r"\d{5}", s) and isinstance(valor, int):
        return s.zfill(6)
    return s


def nit(valor: str | None) -> str | None:
    """Quita puntos, espacios y el dígito de verificación (lo que va después del guion)."""
    if not valor:
        return None
    s = valor.replace(".", "").replace(" ", "").strip()
    s = s.split("-")[0]
    return s if s.isdigit() else (re.sub(r"\D", "", s) or None)


def documento_id(tipo: str | None, numero: str | None) -> str | None:
    """'CC', ' 10.000.001 ' -> 'CC 10000001'."""
    if not numero:
        return None
    num = re.sub(r"[\s.\-]", "", str(numero)).upper()
    t = (tipo or "").strip().upper()
    return f"{t} {num}" if t else num


def documento_id_texto(valor: str | None) -> str | None:
    """'CC  10000001' -> 'CC 10000001'."""
    if not valor:
        return None
    m = re.match(r"\s*([A-Za-z]{2,3})\s+([0-9A-Za-z.\-]+)", valor)
    if m:
        return documento_id(m.group(1), m.group(2))
    return documento_id(None, valor)


def nombre(valor: str | None) -> str | None:
    """Nombre sin paréntesis finales tipo '(a)', en mayúsculas y sin tildes."""
    if not valor:
        return None
    s = re.sub(r"\([^)]*\)", " ", valor)
    return texto(s)


def tokens(valor: str | None, quitar_stopwords: bool = True) -> set[str]:
    if not valor:
        return set()
    t = re.findall(r"[A-Z0-9]+", texto(valor) or "")
    if quitar_stopwords:
        t = [x for x in t if x not in STOPWORDS]
    return set(t)


def raiz(token: str) -> str:
    """Plural simple: PROCEDIMIENTOS -> PROCEDIMIENTO."""
    if len(token) > 4 and token.endswith("ES") and token[-3] not in "AEIOU":
        return token[:-2]
    if len(token) > 4 and token.endswith("S"):
        return token[:-1]
    return token


def dinero(valor: object) -> Decimal | None:
    """Acepta 214000, '214000.00' (XML) y '214.000,00' (PDF)."""
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))
    s = str(valor).strip().replace("$", "").replace(" ", "")
    if not s:
        return None
    # Hay dos plantillas de factura: '214.000,00' y '79,000.00'. El último separador es el decimal.
    if "," in s and "." in s:
        decimal, miles = (",", ".") if s.rfind(",") > s.rfind(".") else (".", ",")
        s = s.replace(miles, "").replace(decimal, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        partes = s.split(sep)
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            s = "".join(partes)  # solo separador de miles: 214.000
        else:
            s = s.replace(sep, ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def dinero_texto(valor: Decimal | None) -> str | None:
    if valor is None:
        return None
    return str(valor.quantize(Decimal("0.01")))


def redondear_centena(valor: Decimal) -> Decimal:
    """Igual que ROUND(x, -2) de Excel: los empates van hacia arriba (181650 -> 181700)."""
    return (valor / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 100


def fecha(valor: str | None) -> date | None:
    """Acepta 2026-07-31, 2026-07-31 10:18, 31/07/2026 y 31/07/2026 11:45."""
    if not valor:
        return None
    s = valor.strip()
    for fmt, largo in (("%Y-%m-%d", 10), ("%d/%m/%Y", 10)):
        try:
            return datetime.strptime(s[:largo], fmt).date()
        except ValueError:
            continue
    return None
