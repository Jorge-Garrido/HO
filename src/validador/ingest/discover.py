"""Clasifica los archivos de la carpeta de una factura.

Nomenclatura observada: ``<PREFIJO>_<NIT>_<FACTURA>.pdf`` (FEV factura, HEV historia
clínica, PDE autorización, PDX apoyo diagnóstico, CRC, RAN...), ``<FACTURA>.xml``,
``<FACTURA>.json`` (RIPS) y ``<FACTURA>_CUV.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Prefijos equivalentes entre EPS: COOSALUD usa FVS para la factura (NUEVA EPS usa FEV).
ALIAS_PREFIJOS = {"FVS": "FEV"}
# Metadatos de Windows copiados junto a los archivos ("archivo.pdf:Zone.Identifier").
IGNORAR = (":Zone.Identifier",)


@dataclass
class CarpetaFactura:
    ruta: Path
    factura: str
    xml: Path | None = None
    rips: Path | None = None
    cuv: Path | None = None
    pdfs: dict[str, list[Path]] = field(default_factory=dict)
    otros: list[Path] = field(default_factory=list)
    # Documentos que salieron de un PDF unificado: prefijo -> "FE123.pdf, págs. 1-2"
    etiquetas: dict[str, str] = field(default_factory=dict)

    def pdf(self, prefijo: str) -> Path | None:
        lista = self.pdfs.get(prefijo)
        return lista[0] if lista else None

    def presentes(self) -> dict[str, list[str]]:
        docs: dict[str, list[str]] = {}
        for clave, ruta in (("XML", self.xml), ("RIPS", self.rips), ("CUV", self.cuv)):
            if ruta:
                docs[clave] = [ruta.name]
        for prefijo, rutas in sorted(self.pdfs.items()):
            docs[prefijo] = [self.etiquetas[prefijo]] if prefijo in self.etiquetas else [r.name for r in rutas]
        return docs


def clasificar(carpeta: Path) -> CarpetaFactura:
    cf = CarpetaFactura(ruta=carpeta, factura=carpeta.name)
    for archivo in sorted(p for p in carpeta.iterdir() if p.is_file()):
        nombre = archivo.name
        if nombre.endswith(IGNORAR):
            continue
        ext = archivo.suffix.lower()
        if ext == ".xml":
            cf.xml = archivo
        elif ext == ".json" and nombre.upper().endswith("_CUV.JSON"):
            cf.cuv = archivo
        elif ext == ".json":
            cf.rips = archivo
        elif ext == ".pdf" and (m := re.match(r"([A-Za-z]{3})_", nombre)):
            prefijo = m.group(1).upper()
            cf.pdfs.setdefault(ALIAS_PREFIJOS.get(prefijo, prefijo), []).append(archivo)
        elif ext == ".pdf":
            # PDF sin prefijo: puede traer todos los soportes juntos (se separa por páginas)
            cf.pdfs.setdefault("UNI", []).append(archivo)
        else:
            cf.otros.append(archivo)
    return cf


def carpetas_de_facturas(raiz: Path) -> list[Path]:
    """La raíz puede ser la carpeta de una factura o una carpeta que contiene varias."""
    if any(p.suffix.lower() in (".xml", ".json", ".pdf") for p in raiz.iterdir() if p.is_file()):
        return [raiz]
    return sorted(p for p in raiz.iterdir() if p.is_dir())
