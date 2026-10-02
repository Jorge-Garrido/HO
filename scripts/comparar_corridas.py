"""Compara dos corridas (carpetas con facturas/*.json) para verificar un cambio del validador.

Separa dos cosas:
1. Lo que ya existía (campos, líneas, hallazgos sin regla, estado sin reglas): debe ser idéntico
   salvo que el cambio lo pretenda. Cualquier diferencia se lista.
2. Lo nuevo del motor de reglas (hallazgos con ``regla``): se lista aparte, con el cambio de
   estado de la factura que produce.

Uso:
    python scripts/comparar_corridas.py salidas/linea_base/<corrida> salidas/con_reglas/<corrida> [--json informe.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ORDEN = {"ok": 0, "info": 1, "aviso": 2, "grave": 3}
# Cambian en cada corrida y no son resultado de la validación.
VOLATILES = {"corrida_id", "ruta_origen", "archivos_origen", "tiempos_ms", "schema_version", "version_pipeline", "reglas"}


def cargar(carpeta: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((carpeta / "facturas").glob("*.json"))}


def sin_reglas(r: dict) -> dict:
    """El resultado como lo habría dado el validador sin motor de reglas."""
    previo = {k: v for k, v in r.items() if k not in VOLATILES}
    previo["hallazgos"] = [{k: v for k, v in h.items() if k != "regla"} for h in r.get("hallazgos", []) if not h.get("regla")]
    previo["propuestas_correccion"] = r.get("propuestas_correccion", [])
    severidades = [c["resultado"] for c in r["campos"]] + [l["resultado"] for l in r["lineas"]] + [h["resultado"] for h in previo["hallazgos"]]
    previo["estado"] = max(severidades, key=ORDEN.get, default="ok")
    return previo


def diferencias(a: dict, b: dict, ruta: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        salida = []
        for k in sorted(set(a) | set(b)):
            salida += diferencias(a.get(k), b.get(k), f"{ruta}.{k}" if ruta else k)
        return salida
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diferencias(x, y, f"{ruta}[{i}]")]
    return [] if a == b else [f"{ruta}: {json.dumps(a, ensure_ascii=False)[:120]} -> {json.dumps(b, ensure_ascii=False)[:120]}"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base", type=Path)
    p.add_argument("nueva", type=Path)
    p.add_argument("--json", type=Path, help="guarda el informe completo")
    a = p.parse_args()

    base, nueva = cargar(a.base), cargar(a.nueva)
    solo_base, solo_nueva = sorted(set(base) - set(nueva)), sorted(set(nueva) - set(base))
    comunes = sorted(set(base) & set(nueva))
    informe: dict = {"base": str(a.base), "nueva": str(a.nueva), "facturas_comunes": len(comunes),
                     "solo_en_base": solo_base, "solo_en_nueva": solo_nueva, "regresiones": {}, "hallazgos_nuevos": [],
                     "cambios_de_estado": []}
    por_regla: Counter[str] = Counter()
    for f in comunes:
        b, n = base[f], nueva[f]
        if difs := diferencias(sin_reglas(b), sin_reglas(n)):
            informe["regresiones"][f] = difs
        for h in n.get("hallazgos", []):
            if h.get("regla"):
                por_regla[h["regla"]["id"]] += 1
                informe["hallazgos_nuevos"].append({"factura": f, "regla": h["regla"]["id"], "glosa": h["regla"].get("codigo_glosa"),
                                                    "severidad": h["resultado"], "motivo": h["motivo"]})
        if b["estado"] != n["estado"]:
            informe["cambios_de_estado"].append({"factura": f, "antes": b["estado"], "despues": n["estado"]})

    print(f"Facturas comparadas: {len(comunes)}  (solo en base: {len(solo_base)}, solo en nueva: {len(solo_nueva)})")
    print(f"\n1) Resultado previo (sin reglas) idéntico: {len(comunes) - len(informe['regresiones'])}/{len(comunes)}")
    for f, difs in informe["regresiones"].items():
        print(f"   DIFERENCIA {f}:")
        for d in difs[:10]:
            print(f"      {d}")
    print(f"\n2) Hallazgos nuevos del motor de reglas: {len(informe['hallazgos_nuevos'])}  {dict(por_regla)}")
    for h in informe["hallazgos_nuevos"]:
        print(f"   {h['factura']}  {h['severidad'].upper():5}  {h['regla']:24} {h['glosa'] or '':7} {h['motivo'][:110]}")
    print(f"\n3) Facturas que cambian de estado: {len(informe['cambios_de_estado'])}  "
          f"{dict(Counter(f'{c['antes']}→{c['despues']}' for c in informe['cambios_de_estado']))}")
    if a.json:
        a.json.write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nInforme: {a.json}")
    return 1 if informe["regresiones"] or solo_base or solo_nueva else 0


if __name__ == "__main__":
    sys.exit(main())
