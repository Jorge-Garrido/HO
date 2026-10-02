"""Prueba en sombra: evalúa una versión candidata de reglas contra la vigente sobre las mismas
facturas, sin guardar resultados ni cambiar nada en producción.

Cada factura se extrae una sola vez y se evalúa con los dos motores; el informe dice, regla
por regla, cuántas facturas cambian de estado y cuáles. Es el paso obligatorio antes de
aprobar una versión (``store.referencia.cambiar_estado`` lo exige).
"""

from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..base.config import Config
from ..ingest import discover
from .motor import MotorReglas

log = logging.getLogger(__name__)
MAX_EJEMPLOS = 30


def _estados(motor: MotorReglas | None, docs, presentes) -> dict[str, str]:
    if motor is None:
        return {}
    _, resumen = motor.evaluar(docs, presentes)
    return {regla_id: estado for estado, ids in resumen.por_estado.items() for regla_id in ids}


def _evaluar(carpeta: Path | discover.CarpetaFactura, cfg: Config, base: MotorReglas | None, candidato: MotorReglas) -> tuple[str, dict, dict] | None:
    from .. import pipeline  # import diferido: pipeline importa el motor

    try:
        cf = carpeta if isinstance(carpeta, discover.CarpetaFactura) else discover.clasificar(carpeta, cfg)
        docs = pipeline.extraer(cf, cfg)
        presentes = cf.presentes()
        return cf.factura, _estados(base, docs, presentes), _estados(candidato, docs, presentes)
    except Exception as e:  # una factura rota no detiene la prueba
        log.error("factura %s: error %s", getattr(carpeta, "factura", None) or getattr(carpeta, "name", "?"), type(e).__name__)
        return None


def comparar(carpetas: list, cfg: Config, base: MotorReglas | None, candidato: MotorReglas, workers: int = 1) -> dict:
    if workers > 1:
        os.environ["VALIDADOR_OCR_HILOS"] = str(max(1, (os.cpu_count() or 2) // workers))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            filas = list(ex.map(_evaluar, carpetas, [cfg] * len(carpetas), [base] * len(carpetas), [candidato] * len(carpetas)))
    else:
        filas = [_evaluar(c, cfg, base, candidato) for c in carpetas]
    filas = [f for f in filas if f is not None]

    conteo_base: dict[str, Counter] = defaultdict(Counter)
    conteo_cand: dict[str, Counter] = defaultdict(Counter)
    cambios: dict[str, list[str]] = defaultdict(list)
    facturas_con_cambio: set[str] = set()
    for factura, eb, ec in filas:
        for regla_id in set(eb) | set(ec):
            antes, despues = eb.get(regla_id, "—"), ec.get(regla_id, "—")
            conteo_base[regla_id][antes] += 1
            conteo_cand[regla_id][despues] += 1
            if antes != despues and "falla" in (antes, despues):
                cambios[regla_id].append(f"{factura}: {antes} → {despues}")
                facturas_con_cambio.add(factura)
    reglas = sorted(set(conteo_base) | set(conteo_cand))
    return {
        "facturas": len(filas),
        "facturas_con_cambio_en_fallas": len(facturas_con_cambio),
        "fallas_base": sum(c["falla"] for c in conteo_base.values()),
        "fallas_candidato": sum(c["falla"] for c in conteo_cand.values()),
        "reglas": {
            r: {
                "base": dict(conteo_base[r]),
                "candidato": dict(conteo_cand[r]),
                "cambios": cambios[r][:MAX_EJEMPLOS],
                "total_cambios": len(cambios[r]),
            }
            for r in reglas
        },
    }
