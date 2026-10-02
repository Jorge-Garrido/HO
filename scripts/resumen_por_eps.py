"""Resumen por EPS de una corrida --por-lote: estados, graves, avisos, OCR y glosas.

    python scripts/resumen_por_eps.py salidas/disco_d/final
"""
import json, glob, collections
import sys
base = sys.argv[1] if len(sys.argv) > 1 else "salidas/disco_d/final"
por_eps = collections.defaultdict(list); lotes = []
for d in sorted(glob.glob(base + "/2026-*")):
    res = json.load(open(d + "/resumen.json")); eps = res["lote"]["nombre"].split(" - ")[0]
    rs = [json.load(open(f)) for f in glob.glob(d + "/facturas/*.json")]
    por_eps[eps] += rs
    lotes.append((res["lote"]["nombre"], res["facturas"], res["por_estado"], res["lote"].get("facturas_con_error", 0), res["tiempo_promedio_ms"]))
for l in lotes: print(l)
for eps, rs in por_eps.items():
    print(f"\n##### {eps}: {len(rs)} facturas", dict(collections.Counter(r["estado"] for r in rs)))
    ocr = collections.Counter(); g = collections.Counter(); a = collections.Counter(); glosas = collections.Counter()
    for r in rs:
        c = next(x for x in r["campos"] if x["campo"] == "numero_autorizacion"); v = c["valores_por_fuente"]
        ocr["con lectura OCR" if "autorizacion_scan" in v else "sin lectura OCR"] += 1
        if "autorizacion_scan" in v: ocr[f"  {c['resultado']}"] += 1
        items = [(f"campo {x['campo']}", x["resultado"], (x["motivo"] or "")[:60]) for x in r["campos"]] + \
                [(f"línea", l["resultado"], (l["motivos"] or [""])[0][:60]) for l in r["lineas"]] + \
                [((h["regla"]["id"] if h.get("regla") else f"hallazgo {h['tipo']}"), h["resultado"], h["motivo"][:60]) for h in r["hallazgos"]]
        for k, sev, m in items:
            if sev == "grave": g[f"{k}: {m}"] += 1
            if sev == "aviso": a[k] += 1
        for h in r["hallazgos"]:
            if h.get("regla") and h["regla"].get("codigo_glosa") and h["resultado"] != "info": glosas[h["regla"]["codigo_glosa"]] += 1
    print("  OCR autorización:", dict(ocr))
    print("  GRAVES:"); [print(f"    {n:4} {k}") for k, n in g.most_common(12)]
    print("  AVISOS:"); [print(f"    {n:4} {k}") for k, n in a.most_common(10)]
    print("  glosas potenciales:", dict(glosas))
