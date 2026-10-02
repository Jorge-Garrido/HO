"""Línea de comandos.

    validador tarifario importar documentos/base_cups.xlsx --uvb-anio 2025
    validador procesar "nuevaeps/NUEVA EPS EXITOSA" --salida salidas
    validador reglas|catalogo|contrato ...   (ver cli_referencia.py)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .. import __version__, pipeline, report, salida_errores
from ..base.config import get_config
from ..ingest import discover
from ..reglas import importador as importador_reglas
from ..reglas.motor import ConjuntoCargado, MotorReglas
from ..store import db, referencia
from ..tarifario import importer
from . import cli_referencia


def _importar_tarifario(args: argparse.Namespace) -> int:
    cfg = get_config()
    res = importer.importar(Path(args.archivo), Decimal(args.uvb_valor) if args.uvb_valor else None)
    for a in res.avisos:
        print(f"AVISO  {a}")
    for e in res.errores:
        print(f"ERROR  {e}")
    if not res.ok:
        print(f"Tarifario rechazado: {len(res.errores)} errores. Corrija el Excel y vuelva a importar.")
        return 1
    engine = db.conectar(cfg.db_url)
    version_id = db.guardar_tarifario(engine, res, args.nombre or res.archivo, args.uvb_anio)
    if args.csv:
        importer.exportar_csv(res, Path(args.csv))
    print(f"Tarifario importado: versión {version_id}, {len(res.items)} ítems, UVB {res.uvb_valor}, sha256 {res.sha256[:12]}")
    return 0


def _trabajo(carpeta, corrida_id, cfg, tarifario, motor):
    try:
        return pipeline.procesar_carpeta(carpeta, corrida_id, cfg, tarifario, None, motor)
    except Exception as e:  # una factura rota no detiene la corrida
        logging.getLogger(__name__).error("factura %s: error %s", carpeta.name, type(e).__name__)
        return None


def _procesar_todas(carpetas, corrida_id, cfg, tarifario, motor, workers: int):
    """Procesa en paralelo por procesos (el OCR usa CPU). Cada proceso limita sus hilos."""
    if workers <= 1:
        for c in carpetas:
            if (r := _trabajo(c, corrida_id, cfg, tarifario, motor)) is not None:
                yield r
        return
    os.environ["VALIDADOR_OCR_HILOS"] = str(max(1, (os.cpu_count() or 2) // workers))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futuros = [ex.submit(_trabajo, c, corrida_id, cfg, tarifario, motor) for c in carpetas]
        for f in as_completed(futuros):
            if (r := f.result()) is not None:
                yield r


def _procesar(args: argparse.Namespace) -> int:
    cfg = get_config()
    if args.sin_ocr:
        cfg = cfg.model_copy(update={"ocr_habilitado": False})
    engine = None if args.sin_db else db.conectar(cfg.db_url)
    tarifario = db.cargar_tarifario(engine, args.tarifario) if engine is not None else None
    if tarifario is None:
        print("AVISO  sin tarifario cargado: no se validan tarifas (use 'validador tarifario importar').")
    motor = _motor(args, cfg, engine)

    corrida_id = f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}-{secrets.token_hex(2)}"
    salida = Path(args.salida) / corrida_id.replace(":", "")
    if engine is not None:
        db.iniciar_corrida(engine, corrida_id, __version__)

    carpetas = discover.carpetas_de_facturas(Path(args.ruta))
    if args.limite:
        carpetas = carpetas[: args.limite]
    resultados = []
    t0 = time.perf_counter()
    for i, r in enumerate(_procesar_todas(carpetas, corrida_id, cfg, tarifario, motor, args.workers), 1):
        if engine is not None:
            pipeline.marcar_duplicados(r, engine)
            db.guardar_resultado(engine, r)
        resultados.append(r)
        report.escribir_factura(r, salida)
        print(f"\r  {i}/{len(carpetas)} facturas ({time.perf_counter() - t0:.0f} s)", end="", flush=True)
    print()

    resumen = report.resumen(corrida_id, resultados)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    report.escribir_casos(resultados, salida / "casos_con_posible_error.csv")
    report.escribir_propuestas(resultados, salida / "propuestas_correccion.json")
    eps = salida_errores.nombre_eps(Path(args.ruta))
    carpeta_eps = salida_errores.preparar_destino(Path(args.errores), eps)
    salida_errores.copiar_errores(resultados, carpeta_eps)
    copiadas = {r.factura for r in resultados if salida_errores.discrepancias(r)}
    reporte = carpeta_eps / f"REPORTE {eps}.xlsx"
    salida_errores.escribir_excel(resultados, reporte, eps, corrida_id, copiadas)
    shutil.copy(reporte, salida / reporte.name)
    if engine is not None:
        db.cerrar_corrida(engine, corrida_id, resumen)
    _imprimir_discrepancias(resultados)
    print(f"\nFacturas: {len(resultados)} | con discrepancias: {len(copiadas)} | sin discrepancias: {len(resultados) - len(copiadas)}")
    print(f"Carpetas con error: {carpeta_eps}")
    print(f"Reporte Excel:      {reporte}")
    print(f"Detalle técnico:    {salida}")
    return 0


def _motor(args: argparse.Namespace, cfg, engine) -> MotorReglas | None:
    """Reglas normativas: las vigentes en la base, las versiones indicadas con --reglas
    (para probar una versión no vigente) o un archivo con --reglas-archivo (sin base)."""
    if args.sin_reglas or not cfg.reglas_habilitadas:
        return None
    if args.reglas_archivo:
        conjunto, _ = importador_reglas.leer(Path(args.reglas_archivo))
        print(f"AVISO  reglas desde archivo ({args.reglas_archivo}): sin catálogos ni contratos, resultados sin versión de reglas")
        return MotorReglas([ConjuntoCargado(conjunto, None)])
    motor = referencia.cargar_motor(engine, args.reglas) if engine is not None else None
    if motor is None:
        print("AVISO  sin reglas normativas vigentes (use 'validador reglas importar' y 'validador reglas activar').")
    else:
        nombres = ", ".join(f"{c.conjunto.nombre} (id {c.version_id})" for c in motor.conjuntos)
        print(f"Reglas normativas: {nombres}; catálogos: {', '.join(motor.catalogos.nombres) or 'ninguno'}; contratos: {len(motor.contratos)}")
    return motor


def _imprimir_discrepancias(resultados) -> None:
    filas = [(r.factura, d) for r in resultados for d in salida_errores.discrepancias(r)]
    if not filas:
        print("Sin discrepancias.")
        return
    print("\nCAMPOS CON DISCREPANCIA")
    for factura, d in filas:
        valores = "  ".join(
            f"{salida_errores.NOMBRE_FUENTE.get(k, k)}={v}{' (!)' if k in d['sospechosos'] else ''}" for k, v in d["valores"].items()
        )
        print(f"  {factura}  {d['severidad'].upper():5}  {d['campo'][:40]:40}  {valores}")
        if d["motivo"] and (d["tipo"] != "Campo" or not valores):
            print(f"  {'':9}  {'':5}  {'':40}  → {d['motivo']}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="validador")
    sub = p.add_subparsers(dest="comando", required=True)

    t = sub.add_parser("tarifario", help="gestión del tarifario").add_subparsers(dest="accion", required=True)
    imp = t.add_parser("importar", help="importa el Excel del tarifario a la base de datos")
    imp.add_argument("archivo")
    imp.add_argument("--nombre")
    imp.add_argument("--uvb-anio", type=int)
    imp.add_argument("--uvb-valor", help="si el Excel no trae el valor de la UVB en el encabezado")
    imp.add_argument("--csv", help="exporta además una copia normalizada en CSV")
    imp.set_defaults(func=_importar_tarifario)

    pr = sub.add_parser("procesar", help="valida una carpeta de factura o una carpeta con varias")
    pr.add_argument("ruta")
    pr.add_argument("--salida", default="salidas")
    pr.add_argument("--limite", type=int)
    pr.add_argument("--tarifario", type=int, help="id de versión del tarifario (por defecto la última)")
    pr.add_argument("--sin-db", action="store_true", help="no usa la base de datos (sin tarifario ni duplicados)")
    pr.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 4), help="procesos en paralelo")
    pr.add_argument("--sin-ocr", action="store_true", help="no lee las autorizaciones escaneadas")
    pr.add_argument("--errores", default="CON ERROR", help="carpeta donde se copian las facturas con discrepancias (una subcarpeta por EPS)")
    pr.add_argument("--reglas", type=int, action="append", help="id de versión de reglas a usar en vez de la vigente (repetible)")
    pr.add_argument("--reglas-archivo", help="conjunto de reglas YAML/JSON sin pasar por la base (pruebas)")
    pr.add_argument("--sin-reglas", action="store_true", help="no aplica las reglas normativas")
    pr.set_defaults(func=_procesar)

    cli_referencia.registrar(sub)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
