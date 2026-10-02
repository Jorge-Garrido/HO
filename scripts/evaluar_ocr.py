"""OCR de las autorizaciones escaneadas (PDE) + verificación de los valores que ya
confirmaron las fuentes digitales. Mide cuántas se concilian solo con OCR.

Por cada autorización:
  - número de autorización: se verifica el valor del XML y el del RIPS
  - documento del paciente
  - cada CUPS facturado (RIPS)

El OCR se guarda en caché (salidas/ocr_cache, fuera del repositorio) para poder
reevaluar sin volver a leer. Solo imprime conteos agregados.

    .venv/bin/python scripts/evaluar_ocr.py "nuevaeps/NUEVA EPS EXITOSA" [-v]
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from validador import localizar
from validador.ingest import discover, rips, xml_parser
from validador.ingest.util import sha256
from validador.ocr import a_layout, imagenes, motor

CACHE = Path("salidas/ocr_cache")


def ocr_con_cache(ruta: Path):
    CACHE.mkdir(parents=True, exist_ok=True)
    archivo = CACHE / f"{sha256(ruta)}.json"
    imgs = imagenes.extraer(ruta)
    if archivo.exists():
        datos = json.loads(archivo.read_text())
        res = [motor.ResultadoOCR([motor.LineaOCR(**l) for l in d["lineas"]], d["rotacion"], d["ancho"], d["alto"], d["ms"]) for d in datos]
    else:
        res = [motor.leer(i.imagen) for i in imgs]
        archivo.write_text(json.dumps([
            {"lineas": [l.__dict__ for l in r.lineas], "rotacion": r.rotacion, "ancho": r.ancho, "alto": r.alto, "ms": r.ms}
            for r in res
        ]))
    return list(zip(imgs, res))


def main(raiz: Path) -> None:
    c: dict[str, Counter] = defaultdict(Counter)
    detalle: list[str] = []
    ms_pagina = []
    t0 = time.perf_counter()
    for carpeta in discover.carpetas_de_facturas(raiz):
        cf = discover.clasificar(carpeta)
        pde = cf.pdf("PDE")
        if not pde or not cf.xml:
            continue
        pares = ocr_con_cache(pde)
        ms_pagina += [r.ms for _, r in pares if r.ms]
        doc = a_layout.a_layout(pares)
        x, _ = xml_parser.extraer(cf.xml)
        r = rips.extraer(cf.rips) if cf.rips else None

        # Número de autorización: XML y RIPS por separado.
        aut_xml = x.campos.get("numero_autorizacion")
        aut_rips = r.campos.get("numero_autorizacion") if r else None
        for fuente, cand in (("xml", aut_xml), ("rips", aut_rips)):
            if cand and cand.valor:
                for valor in cand.valor.split(","):
                    v = localizar.verificar(doc, "numero_autorizacion", valor)
                    c[f"autorizacion_{fuente}"][v.estado] += 1
                    if v.estado != "confirmado":
                        detalle.append(f"{cf.factura} autorizacion_{fuente} -> {v.estado} (dist {v.distancia})")
        # Documento del paciente
        pid = (r.campos.get("paciente_id") if r else None) or x.campos.get("paciente_id")
        if pid:
            v = localizar.verificar(doc, "paciente_id", pid.valor)
            c["paciente_id"][v.estado] += 1
            if v.estado != "confirmado":
                detalle.append(f"{cf.factura} paciente_id -> {v.estado}")
        # CUPS facturados (numéricos de 6 dígitos; los insumos como S55114 no van en la autorización)
        codigos = {l.codigo for l in (r.lineas if r else x.lineas) if l.codigo and re.fullmatch(r"\d{6}", l.codigo)}
        for cod in sorted(codigos):
            v = localizar.verificar(doc, "cups", cod)
            c["cups"][v.estado] += 1
            if v.estado != "confirmado":
                detalle.append(f"{cf.factura} cups -> {v.estado}")

    print(f"{'campo':20} {'n':>4}  " + "  ".join(f"{e:>14}" for e in ("confirmado", "sin_contexto", "diferencia", "no_encontrado", "otro")))
    for campo, cnt in c.items():
        n = sum(cnt.values())
        otros = n - cnt["confirmado"] - cnt["confirmado_sin_contexto"] - cnt["diferencia"] - cnt["no_encontrado"]
        cols = [cnt["confirmado"], cnt["confirmado_sin_contexto"], cnt["diferencia"], cnt["no_encontrado"], otros]
        print(f"{campo:20} {n:4}  " + "  ".join(f"{x:5d} ({x/n:5.1%})" for x in cols))
    if ms_pagina:
        print(f"\nOCR: {len(ms_pagina)} páginas leídas ahora, {sum(ms_pagina)/len(ms_pagina):.0f} ms/página")
    print(f"tiempo total: {time.perf_counter() - t0:.0f} s")
    if "-v" in sys.argv:
        print("\n".join(detalle))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
