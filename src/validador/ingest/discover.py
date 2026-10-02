"""Encuentra las facturas y clasifica sus archivos, en cualquier estructura de carpetas.

Nomenclatura de la Res. 2284 (la que usa NUEVA EPS): ``<PREFIJO>_<NIT>_<FACTURA>.pdf``
(FEV factura, HEV historia clínica, PDE autorización, OPF orden, PDX apoyo diagnóstico...),
``<FACTURA>.xml``, ``<FACTURA>.json`` (RIPS) y ``<FACTURA>_CUV.json``.

En la práctica cada EPS y cada mes llega distinto: una carpeta por factura; el RIPS en una
carpeta hermana (``RIPS/``); el CUV como ``CUV_<FACTURA>.json``; soportes sin prefijo
(``PDE.pdf``, ``HISTORIA.pdf``, ``ORDEN N.pdf``); paquetes ``.zip`` por factura o por lote.
``descubrir`` no supone ninguna estructura: agrupa todos los archivos de un árbol por el
número de factura (en el nombre del archivo o de la carpeta que lo contiene), descomprime los
zip en una caché y elimina copias idénticas. Los alias de nombres viven en la configuración.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..base.config import Config

# Valores por defecto (los de Config) para los usos que no pasan configuración.
_CFG = Config()

# Tipos de archivo que deben ser únicos por factura: si hay dos con contenido distinto, se avisa.
TIPOS_UNICOS = ("xml", "rips", "cuv")


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
    # Todos los archivos de la factura (para copiarlos a revisión) y avisos del descubrimiento.
    archivos: list[Path] = field(default_factory=list)
    conflictos: list[str] = field(default_factory=list)

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


@dataclass
class Descubrimiento:
    facturas: list[CarpetaFactura]
    # Archivos que no se pudieron asociar a una factura o leer (rar, zip dañado...).
    omitidos: list[str] = field(default_factory=list)


# --- clasificación de un archivo -----------------------------------------------------------------


def ignorado(nombre: str, cfg: Config = _CFG) -> bool:
    n = nombre.lower()
    return any(fnmatch.fnmatch(n, p.lower()) for p in cfg.ignorar_archivos)


def tipo_archivo(nombre: str, cfg: Config = _CFG) -> tuple[str, str | None]:
    """("xml" | "rips" | "cuv" | "pdf" | "otro", prefijo del soporte si es PDF)."""
    ext = Path(nombre).suffix.lower()
    stem = Path(nombre).stem.upper().strip(" .}")
    if ext == ".xml":
        return "xml", None
    if ext == ".json":
        return ("cuv", None) if "CUV" in stem else ("rips", None)
    if ext != ".pdf":
        return "otro", None
    if m := re.match(r"([A-Z]{3})_", stem):
        prefijo = m.group(1)
        return "pdf", cfg.alias_soportes.get(prefijo, prefijo)
    if re.search(cfg.patron_factura, stem):
        return "pdf", "UNI"  # PDF con el nombre de la factura: puede traer todos los soportes juntos
    palabra = re.split(r"[\s._\-]+", stem)[0]
    if palabra in cfg.alias_soportes:
        return "pdf", cfg.alias_soportes[palabra]
    if re.fullmatch(r"[A-Z]{3}", palabra):
        return "pdf", palabra  # PDE.pdf, OPF 2.pdf, HEV.pdf
    return "pdf", palabra or "UNI"


def _agregar(cf: CarpetaFactura, archivo: Path, cfg: Config) -> None:
    tipo, prefijo = tipo_archivo(archivo.name, cfg)
    cf.archivos.append(archivo)
    if tipo in TIPOS_UNICOS:
        actual = getattr(cf, tipo)
        if actual is None:
            setattr(cf, tipo, archivo)
        elif not _mismo_contenido(actual, archivo):
            cf.conflictos.append(f"{tipo.upper()}: versiones distintas ({actual.name} y {archivo.name}); se usó {actual.name}")
    elif tipo == "pdf":
        lista = cf.pdfs.setdefault(prefijo, [])
        if not any(_mismo_contenido(p, archivo) for p in lista):
            if prefijo == "FEV" and lista:
                cf.conflictos.append(f"FEV: versiones distintas ({lista[0].name} y {archivo.name}); se usó {lista[0].name}")
            lista.append(archivo)
    else:
        cf.otros.append(archivo)


def _mismo_contenido(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return _sha(a) == _sha(b)
    except OSError:
        return False


def _sha(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def clasificar(carpeta: Path, cfg: Config = _CFG) -> CarpetaFactura:
    """Una carpeta que contiene los archivos de una sola factura."""
    cf = CarpetaFactura(ruta=carpeta, factura=carpeta.name)
    for archivo in sorted(p for p in carpeta.iterdir() if p.is_file()):
        if not ignorado(archivo.name, cfg):
            _agregar(cf, archivo, cfg)
    return cf


def carpetas_de_facturas(raiz: Path) -> list[Path]:
    """La raíz puede ser la carpeta de una factura o una carpeta que contiene varias."""
    if any(p.suffix.lower() in (".xml", ".json", ".pdf") for p in raiz.iterdir() if p.is_file()):
        return [raiz]
    return sorted(p for p in raiz.iterdir() if p.is_dir())


# --- descubrimiento en un árbol cualquiera ------------------------------------------------------------


def _descomprimir(zip_ruta: Path, cache: Path, omitidos: list[str]) -> Path | None:
    """Descomprime una vez (por ruta, tamaño y fecha) y devuelve la carpeta. Nunca escribe en el origen."""
    st = zip_ruta.stat()
    clave = hashlib.sha1(f"{zip_ruta.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    destino = cache / clave
    marca = destino / ".completo"
    if marca.exists():
        return destino
    if destino.exists():
        shutil.rmtree(destino)  # extracción anterior interrumpida
    try:
        with zipfile.ZipFile(zip_ruta) as z:
            raiz = destino.resolve()
            for miembro in z.infolist():
                final = (destino / miembro.filename).resolve()
                if not final.is_relative_to(raiz):  # zip slip
                    omitidos.append(f"{zip_ruta.name}: ruta insegura {miembro.filename}")
                    continue
                z.extract(miembro, destino)
    except (zipfile.BadZipFile, OSError) as e:
        omitidos.append(f"{zip_ruta}: no se pudo descomprimir ({type(e).__name__})")
        return None
    marca.write_text(str(zip_ruta), encoding="utf-8")
    return destino


def _recorrer(carpeta: Path, cache: Path, cfg: Config, omitidos: list[str], profundidad_zip: int = 0, solo_sueltos: bool = False):
    """(archivo, carpetas que lo contienen desde la raíz) para todo archivo útil, entrando a los zip."""
    for actual, dirs, archivos in os.walk(carpeta):
        if solo_sueltos:
            dirs.clear()
        dirs.sort()
        base = Path(actual)
        for nombre in sorted(archivos):
            ruta = base / nombre
            if ignorado(nombre, cfg):
                continue
            ext = ruta.suffix.lower()
            if ext == ".zip" and profundidad_zip < 3:
                if (extraido := _descomprimir(ruta, cache, omitidos)) is not None:
                    for archivo, contexto in _recorrer(extraido, cache, cfg, omitidos, profundidad_zip + 1):
                        yield archivo, [ruta.stem] + contexto
            elif ext in (".rar", ".7z"):
                omitidos.append(f"{ruta}: formato {ext} no soportado (descomprimir a mano)")
            else:
                yield ruta, list(ruta.relative_to(carpeta).parent.parts)


# Número de factura en el nombre de un XML o RIPS: prefijo DIAN (letras) + consecutivo.
# '900012660_FEAV57789', 'CUV_FCOB123', 'FE4417197_CUV' -> FEAV, FCOB, FE.
_RE_PREFIJO = re.compile(r"(?:^|_)([A-Z]{2,6})(\d{3,})(?:_|$)")
# Palabras que aparecen junto al número y no son prefijos de factura.
_NO_PREFIJOS = {"CUV", "JSON", "RIPS", "XML"}


def patron_factura(archivos: list[tuple[Path, list[str]]], cfg: Config = _CFG) -> str:
    """Patrón del número de factura: el de la configuración más los prefijos DIAN que aparecen en
    los nombres de los XML y RIPS del árbol. Cada IPS tiene su propia numeración (FE, FEAV, FCOB…):
    así una IPS nueva no necesita configuración."""
    prefijos = set()
    for archivo, _ in archivos:
        if archivo.suffix.lower() in (".xml", ".json"):
            for m in _RE_PREFIJO.finditer(archivo.stem.upper()):
                if m.group(1) not in _NO_PREFIJOS:
                    prefijos.add(m.group(1))
    if not prefijos:
        return cfg.patron_factura
    aprendido = "(?:" + "|".join(sorted(prefijos, key=len, reverse=True)) + r")\d{3,}"
    return f"{cfg.patron_factura}|{aprendido}"


def descubrir(raiz: Path, cfg: Config = _CFG, solo_sueltos: bool = False) -> Descubrimiento:
    """Agrupa todos los archivos bajo ``raiz`` por número de factura.

    El número sale del nombre del archivo; si no lo tiene (``PDE.pdf``), de la carpeta más
    cercana que lo tenga. Archivos sin número de factura se reportan como omitidos.
    Con ``solo_sueltos`` solo se miran los archivos directamente en ``raiz`` (y sus zip).
    """
    cache = Path(cfg.cache_descomprimidos)
    omitidos: list[str] = []
    grupos: dict[str, CarpetaFactura] = {}
    archivos = list(_recorrer(raiz, cache, cfg, omitidos, solo_sueltos=solo_sueltos))
    patron = re.compile(patron_factura(archivos, cfg), re.I)
    for archivo, contexto in archivos:
        m = patron.search(archivo.stem)
        if m is None:
            m = next((x for x in (patron.search(c) for c in reversed(contexto)) if x), None)
        if m is None:
            omitidos.append(f"{archivo}: sin número de factura")
            continue
        factura = m.group(0).upper()
        cf = grupos.setdefault(factura, CarpetaFactura(ruta=archivo.parent, factura=factura))
        _agregar(cf, archivo, cfg)
    for cf in grupos.values():
        # La carpeta de la factura es la que tiene más de sus archivos (para mostrar y copiar).
        carpetas = [a.parent for a in cf.archivos]
        cf.ruta = max(set(carpetas), key=carpetas.count)
    return Descubrimiento(sorted(grupos.values(), key=lambda c: c.factura), omitidos)
