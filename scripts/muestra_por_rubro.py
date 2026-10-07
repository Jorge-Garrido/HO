"""Muestra de N facturas por rubro de error (aviso/grave) para revisión humana o auditoría.

Rubro = tipo de discrepancia: campo comparado, línea de servicio, hallazgo o regla normativa.
Por rubro toma primero los *grave* y reparte entre EPS. Copia los documentos originales de cada
factura, su JSON de resultado y un _REVISION.txt en lenguaje claro.

    python scripts/muestra_por_rubro.py salidas/disco_d2/v5 ~/muestra_errores --n 5

La salida contiene datos de pacientes: debe quedar fuera del repositorio.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

from validador.base.schemas import ResultadoFactura
from validador.salida_errores import discrepancias, texto_revision

ORDEN = {"grave": 0, "aviso": 1}


def rubros(r: dict) -> dict[str, tuple[str, str]]:
    """{rubro: (severidad más alta, motivo)} de una factura."""
    out: dict[str, tuple[str, str]] = {}

    def poner(rubro: str, sev: str, motivo: str) -> None:
        if sev in ORDEN and (rubro not in out or ORDEN[sev] < ORDEN[out[rubro][0]]):
            out[rubro] = (sev, motivo)

    for c in r["campos"]:
        rubro = f"campo_{c['campo']}"
        if c["campo"] == "numero_autorizacion":
            ocr = "autorizacion_scan" in (c.get("documento_sospechoso") or "") or "documento de la EPS dice" in (c["motivo"] or "")
            rubro += "_vs_escaneo" if ocr else "_digital"
        poner(rubro, c["resultado"], c["motivo"] or "")
    for l in r["lineas"]:
        motivo = "; ".join(l["motivos"])
        poner("linea_codigo_distinto_rips_factura" if "código distinto" in motivo else "linea_otras_diferencias", l["resultado"], motivo)
    for h in r["hallazgos"]:
        poner(f"norma_{h['regla']['id']}" if h.get("regla") else f"hallazgo_{h['tipo']}", h["resultado"], h["motivo"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("corrida", help="carpeta con lotes 2026-*/facturas/*.json")
    ap.add_argument("destino")
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()

    candidatos: dict[str, list[tuple[str, str, dict, str]]] = defaultdict(list)
    for d in sorted(glob.glob(a.corrida + "/2026-*")):
        eps = json.load(open(d + "/resumen.json"))["lote"]["nombre"].split(" - ")[0]
        for f in sorted(glob.glob(d + "/facturas/*.json")):
            r = json.load(open(f, encoding="utf-8"))
            for rubro, (sev, motivo) in rubros(r).items():
                candidatos[rubro].append((sev, eps, r, motivo))

    destino = Path(a.destino).expanduser()
    destino.mkdir(parents=True, exist_ok=False)
    indice = []
    for rubro, lista in sorted(candidatos.items()):
        # graves primero; dentro de cada severidad, turnando entre EPS
        por_eps: dict[tuple[str, str], list] = defaultdict(list)
        for x in lista:
            por_eps[(x[0], x[1])].append(x)
        elegidos = []
        for sev in ORDEN:
            colas = [v for (s, _), v in sorted(por_eps.items()) if s == sev]
            while colas and len(elegidos) < a.n:
                for q in list(colas):
                    if len(elegidos) < a.n and q:
                        elegidos.append(q.pop(0))
                    if not q:
                        colas.remove(q)
        for sev, eps, r, motivo in elegidos:
            carpeta = destino / rubro / f"{eps}_{r['factura']}"
            (carpeta / "documentos").mkdir(parents=True)
            vistos = set()
            for p in r["archivos_origen"]:
                p = Path(p)
                if p.name not in vistos and p.exists():
                    vistos.add(p.name)
                    shutil.copy2(p, carpeta / "documentos" / p.name)
            res = ResultadoFactura.model_validate(r)
            (carpeta / "_REVISION.txt").write_text(texto_revision(res, discrepancias(res)), encoding="utf-8")
            (carpeta / "resultado.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
            indice.append({"rubro": rubro, "severidad": sev, "eps": eps, "factura": r["factura"],
                           "estado_factura": r["estado"], "motivo": re.sub(r"\s+", " ", motivo),
                           "carpeta": f"{rubro}/{carpeta.name}", "total_en_corrida": len(lista)})

    with open(destino / "INDICE.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(indice[0]))
        w.writeheader()
        w.writerows(indice)
    print(f"{len(indice)} casos en {len(candidatos)} rubros → {destino}")
    for rubro, lista in sorted(candidatos.items()):
        print(f"  {len(lista):5}  {rubro}")


if __name__ == "__main__":
    main()
