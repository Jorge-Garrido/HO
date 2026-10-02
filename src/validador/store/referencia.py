"""Persistencia de la referencia versionada: conjuntos de reglas, catálogos, contratos y la
bitácora de auditoría.

Principios (ver documentos/ARQUITECTURA_REGLAS.md):

- **Inmutable**: una versión guardada no se edita. Corregir es crear otra versión.
- **Ciclo de vida** de un conjunto de reglas: borrador → en_prueba → aprobada → vigente →
  retirada. Solo puede haber una versión vigente por nombre de conjunto.
- **Auditoría**: cada importación, prueba, aprobación, activación y retiro queda en
  ``auditoria`` con usuario, fecha y detalle.
- Cada resultado de factura guarda qué versión de reglas, catálogos y contrato se usó, así
  que una factura se puede volver a validar exactamente como se validó.
"""

from __future__ import annotations

import getpass
import os
from datetime import date

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

from ..reglas.catalogos import Catalogos, ItemCatalogo, ResultadoImportacionCatalogo, VersionCatalogo
from ..reglas.contratos import Contrato, Contratos
from ..reglas.importador import huella
from ..reglas.modelos import ConjuntoReglas, EstadoConjunto
from ..reglas.motor import ConjuntoCargado, MotorReglas
from .db import JsonType, ahora, metadata

# --- tablas --------------------------------------------------------------------------------

regla_conjunto = Table(
    "regla_conjunto",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("nombre", String(60), nullable=False),
    Column("version", Integer, nullable=False),
    Column("estado", String(12), nullable=False, index=True),
    Column("origen", String(20), nullable=False),  # archivo | agente | manual
    Column("origen_detalle", Text),  # archivo, norma, propuesta...
    Column("base_id", Integer, ForeignKey("regla_conjunto.id")),  # versión de la que parte
    Column("sha256", String(64), nullable=False),
    Column("descripcion", Text),
    Column("contenido", JsonType, nullable=False),  # ConjuntoReglas completo
    Column("creado_en", DateTime(timezone=True), nullable=False),
    Column("creado_por", String(100), nullable=False),
    UniqueConstraint("nombre", "version"),
)

