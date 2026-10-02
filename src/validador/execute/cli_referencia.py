"""Comandos de mantenimiento de la referencia versionada: reglas, catálogos y contratos.

    validador reglas importar referencia/reglas/normativa.yaml
    validador reglas probar 2 "nuevaeps/NUEVA EPS EXITOSA"       # sombra contra la vigente
    validador reglas aprobar 2 && validador reglas activar 2
    validador catalogo importar CIE10.xlsx --nombre CIE10 --vigente-desde 2026-01-01
    validador contrato importar referencia/contratos/<EPS>.yaml

Ver documentos/ARQUITECTURA_REGLAS.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from ..base.config import get_config
from ..ingest import discover, pdf_text
from ..reglas import catalogos, condicion, contratos, expediente, importador, primitivas, sombra
from ..reglas.modelos import Regla
from ..store import db, referencia


def _engine():
    return db.conectar(get_config().db_url)


def _imprimir_tabla(filas: list[dict], columnas: list[str]) -> None:
    if not filas:
        print("(vacío)")
        return
    anchos = {c: max(len(c), *(len(str(f.get(c) if f.get(c) is not None else "")) for f in filas)) for c in columnas}
    print("  ".join(c.upper().ljust(anchos[c]) for c in columnas))
    for f in filas:
        valores = []
        for c in columnas:
            v = f.get(c)
            if isinstance(v, datetime):
                v = v.strftime("%Y-%m-%d %H:%M")
            valores.append(str(v if v is not None else "").ljust(anchos[c]))
        print("  ".join(valores))


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# --- reglas ---------------------------------------------------------------------------------


def _reglas_importar(a) -> int:
    conjunto, sha_archivo = importador.leer(Path(a.archivo))
    engine = _engine()
    base = referencia.vigente_id(engine, conjunto.nombre)
    cid, nuevo = referencia.guardar_conjunto(
        engine, conjunto, referencia.usuario_actual(a.usuario), "archivo", f"{a.archivo} (sha256 {sha_archivo[:12]})", base
    )
    print(f"{'Versión nueva' if nuevo else 'Ya existía (mismo contenido)'}: conjunto '{conjunto.nombre}' id {cid}, {len(conjunto.reglas)} reglas, estado borrador")
    if nuevo:
        print(f"Siguiente paso: validador reglas probar {cid} <carpeta de facturas>")
    return 0


def _reglas_restaurar(a) -> int:
    """Vuelve a una versión anterior: copia su contenido como versión nueva (borrador)."""
    engine = _engine()
    conjunto, fila = referencia.obtener_conjunto(engine, a.id)
    base = referencia.vigente_id(engine, conjunto.nombre)
    cid, nuevo = referencia.guardar_conjunto(
        engine, conjunto, referencia.usuario_actual(a.usuario), "restauracion", f"copia de la versión {fila['version']} (id {a.id})", base
    )
    if not nuevo:
        print(f"El contenido de {a.id} ya está en la versión {cid} (no retirada); use esa.")
        return 1
    print(f"Conjunto {cid} creado en borrador con el contenido de {a.id}. Siguiente: validador reglas probar {cid} <facturas>")
    return 0


def _reglas_listar(a) -> int:
    _imprimir_tabla(referencia.listar_conjuntos(_engine()), ["id", "nombre", "version", "estado", "origen", "base_id", "creado_por", "creado_en"])
    return 0


def _reglas_mostrar(a) -> int:
    conjunto, fila = referencia.obtener_conjunto(_engine(), a.id)
    if a.json:
        print(_json(conjunto.model_dump(mode="json")))
        return 0
    print(f"Conjunto '{fila['nombre']}' v{fila['version']} (id {fila['id']}) — {fila['estado']} — {fila['origen']}: {fila['origen_detalle'] or ''}")
    _imprimir_tabla(
        [
            {"id": r.id, "v": r.version, "primitiva": r.primitiva, "alcance": r.alcance, "vigencia": f"{r.vigencia.desde}..{r.vigencia.hasta or ''}",
             "severidad": r.resultado.severidad, "tipo": r.resultado.tipo, "glosa": r.resultado.codigo_glosa, "activa": r.activa}
            for r in conjunto.reglas
        ],
        ["id", "v", "primitiva", "alcance", "vigencia", "tipo", "severidad", "glosa", "activa"],
    )
    return 0


def _reglas_probar(a) -> int:
    cfg = get_config()
    if not a.con_ocr:
        cfg = cfg.model_copy(update={"ocr_habilitado": False})
    engine = _engine()
    candidato_conjunto, fila = referencia.obtener_conjunto(engine, a.id)
    contra = a.contra if a.contra is not None else referencia.vigente_id(engine, candidato_conjunto.nombre)
    if contra == a.id:
        contra = None
    candidato = referencia.cargar_motor(engine, [a.id])
    base = referencia.cargar_motor(engine, [contra]) if contra else None
    carpetas = [c for ruta in a.rutas for c in discover.descubrir(Path(ruta), cfg).facturas]
    if a.limite:
        carpetas = carpetas[: a.limite]
    print(f"Prueba en sombra: conjunto {a.id} contra {contra or 'ninguno (primera versión)'} sobre {len(carpetas)} facturas…")
    informe = sombra.comparar(carpetas, cfg, base, candidato, a.workers)
    informe.update({"conjunto_id": a.id, "contra_id": contra, "rutas": a.rutas, "fecha": datetime.now(timezone.utc).isoformat()})
    salida = Path(a.salida) / f"sombra-conjunto{a.id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(_json(informe), encoding="utf-8")

    print(f"\nFacturas: {informe['facturas']} | fallas base: {informe['fallas_base']} | fallas candidato: {informe['fallas_candidato']}"
          f" | facturas con cambios: {informe['facturas_con_cambio_en_fallas']}")
    filas = []
    for rid, d in informe["reglas"].items():
        filas.append({"regla": rid, "base": _resumir(d["base"]), "candidato": _resumir(d["candidato"]), "cambios": d["total_cambios"]})
    _imprimir_tabla(filas, ["regla", "base", "candidato", "cambios"])
    print(f"\nInforme completo: {salida}")

    resumen = {k: informe[k] for k in ("facturas", "facturas_con_cambio_en_fallas", "fallas_base", "fallas_candidato", "contra_id", "rutas")}
    resumen["informe"] = str(salida)
    if fila["estado"] in ("borrador", "en_prueba"):
        referencia.cambiar_estado(engine, a.id, "en_prueba", referencia.usuario_actual(a.usuario), resumen)
        print(f"Estado: en_prueba. Si el resultado es el esperado: validador reglas aprobar {a.id}")
    return 0


def _resumir(conteo: dict) -> str:
    orden = ["falla", "reportada_muv", "no_evaluable", "cumple", "no_aplica", "—"]
    return " ".join(f"{k}={conteo[k]}" for k in orden if conteo.get(k))


def _transicion(estado: str):
    def accion(a) -> int:
        cfg = get_config()
        detalle = {"nota": a.nota} if getattr(a, "nota", None) else None
        referencia.cambiar_estado(_engine(), a.id, estado, referencia.usuario_actual(a.usuario), detalle, cfg.aprobacion_cuatro_ojos)
        print(f"Conjunto {a.id}: {estado}")
        return 0

    return accion


def _reglas_bitacora(a) -> int:
    for e in referencia.bitacora(_engine(), "regla_conjunto", a.id):
        print(f"{e['fecha']:%Y-%m-%d %H:%M}  {e['usuario']:15} {e['accion']:14} {json.dumps(e['detalle'], ensure_ascii=False, default=str) if e['detalle'] else ''}")
    return 0


def _reglas_historia(a) -> int:
    _imprimir_tabla(
        referencia.historia_regla(_engine(), a.regla_id),
        ["conjunto_id", "conjunto_version", "estado", "version", "vigente_desde", "vigente_hasta", "activa", "norma"],
    )
    return 0


def _reglas_cobertura(a) -> int:
    if a.archivo:
        conjunto, _ = importador.leer(Path(a.archivo))
    else:
        engine = _engine()
        cid = a.id or referencia.vigente_id(engine, "normativa")
        if cid is None:
            print("No hay conjunto vigente: indique --id o --archivo")
            return 1
        conjunto, _ = referencia.obtener_conjunto(engine, cid)
    filas = importador.cobertura(conjunto, importador.leer_matriz(Path(a.matriz)))
    _imprimir_tabla(filas, ["matriz", "campo", "glosa", "reglas", "cruce"])
    por_reglas = sum(1 for f in filas if f["reglas"])
    total = sum(1 for f in filas if f["reglas"] or f["cruce"])
    print(f"\nCubiertas: {total}/{len(filas)} ({por_reglas} por reglas normativas, el resto por el cruce entre documentos)")
    return 0


def _reglas_rutas(a) -> int:
    for ruta, desc in expediente.RUTAS.items():
        print(f"{ruta:42} {desc}")
    return 0


def _reglas_primitivas(a) -> int:
    for p in primitivas.describir():
        params = ", ".join(p["params"].get("properties", {}))
        print(f"{p['nombre']:22} {p['descripcion']}\n{'':22} params: {params}\n")
    return 0


def _reglas_contexto_agente(a) -> int:
    """Todo lo que el agente normativo necesita para proponer reglas, en un JSON."""
    engine = _engine()
    cid = a.base or referencia.vigente_id(engine, "normativa")
    vigentes = referencia.obtener_conjunto(engine, cid)[0].model_dump(mode="json") if cid else None
    contexto = {
        "instrucciones": "documentos/prompts/agente_normativo.md",
        "rutas_expediente": expediente.RUTAS,
        "primitivas": primitivas.describir(),
        "operadores_condicion": sorted(condicion.OPERADORES),
        "esquema_regla": Regla.model_json_schema(),
        "esquema_respuesta": importador.PropuestaAgente.model_json_schema(),
        "conjunto_base_id": cid,
        "reglas_vigentes": vigentes,
    }
    texto = _json(contexto)
    if a.salida:
        Path(a.salida).write_text(texto, encoding="utf-8")
        print(f"Contexto escrito en {a.salida}")
    else:
        print(texto)
    return 0


def _texto_norma(ruta: Path) -> str:
    if ruta.suffix.lower() == ".pdf":
        return "\n".join(p.texto for p in pdf_text.leer(ruta).paginas)
    return ruta.read_text(encoding="utf-8")


def _reglas_proponer(a) -> int:
    engine = _engine()
    propuesta = importador.PropuestaAgente.model_validate_json(Path(a.propuesta).read_text(encoding="utf-8"))
    base_id = a.base or referencia.vigente_id(engine, "normativa")
    if base_id is None:
        print("No hay conjunto base: importe primero un conjunto o indique --base")
        return 1
    base, _ = referencia.obtener_conjunto(engine, base_id)
    texto = _texto_norma(Path(a.norma)) if a.norma else None
    if texto is None:
        print("AVISO  sin --norma: las citas no se verifican contra el documento (no recomendado)")
    nuevo, informe = importador.aplicar_propuesta(base, propuesta, texto)
    for r in informe["rechazados"]:
        print(f"RECHAZADO  {r['accion']:9} {r['regla_id']}: {r['motivo']}")
    for r in informe["requiere_extractor"]:
        print(f"EXTRACTOR  {r}")
    for r in informe["aplicados"]:
        print(f"APLICADO   {r['accion']:9} {r['regla_id']} ({r['cita']})")
    if not informe["aplicados"]:
        print("Ningún cambio aplicable: no se crea versión.")
        return 1
    cid, nuevo_flag = referencia.guardar_conjunto(
        engine, nuevo, referencia.usuario_actual(a.usuario), "agente", f"{propuesta.norma}: {propuesta.resumen}"[:2000], base_id,
        {"propuesta": a.propuesta, "norma_archivo": a.norma, "informe": informe},
    )
    print(f"\nConjunto {cid} creado en borrador a partir de {base_id}. Revisar con: validador reglas mostrar {cid}; probar con: validador reglas probar {cid} <facturas>")
    return 0


# --- catálogos y contratos -----------------------------------------------------------------------


def _catalogo_importar(a) -> int:
    res = catalogos.importar(Path(a.archivo), a.nombre, a.col_codigo, a.col_descripcion, a.hoja)
    for x in res.avisos[:20]:
        print(f"AVISO  {x}")
    for x in res.errores[:50]:
        print(f"ERROR  {x}")
    if not res.ok:
        print("Catálogo rechazado.")
        return 1
    vid, nuevo = referencia.guardar_catalogo(
        _engine(), res, referencia.usuario_actual(a.usuario), a.etiqueta, a.fuente,
        date.fromisoformat(a.vigente_desde) if a.vigente_desde else None,
        date.fromisoformat(a.vigente_hasta) if a.vigente_hasta else None,
    )
    atributos = sorted({k for it in res.items for k in it.atributos})
    print(f"{'Versión nueva' if nuevo else 'Ya existía'}: catálogo {res.nombre} id {vid}, {len(res.items)} códigos; atributos: {', '.join(atributos) or '(ninguno)'}")
    return 0


def _catalogo_listar(a) -> int:
    _imprimir_tabla(referencia.listar_catalogos(_engine()), ["id", "nombre", "etiqueta", "items", "vigente_desde", "vigente_hasta", "importado_en"])
    return 0


def _contrato_importar(a) -> int:
    contrato, sha = contratos.leer(Path(a.archivo))
    cid, nuevo = referencia.guardar_contrato(_engine(), contrato, sha, referencia.usuario_actual(a.usuario))
    print(f"{'Versión nueva' if nuevo else 'Ya existía'}: contrato {contrato.eps_nombre} ({contrato.eps_nit}) id {cid}, vigencia {contrato.vigencia.desde}..{contrato.vigencia.hasta or ''}")
    return 0


def _contrato_listar(a) -> int:
    _imprimir_tabla(referencia.listar_contratos(_engine()), ["id", "eps_nit", "eps_nombre", "numero_contrato", "version", "vigente_desde", "vigente_hasta", "activo"])
    return 0


def registrar(sub: argparse._SubParsersAction) -> None:
    usuario = argparse.ArgumentParser(add_help=False)
    usuario.add_argument("--usuario", help="quién hace el cambio (por defecto VALIDADOR_USUARIO o el usuario del sistema)")

    r = sub.add_parser("reglas", help="reglas normativas versionadas").add_subparsers(dest="accion", required=True)
    p = r.add_parser("importar", parents=[usuario], help="importa un YAML/JSON como versión nueva (borrador)")
    p.add_argument("archivo")
    p.set_defaults(func=_reglas_importar)
    r.add_parser("listar", help="versiones de conjuntos y su estado").set_defaults(func=_reglas_listar)
    p = r.add_parser("restaurar", parents=[usuario], help="copia una versión anterior como borrador (para volver atrás)")
    p.add_argument("id", type=int)
    p.set_defaults(func=_reglas_restaurar)
    p = r.add_parser("mostrar", help="reglas de una versión")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_reglas_mostrar)
    p = r.add_parser("probar", parents=[usuario], help="prueba en sombra contra la versión vigente (o --contra)")
    p.add_argument("id", type=int)
    p.add_argument("rutas", nargs="+", help="carpetas de facturas")
    p.add_argument("--contra", type=int)
    p.add_argument("--limite", type=int)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--con-ocr", action="store_true", help="incluye el OCR de autorizaciones (más lento)")
    p.add_argument("--salida", default="salidas/sombra")
    p.set_defaults(func=_reglas_probar)
    for nombre, estado, ayuda in (("aprobar", "aprobada", "aprueba una versión probada"),
                                  ("activar", "vigente", "pone en producción una versión aprobada (retira la anterior)"),
                                  ("retirar", "retirada", "retira una versión")):
        p = r.add_parser(nombre, parents=[usuario], help=ayuda)
        p.add_argument("id", type=int)
        p.add_argument("--nota")
        p.set_defaults(func=_transicion(estado))
    p = r.add_parser("bitacora", help="auditoría de una versión")
    p.add_argument("id", type=int)
    p.set_defaults(func=_reglas_bitacora)
    p = r.add_parser("historia", help="todas las versiones de una regla")
    p.add_argument("regla_id")
    p.set_defaults(func=_reglas_historia)
    p = r.add_parser("cobertura", help="qué reglas de la matriz SEMCOL están implementadas")
    p.add_argument("--matriz", default="documentos/SEMCOL_Consulta_Validaciones_Normativas.xlsx")
    p.add_argument("--id", type=int)
    p.add_argument("--archivo")
    p.set_defaults(func=_reglas_cobertura)
    r.add_parser("rutas", help="rutas del expediente disponibles para las reglas").set_defaults(func=_reglas_rutas)
    r.add_parser("primitivas", help="primitivas disponibles y sus parámetros").set_defaults(func=_reglas_primitivas)
    p = r.add_parser("contexto-agente", help="JSON con todo lo que necesita el agente normativo")
    p.add_argument("--base", type=int)
    p.add_argument("--salida")
    p.set_defaults(func=_reglas_contexto_agente)
    p = r.add_parser("proponer", parents=[usuario], help="aplica una propuesta del agente como versión borrador")
    p.add_argument("propuesta", help="JSON con la respuesta del agente")
    p.add_argument("--norma", help="PDF o texto de la norma, para verificar las citas")
    p.add_argument("--base", type=int)
    p.set_defaults(func=_reglas_proponer)

    c = sub.add_parser("catalogo", help="catálogos de referencia versionados").add_subparsers(dest="accion", required=True)
    p = c.add_parser("importar", parents=[usuario], help="importa un CSV/XLSX como versión nueva")
    p.add_argument("archivo")
    p.add_argument("--nombre", required=True, help="CIE10, CUPSRIPS, RIPSFINALIDADCONSULTAVERSION2...")
    p.add_argument("--col-codigo", default="codigo")
    p.add_argument("--col-descripcion", default="descripcion")
    p.add_argument("--hoja")
    p.add_argument("--etiqueta")
    p.add_argument("--fuente")
    p.add_argument("--vigente-desde")
    p.add_argument("--vigente-hasta")
    p.set_defaults(func=_catalogo_importar)
    c.add_parser("listar").set_defaults(func=_catalogo_listar)

    k = sub.add_parser("contrato", help="contratos por EPS versionados").add_subparsers(dest="accion", required=True)
    p = k.add_parser("importar", parents=[usuario], help="importa un contrato (JSON/YAML) como versión nueva")
    p.add_argument("archivo")
    p.set_defaults(func=_contrato_importar)
    k.add_parser("listar").set_defaults(func=_contrato_listar)
