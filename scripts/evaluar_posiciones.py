"""Mide la precisión del localizador genérico (solo posiciones + diccionario de etiquetas)
contra los parsers por plantilla, que ya coinciden al 100 % con XML/RIPS.

Para cada documento y campo:
  - correcto:   verificar(valor real)             -> debe dar 'confirmado'
  - alterado:   verificar(valor con 1 cambio)      -> debe dar 'diferencia' (nunca 'confirmado')
  - inventado:  verificar(valor ajeno, misma forma) -> no debe dar 'confirmado'
  - extracción: extraer(campo) sin valor esperado  -> debe ser igual al valor real

Solo imprime conteos agregados (sin datos personales).

    .venv/bin/python scripts/evaluar_posiciones.py "nuevaeps/NUEVA EPS EXITOSA"
"""

from __future__ import annotations

import random
import sys
import time
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from validador import localizar
from validador.data_preprocessing import layout, normalize
from validador.ingest import autorizacion_header, discover, factura_pdf_parser, hc_parser

rnd = random.Random(7)


def alterar(tipo: str, valor: str) -> str:
    if tipo in ("codigo", "documento"):
        pos = [i for i, c in enumerate(valor) if c.isdigit()]
        i = pos[len(pos) // 2]
        return valor[:i] + str((int(valor[i]) + 3) % 10) + valor[i + 1 :]
    if tipo == "nombre":
        toks = valor.split()
        j = max(range(len(toks)), key=lambda k: len(toks[k]))
        t = toks[j]
        toks[j] = t[:2] + ("X" if t[2] != "X" else "Y") + t[3:]
        return " ".join(toks)
    if tipo == "dinero":
        return str(normalize.dinero(valor) + Decimal(1000))
    return (normalize.fecha(valor) + timedelta(days=1)).isoformat()


def inventar(tipo: str, valor: str) -> str:
    if tipo in ("codigo", "documento"):
        return "".join(str(rnd.randint(0, 9)) if c.isdigit() else c for c in valor)
    if tipo == "nombre":
        return "ZUÑIGA QUINTERO XIMENA"
    if tipo == "dinero":
        return "123456789.00"
    return "1999-01-01"


def igual(tipo: str, a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    if tipo == "nombre":
        return normalize.tokens(a, False) == normalize.tokens(b, False)
    if tipo == "codigo":
        return a.lower() == b.lower()
    return a == b


def main(raiz: Path) -> None:
    fuentes = {
        "FEV": factura_pdf_parser.extraer,
        "HEV": hc_parser.extraer,
        "PDE": autorizacion_header.extraer,
    }
    stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fallos: dict[tuple[str, str], list[str]] = defaultdict(list)
    tiempos = []
    for carpeta in discover.carpetas_de_facturas(raiz):
        cf = discover.clasificar(carpeta)
        for prefijo, extractor in fuentes.items():
            ruta = cf.pdf(prefijo)
            if not ruta:
                continue
            ref = extractor(ruta)
            if not ref.campos:
                continue
            t0 = time.perf_counter()
            doc = layout.desde_pdf(ruta)
            for campo, extraido in ref.campos.items():
                if campo not in localizar.TIPOS:
                    continue
                tipo = localizar.TIPOS[campo]
                real = extraido.valor
                s = stats[(prefijo, campo)]
                s["n"] += 1
                v = localizar.verificar(doc, campo, real)
                s[f"correcto:{v.estado}"] += 1
                if v.estado != "confirmado":
                    fallos[(prefijo, campo)].append(f"{cf.factura} correcto->{v.estado}")
                v = localizar.verificar(doc, campo, alterar(tipo, real))
                s[f"alterado:{v.estado}"] += 1
                if v.estado != "diferencia":
                    fallos[(prefijo, campo)].append(f"{cf.factura} alterado->{v.estado}")
                v = localizar.verificar(doc, campo, inventar(tipo, real))
                s[f"inventado:{v.estado}"] += 1
                e = localizar.extraer(doc, campo)
                ok = igual(tipo, e, real)
                s["extraccion_ok"] += ok
                if not ok:
                    fallos[(prefijo, campo)].append(f"{cf.factura} extraccion->{'vacio' if e is None else 'distinto'}")
            tiempos.append(time.perf_counter() - t0)

    print(f"{'doc':4} {'campo':22} {'n':>4}  {'correcto':>9} {'alterado':>9} {'falsa_conf':>10} {'extraccion':>10}")
    tot = defaultdict(int)
    for (prefijo, campo), s in sorted(stats.items()):
        n = s["n"]
        ok = s["correcto:confirmado"]
        alt = s["alterado:diferencia"]
        falsa = s["alterado:confirmado"] + s["inventado:confirmado"] + s["alterado:confirmado_sin_contexto"] + s["inventado:confirmado_sin_contexto"]
        ext = s["extraccion_ok"]
        for k, v in (("n", n), ("ok", ok), ("alt", alt), ("falsa", falsa), ("ext", ext)):
            tot[k] += v
        print(f"{prefijo:4} {campo:22} {n:4}  {ok/n:9.1%} {alt/n:9.1%} {falsa:10d} {ext/n:10.1%}")
    n = tot["n"]
    print(f"{'':4} {'TOTAL':22} {n:4}  {tot['ok']/n:9.1%} {tot['alt']/n:9.1%} {tot['falsa']:10d} {tot['ext']/n:10.1%}")
    print(f"\ntiempo promedio por documento: {sum(tiempos)/len(tiempos)*1000:.0f} ms")
    if "-v" in sys.argv:
        for k, lista in sorted(fallos.items()):
            print(k, len(lista), lista[:6])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