# Una fila por regla de cada versión de conjunto: permite consultar la historia de una regla
# ("todas las versiones de RIPS-FECHA-PERIODO") sin abrir los JSON.
regla = Table(
    "regla",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("conjunto_id", Integer, ForeignKey("regla_conjunto.id"), nullable=False),
    Column("regla_id", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("primitiva", String(40), nullable=False),
    Column("vigente_desde", Date, nullable=False),
    Column("vigente_hasta", Date),
    Column("activa", Boolean, nullable=False),
    Column("codigo_glosa", String(10)),
    Column("norma", String(300), nullable=False),
    Column("contenido", JsonType, nullable=False),
    UniqueConstraint("conjunto_id", "regla_id", "version"),
)
Index("ix_regla_regla_id", regla.c.regla_id)

catalogo_version = Table(
    "catalogo_version",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("nombre", String(60), nullable=False, index=True),
    Column("etiqueta", String(100)),  # p. ej. "SISPRO 2026-07" o "CUPS Res. 2706/2025"
    Column("fuente", String(300)),
    Column("archivo", String(300), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("vigente_desde", Date),
    Column("vigente_hasta", Date),
    Column("items", Integer, nullable=False),
    Column("avisos", JsonType),
    Column("importado_en", DateTime(timezone=True), nullable=False),
    Column("importado_por", String(100), nullable=False),
    UniqueConstraint("nombre", "sha256"),
)

catalogo_item = Table(
    "catalogo_item",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("version_id", Integer, ForeignKey("catalogo_version.id"), nullable=False),
    Column("codigo", String(40), nullable=False),
    Column("descripcion", Text),
    Column("atributos", JsonType),
    UniqueConstraint("version_id", "codigo"),
)

contrato_version = Table(
    "contrato_version",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("eps_nit", String(20), nullable=False, index=True),
    Column("eps_nombre", String(200), nullable=False),
    Column("numero_contrato", String(100)),
    Column("version", Integer, nullable=False),
    Column("vigente_desde", Date, nullable=False),
    Column("vigente_hasta", Date),
    Column("activo", Boolean, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("contenido", JsonType, nullable=False),
    Column("creado_en", DateTime(timezone=True), nullable=False),
    Column("creado_por", String(100), nullable=False),
)

auditoria = Table(
    "auditoria",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("fecha", DateTime(timezone=True), nullable=False),
    Column("usuario", String(100), nullable=False),
    Column("entidad", String(30), nullable=False),  # regla_conjunto | catalogo | contrato
    Column("entidad_id", Integer, nullable=False),
    Column("accion", String(30), nullable=False),
    Column("detalle", JsonType),
)
Index("ix_auditoria_entidad", auditoria.c.entidad, auditoria.c.entidad_id)

TRANSICIONES: dict[str, set[str]] = {
    "borrador": {"en_prueba", "retirada"},
    "en_prueba": {"en_prueba", "aprobada", "borrador", "retirada"},
    "aprobada": {"vigente", "retirada"},
    "vigente": {"retirada"},
    "retirada": set(),
}


class ErrorReferencia(ValueError):
    pass


def usuario_actual(usuario: str | None = None) -> str:
    return usuario or os.environ.get("VALIDADOR_USUARIO") or getpass.getuser()


def _auditar(con: Connection, usuario: str, entidad: str, entidad_id: int, accion: str, detalle: dict | None = None) -> None:
    con.execute(insert(auditoria).values(fecha=ahora(), usuario=usuario, entidad=entidad, entidad_id=entidad_id, accion=accion, detalle=detalle))


def bitacora(engine: Engine, entidad: str, entidad_id: int) -> list[dict]:
    with engine.connect() as con:
        filas = con.execute(
            select(auditoria).where(auditoria.c.entidad == entidad, auditoria.c.entidad_id == entidad_id).order_by(auditoria.c.id)
        ).mappings()
        return [dict(f) for f in filas]


# --- conjuntos de reglas ----------------------------------------------------------------------


def guardar_conjunto(
    engine: Engine,
    conjunto: ConjuntoReglas,
    usuario: str,
    origen: str,
    origen_detalle: str | None = None,
    base_id: int | None = None,
    detalle_auditoria: dict | None = None,
) -> tuple[int, bool]:
    """Guarda una versión nueva en estado borrador. Si el contenido es idéntico a una versión
    del mismo nombre que no está retirada, devuelve esa (id, False) en vez de duplicarla. Una
    versión retirada sí se puede volver a crear: así se restaura (vuelve a pasar por sombra)."""
    sha = huella(conjunto)
    with engine.begin() as con:
        existente = con.execute(
            select(regla_conjunto.c.id).where(
                regla_conjunto.c.nombre == conjunto.nombre, regla_conjunto.c.sha256 == sha, regla_conjunto.c.estado != "retirada"
            )
        ).scalar_one_or_none()
        if existente is not None:
            return existente, False
        version = (con.execute(select(func.max(regla_conjunto.c.version)).where(regla_conjunto.c.nombre == conjunto.nombre)).scalar() or 0) + 1
        conjunto_id = con.execute(
            insert(regla_conjunto).values(
                nombre=conjunto.nombre, version=version, estado="borrador", origen=origen, origen_detalle=origen_detalle,
                base_id=base_id, sha256=sha, descripcion=conjunto.descripcion, contenido=conjunto.model_dump(mode="json"),
                creado_en=ahora(), creado_por=usuario,
            )
        ).inserted_primary_key[0]
        con.execute(
            insert(regla),
            [
                {
                    "conjunto_id": conjunto_id, "regla_id": r.id, "version": r.version, "primitiva": r.primitiva,
                    "vigente_desde": r.vigencia.desde, "vigente_hasta": r.vigencia.hasta, "activa": r.activa,
                    "codigo_glosa": r.resultado.codigo_glosa, "norma": str(r.norma)[:300], "contenido": r.model_dump(mode="json"),
                }
                for r in conjunto.reglas
            ],
        )
        _auditar(con, usuario, "regla_conjunto", conjunto_id, "creada", {"origen": origen, "detalle": origen_detalle, "base_id": base_id, "reglas": len(conjunto.reglas), **(detalle_auditoria or {})})
    return conjunto_id, True


def listar_conjuntos(engine: Engine) -> list[dict]:
    with engine.connect() as con:
        filas = con.execute(
            select(
                regla_conjunto.c.id, regla_conjunto.c.nombre, regla_conjunto.c.version, regla_conjunto.c.estado,
                regla_conjunto.c.origen, regla_conjunto.c.creado_en, regla_conjunto.c.creado_por, regla_conjunto.c.base_id,
            ).order_by(regla_conjunto.c.nombre, regla_conjunto.c.version)
        ).mappings()
        return [dict(f) for f in filas]


def obtener_conjunto(engine: Engine, conjunto_id: int) -> tuple[ConjuntoReglas, dict]:
    with engine.connect() as con:
        fila = con.execute(select(regla_conjunto).where(regla_conjunto.c.id == conjunto_id)).mappings().one_or_none()
    if fila is None:
        raise ErrorReferencia(f"no existe el conjunto de reglas {conjunto_id}")
    return ConjuntoReglas.model_validate(fila["contenido"]), dict(fila)


def vigente_id(engine: Engine, nombre: str) -> int | None:
    with engine.connect() as con:
        return con.execute(
            select(regla_conjunto.c.id).where(regla_conjunto.c.nombre == nombre, regla_conjunto.c.estado == "vigente")
        ).scalar_one_or_none()


def cambiar_estado(engine: Engine, conjunto_id: int, nuevo: EstadoConjunto, usuario: str, detalle: dict | None = None,
                   cuatro_ojos: bool = False) -> None:
    """Transición del ciclo de vida, con sus controles:

    - aprobar exige al menos una prueba en sombra registrada;
    - con ``cuatro_ojos`` quien aprueba no puede ser quien creó la versión;
    - activar retira la versión vigente anterior del mismo conjunto, en la misma transacción.
    """
    with engine.begin() as con:
        fila = con.execute(select(regla_conjunto).where(regla_conjunto.c.id == conjunto_id)).mappings().one_or_none()
        if fila is None:
            raise ErrorReferencia(f"no existe el conjunto de reglas {conjunto_id}")
        actual = fila["estado"]
        if nuevo not in TRANSICIONES[actual]:
            raise ErrorReferencia(f"no se puede pasar de '{actual}' a '{nuevo}'")
        if nuevo == "aprobada":
            probado = con.execute(
                select(func.count()).select_from(auditoria).where(
                    auditoria.c.entidad == "regla_conjunto", auditoria.c.entidad_id == conjunto_id, auditoria.c.accion == "prueba_sombra"
                )
            ).scalar()
            if not probado:
                raise ErrorReferencia("no se puede aprobar sin una prueba en sombra (validador reglas probar)")
            if cuatro_ojos and fila["creado_por"] == usuario:
                raise ErrorReferencia("la aprueba alguien distinto de quien la creó (aprobacion_cuatro_ojos)")
        if nuevo == "vigente":
            anteriores = con.execute(
                select(regla_conjunto.c.id).where(regla_conjunto.c.nombre == fila["nombre"], regla_conjunto.c.estado == "vigente")
            ).scalars().all()
            for anterior in anteriores:
                con.execute(update(regla_conjunto).where(regla_conjunto.c.id == anterior).values(estado="retirada"))
                _auditar(con, usuario, "regla_conjunto", anterior, "retirada", {"reemplazada_por": conjunto_id})
        con.execute(update(regla_conjunto).where(regla_conjunto.c.id == conjunto_id).values(estado=nuevo))
        accion = {"en_prueba": "prueba_sombra"}.get(nuevo, nuevo)
        _auditar(con, usuario, "regla_conjunto", conjunto_id, accion, {"desde": actual, **(detalle or {})})


def historia_regla(engine: Engine, regla_id: str) -> list[dict]:
    with engine.connect() as con:
        filas = con.execute(
            select(
                regla.c.conjunto_id, regla_conjunto.c.version.label("conjunto_version"), regla_conjunto.c.estado,
                regla.c.version, regla.c.vigente_desde, regla.c.vigente_hasta, regla.c.activa, regla.c.norma,
            )
            .join(regla_conjunto, regla.c.conjunto_id == regla_conjunto.c.id)
            .where(regla.c.regla_id == regla_id)
            .order_by(regla.c.conjunto_id)
        ).mappings()
        return [dict(f) for f in filas]


# --- catálogos ------------------------------------------------------------------------------------


def guardar_catalogo(
    engine: Engine, res: ResultadoImportacionCatalogo, usuario: str, etiqueta: str | None, fuente: str | None,
    vigente_desde: date | None, vigente_hasta: date | None,
) -> tuple[int, bool]:
    with engine.begin() as con:
        existente = con.execute(
            select(catalogo_version.c.id).where(catalogo_version.c.nombre == res.nombre, catalogo_version.c.sha256 == res.sha256)
        ).scalar_one_or_none()
        if existente is not None:
            return existente, False
        version_id = con.execute(
            insert(catalogo_version).values(
                nombre=res.nombre, etiqueta=etiqueta, fuente=fuente, archivo=res.archivo, sha256=res.sha256,
                vigente_desde=vigente_desde, vigente_hasta=vigente_hasta, items=len(res.items), avisos=res.avisos[:200],
                importado_en=ahora(), importado_por=usuario,
            )
        ).inserted_primary_key[0]
        con.execute(
            insert(catalogo_item),
            [{"version_id": version_id, "codigo": it.codigo, "descripcion": it.descripcion, "atributos": it.atributos} for it in res.items],
        )
        _auditar(con, usuario, "catalogo", version_id, "importado", {"nombre": res.nombre, "items": len(res.items), "archivo": res.archivo})
    return version_id, True


def listar_catalogos(engine: Engine) -> list[dict]:
    with engine.connect() as con:
        filas = con.execute(
            select(
                catalogo_version.c.id, catalogo_version.c.nombre, catalogo_version.c.etiqueta, catalogo_version.c.items,
                catalogo_version.c.vigente_desde, catalogo_version.c.vigente_hasta, catalogo_version.c.importado_en,
            ).order_by(catalogo_version.c.nombre, catalogo_version.c.id)
        ).mappings()
        return [dict(f) for f in filas]


def cargar_catalogos(engine: Engine, nombres: set[str] | None = None) -> Catalogos:
    """Carga todas las versiones (o solo las de ``nombres``). Son pocas y la elección por
    fecha se hace en memoria para cada factura."""
    catalogos = Catalogos()
    with engine.connect() as con:
        q = select(catalogo_version)
        if nombres:
            q = q.where(catalogo_version.c.nombre.in_({n.upper() for n in nombres}))
        for v in con.execute(q).mappings().all():
            items = {
                f["codigo"]: ItemCatalogo(f["codigo"], f["descripcion"], f["atributos"] or {})
                for f in con.execute(select(catalogo_item).where(catalogo_item.c.version_id == v["id"])).mappings()
            }
            catalogos.agregar(VersionCatalogo(v["nombre"], v["id"], v["vigente_desde"], v["vigente_hasta"], items, v["etiqueta"]))
    return catalogos


# --- contratos ---------------------------------------------------------------------------------------


def guardar_contrato(engine: Engine, contrato: Contrato, sha: str, usuario: str) -> tuple[int, bool]:
    """Versión nueva del contrato (mismo NIT y número). La anterior queda inactiva."""
    with engine.begin() as con:
        mismos = (contrato_version.c.eps_nit == contrato.eps_nit) & (
            contrato_version.c.numero_contrato.is_(None) if contrato.numero_contrato is None
            else contrato_version.c.numero_contrato == contrato.numero_contrato
        )
        existente = con.execute(select(contrato_version.c.id).where(mismos, contrato_version.c.sha256 == sha)).scalar_one_or_none()
        if existente is not None:
            return existente, False
        version = (con.execute(select(func.max(contrato_version.c.version)).where(mismos)).scalar() or 0) + 1
        anteriores = con.execute(select(contrato_version.c.id).where(mismos, contrato_version.c.activo.is_(True))).scalars().all()
        con.execute(update(contrato_version).where(mismos).values(activo=False))
        contrato_id = con.execute(
            insert(contrato_version).values(
                eps_nit=contrato.eps_nit, eps_nombre=contrato.eps_nombre, numero_contrato=contrato.numero_contrato, version=version,
                vigente_desde=contrato.vigencia.desde, vigente_hasta=contrato.vigencia.hasta, activo=True, sha256=sha,
                contenido=contrato.model_dump(mode="json"), creado_en=ahora(), creado_por=usuario,
            )
        ).inserted_primary_key[0]
        _auditar(con, usuario, "contrato", contrato_id, "importado", {"version": version, "reemplaza": anteriores})
    return contrato_id, True


def listar_contratos(engine: Engine) -> list[dict]:
    with engine.connect() as con:
        filas = con.execute(
            select(
                contrato_version.c.id, contrato_version.c.eps_nit, contrato_version.c.eps_nombre, contrato_version.c.numero_contrato,
                contrato_version.c.version, contrato_version.c.vigente_desde, contrato_version.c.vigente_hasta, contrato_version.c.activo,
            ).order_by(contrato_version.c.eps_nit, contrato_version.c.id)
        ).mappings()
        return [dict(f) for f in filas]


def cargar_contratos(engine: Engine) -> Contratos:
    with engine.connect() as con:
        filas = con.execute(select(contrato_version).where(contrato_version.c.activo.is_(True))).mappings().all()
    return Contratos([(Contrato.model_validate(f["contenido"]), f["id"]) for f in filas])


# --- motor listo para usar ---------------------------------------------------------------------------


def cargar_motor(engine: Engine, conjunto_ids: list[int] | None = None) -> MotorReglas | None:
    """Motor con los conjuntos vigentes (o los indicados, en cualquier estado, para pruebas),
    todos los catálogos y los contratos activos. None si no hay ninguna regla cargada."""
    with engine.connect() as con:
        if conjunto_ids:
            ids = conjunto_ids
        else:
            ids = con.execute(select(regla_conjunto.c.id).where(regla_conjunto.c.estado == "vigente")).scalars().all()
    if not ids:
        return None
    conjuntos = [ConjuntoCargado(obtener_conjunto(engine, i)[0], i) for i in ids]
    return MotorReglas(conjuntos, cargar_catalogos(engine), cargar_contratos(engine))
