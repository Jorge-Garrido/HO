"""Texto digital con PyMuPDF: líneas con posición y detección de páginas escaneadas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ..data_preprocessing import normalize

# Una página "sin texto útil": poco texto y una imagen que cubre casi toda la página.
MIN_CARACTERES = 50
MIN_COBERTURA_IMAGEN = 0.5


@dataclass
class Linea:
    texto: str
    bbox: tuple[float, float, float, float]

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]


@dataclass
class Pagina:
    numero: int
    texto: str
    lineas: list[Linea]
    cobertura_imagen: float
    sin_texto: bool


@dataclass
class PdfTexto:
    paginas: list[Pagina] = field(default_factory=list)

    @property
    def paginas_sin_texto(self) -> list[int]:
        return [p.numero for p in self.paginas if p.sin_texto]

    @property
    def todo_escaneado(self) -> bool:
        return bool(self.paginas) and all(p.sin_texto for p in self.paginas)

    @property
    def texto(self) -> str:
        return "\n".join(p.texto for p in self.paginas)


def leer(ruta: Path) -> PdfTexto:
    resultado = PdfTexto()
    with pymupdf.open(ruta) as doc:
        for pagina in doc:
            area = pagina.rect.width * pagina.rect.height or 1
            cobertura = 0.0
            for img in pagina.get_image_info():
                x0, y0, x1, y1 = img["bbox"]
                cobertura = max(cobertura, (x1 - x0) * (y1 - y0) / area)
            lineas = []
            for bloque in pagina.get_text("dict")["blocks"]:
                for ln in bloque.get("lines", []):
                    t = "".join(s["text"] for s in ln["spans"])
                    if t.strip():
                        lineas.append(Linea(t, tuple(ln["bbox"])))
            texto = pagina.get_text()
            resultado.paginas.append(
                Pagina(
                    numero=pagina.number + 1,
                    texto=texto,
                    lineas=lineas,
                    cobertura_imagen=cobertura,
                    sin_texto=len(texto.strip()) < MIN_CARACTERES and cobertura >= MIN_COBERTURA_IMAGEN,
                )
            )
    return resultado


def _misma_fila(a: Linea, b: Linea) -> bool:
    arriba = max(a.bbox[1], b.bbox[1])
    abajo = min(a.bbox[3], b.bbox[3])
    alto = min(a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1]) or 1
    return (abajo - arriba) / alto >= 0.5


def valor_a_la_derecha(pagina: Pagina, etiqueta: str, max_y: float | None = None) -> Linea | None:
    """Busca la línea cuyo texto es exactamente ``etiqueta`` y devuelve la más cercana a su derecha.

    En la historia clínica y en el encabezado de la autorización el valor está en la
    misma fila, a la derecha de la etiqueta (el orden del texto plano no sirve).
    """
    objetivo = normalize.texto(etiqueta)
    for lab in pagina.lineas:
        if max_y is not None and lab.y0 > max_y:
            continue
        if normalize.texto(lab.texto) != objetivo:
            continue
        candidatos = [l for l in pagina.lineas if l is not lab and l.x0 >= lab.bbox[2] - 1 and _misma_fila(l, lab)]
        if candidatos:
            return min(candidatos, key=lambda l: l.x0)
    return None


def buscar(patron: str, texto: str, flags: int = 0) -> re.Match | None:
    return re.search(patron, texto, flags)
