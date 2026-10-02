"""Cascada de OCR completa sobre las autorizaciones: en qué etapa se resuelve cada valor,
cuánto queda para el siguiente nivel (LLM/humano) y controles negativos (valores
alterados o inventados nunca deben confirmarse). Solo imprime conteos.

    .venv/bin/python scripts/evaluar_cascada.py "nuevaeps/NUEVA EPS EXITOSA" "COOSALUD EXITOSAS"
"""

from __future__ import annotations

import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

from validador.ingest import discover, rips, xml_parser
from validador.ocr import cascada

CACHE = Path("salidas/ocr_cache")
rnd = random.Random(11)


def alterar(v: str) -> str:
    pos = [i for i, c in enumerate(v) if c.isdigit()]
    i = pos[len(pos) // 2]
    return v[:i] + str((int(v[i]) + 3) % 10) + v[i + 1 :]


def main(raices: list[Path]) -> None:
    res: Counter = Counter()
    negativos: Counter = Counter()
    t0 = time.perf_counter()
    for raiz in raices:
        for carpeta in discover.carpetas_de_facturas(raiz):
            cf = discover.clasificar(carpeta)
            if not cf.pdf("PDE") or not cf.xml:
                continue
            doc = cascada.leer_pdf(cf.pdf("PDE"), CACHE)
            x, _ = xml_parser.extraer(cf.xml)
            r = rips.extraer(cf.rips) if cf.rips else None
            valores: list[tuple[str, str]] = []
            auts = {c.valor for d in (x, r) if d for k, c in d.campos.items() if k == "numero_autorizacion"}
            valores += [("numero_autorizacion", a) for a in auts]
            pid = (r.campos.get("paciente_id") if r else None) or x.campos.get("paciente_id")
            if pid:
                valores.append(("paciente_id", pid.valor))
            lineas = r.lineas if r else x.lineas
            valores += [("cups", c) for c in sorted({l.codigo for l in lineas if l.codigo and re.fullmatch(r"\d{6}", l.codigo)})]
            for campo, v in valores:
                out = cascada.resolver(doc, campo, v)
                res[(raiz.name, campo, out.estado, out.etapa or "-")] += 1
                for tipo, val in (("alterado", alterar(v)), ("inventado", "".join(str(rnd.randint(0, 9)) if ch.isdigit() else ch for ch in v))):
                    negativos[(tipo, cascada.resolver(doc, campo, val).estado)] += 1

    print(f"{'EPS':24} {'campo':20} {'estado':12} {'etapa':16} {'n':>4}")
    for k, n in sorted(res.items()):
        print(f"{k[0][:24]:24} {k[1]:20} {k[2]:12} {k[3]:16} {n:4}")
    print("\nControles negativos:", dict(negativos))
    print("CONFIRMACIONES FALSAS:", sum(n for (t, e), n in negativos.items() if e == "confirmado"))
    print(f"tiempo: {time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main([Path(a) for a in sys.argv[1:]])
