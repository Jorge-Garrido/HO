"""Descubrimiento en seco (sin validar): cuántas facturas y qué soportes encuentra por lote.

    python scripts/descubrir_lotes.py ~/datos_disco_d/FOMAG/*
"""
import sys, time, collections
from pathlib import Path
from validador.base.config import get_config
from validador.ingest import discover
cfg = get_config()
for lote in map(Path, sys.argv[1:]):
    t = time.time()
    h = discover.descubrir(lote, cfg)
    comp = collections.Counter()
    for cf in h.facturas:
        for k, v in (("xml", cf.xml), ("rips", cf.rips), ("cuv", cf.cuv)): comp[k] += bool(v)
        for k in ("FEV", "UNI", "HEV", "PDE", "OPF"): comp[k] += bool(cf.pdf(k))
        comp["con_conflicto"] += bool(cf.conflictos)
    otros = collections.Counter(k for cf in h.facturas for k in cf.pdfs if k not in ("FEV", "UNI", "HEV", "PDE", "OPF"))
    omit = collections.Counter(o.split(": ")[-1] for o in h.omitidos)
    conf = collections.Counter(c.split(":")[0] for cf in h.facturas for c in cf.conflictos)
    print(f"== {lote.name}: {len(h.facturas)} facturas ({time.time()-t:.0f}s)\n   {dict(comp)}\n   otros soportes: {dict(otros.most_common(12))}\n   omitidos: {dict(omit.most_common(6))}\n   conflictos: {dict(conf)}")
    ej = [o for o in h.omitidos][:4]
    if ej: print("   ej. omitidos:", *ej, sep="\n     ")
