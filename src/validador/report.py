"""Salidas de una corrida: JSON por factura, resumen para indicadores y listas descargables."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .base.schemas import ResultadoFactura


def escribir_factura(r: ResultadoFactura, carpeta: Path) -> Path:
    destino = carpeta / "facturas" / f"{r.factura}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(r.model_dump_json(indent=2), encoding="utf-8")
    return destino


def resumen(corrida_id: str, resultados: list[ResultadoFactura]) -> dict:
    por_estado = Counter(r.estado for r in resultados)
    sospechosos: Counter[str] = Counter()
    campos_problema: Counter[str] = Counter()
    codigos: Counter[str] = Counter()
    tarifa: Counter[str] = Counter()
    hallazgos: Counter[str] = Counter()
    for r in resultados:
        for c in r.campos:
            if c.resultado in ("aviso", "grave"):
                campos_problema[f"{c.campo}:{c.resultado}"] += 1
                for d in (c.documento_sospechoso or "").split(","):
                    if d:
                        sospechosos[d] += 1
        for l in r.lineas:
            if l.tarifa:
                tarifa[l.tarifa.estado] += 1
            if l.resultado == "grave":
                campos_problema["lineas:grave"] += 1
        # Una vez por factura (FAJ43b, RUT01, FED137...).
        for cod in set(r.dian.get("avisos") or []):
            codigos[f"dian:{cod}"] += 1
        for cod in set(r.cuv.get("codigos") or []):
            codigos[f"cuv:{cod}"] += 1
        for h in r.hallazgos:
            if h.resultado in ("aviso", "grave"):
                hallazgos[f"{h.tipo}:{h.resultado}"] += 1
    return {
        "corrida_id": corrida_id,
        "facturas": len(resultados),
        "por_estado": dict(por_estado),
        "por_fuente_principal": dict(Counter(r.fuente_principal for r in resultados)),
        "por_documento_sospechoso": dict(sospechosos.most_common()),
        "campos_con_problema": dict(campos_problema.most_common()),
        "hallazgos_con_problema": dict(hallazgos.most_common()),
        "codigos_dian_cuv_por_factura": dict(codigos.most_common()),
        "tarifa_por_estado": dict(tarifa.most_common()),
        "requiere_ocr": dict(Counter(f for r in resultados for f in r.requiere_ocr)),
        "tiempo_promedio_ms": round(sum(r.tiempos_ms.get("total", 0) for r in resultados) / len(resultados)) if resultados else 0,
    }


def escribir_casos(resultados: list[ResultadoFactura], destino: Path) -> None:
    """Lista descargable: una fila por problema (aviso o grave). Sin valores personales."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["factura", "estado_factura", "tipo", "elemento", "resultado", "documento_sospechoso", "motivo"])
        for r in resultados:
            if r.estado not in ("aviso", "grave"):
                continue
            for c in r.campos:
                if c.resultado in ("aviso", "grave"):
                    w.writerow([r.factura, r.estado, "campo", c.campo, c.resultado, c.documento_sospechoso or "", c.motivo or ""])
            for l in r.lineas:
                if l.resultado in ("aviso", "grave"):
                    w.writerow([r.factura, r.estado, "linea", l.codigo, l.resultado, l.documento_sospechoso or "", "; ".join(l.motivos)])
            for h in r.hallazgos:
                if h.resultado in ("aviso", "grave"):
                    w.writerow([r.factura, r.estado, h.tipo, "", h.resultado, h.documento_sospechoso or "", h.motivo])


def escribir_propuestas(resultados: list[ResultadoFactura], destino: Path) -> None:
    datos = [
        {"factura": r.factura, **p.model_dump(mode="json")} for r in resultados for p in r.propuestas_correccion
    ]
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")

