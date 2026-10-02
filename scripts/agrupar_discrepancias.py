"""Agrupa las discrepancias (aviso/grave) de una o varias corridas por tipo y motivo.

    python scripts/agrupar_discrepancias.py salidas/X/2026-*
"""
import json, glob, collections, sys
c = collections.Counter(); fact = collections.defaultdict(set); total = 0; estados = collections.Counter()
for corrida in sys.argv[1:]:
    for f in glob.glob(corrida + "/facturas/*.json"):
        r = json.load(open(f)); total += 1; estados[r["estado"]] += 1
        for x in r["campos"]:
            if x["resultado"] in ("aviso", "grave"):
                k = f"campo {x['campo']} {x['resultado']}: sosp={x['documento_sospechoso']} fuentes={','.join(sorted(x['valores_por_fuente']))}"
                c[k] += 1; fact[k].add(r["factura"])
        for l in r["lineas"]:
            if l["resultado"] in ("aviso", "grave"):
                for m in l["motivos"]:
                    k = f"línea {l['resultado']}: {m.split('(')[0][:70]}"; c[k] += 1; fact[k].add(r["factura"])
        for h in r["hallazgos"]:
            if h["resultado"] in ("aviso", "grave"):
                k = f"hallazgo {h['tipo']} {h['resultado']}: " + (h["regla"]["id"] if h.get("regla") else h["motivo"][:75])
                c[k] += 1; fact[k].add(r["factura"])
print(total, "facturas", dict(estados))
for k, n in c.most_common(30): print(f"{len(fact[k]):5} fact  {k}")
