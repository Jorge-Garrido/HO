"""Persistencia con SQLAlchemy Core.

El mismo código corre en SQLite (desarrollo) y Postgres (producción): basta con
cambiar ``VALIDADOR_DB_URL`` (p. ej. ``postgresql+psycopg://usuario@host/validador``).
En Postgres los resultados quedan en JSONB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from ..base.schemas import ResultadoFactura
from ..tarifario.importer import ItemTarifa, ResultadoImportacion
from ..tarifario.matcher import Tarifario

JsonType = JSON().with_variant(JSONB(), "postgresql")
metadata = MetaData()

corridas = Table(
    "corridas",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("inicio", DateTime(timezone=True), nullable=False),
    Column("fin", DateTime(timezone=True)),
    Column("version_pipeline", String(20), nullable=False),
    Column("resumen", JsonType),
)

resultados = Table(
    "resultados",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("corrida_id", String(64), ForeignKey("corridas.id"), nullable=False, index=True),
    Column("numero_factura", String(40), nullable=False, index=True),
    Column("cufe", String(96), index=True),
    Column("estado", String(10), nullable=False, index=True),
    Column("creado", DateTime(timezone=True), nullable=False),
    Column("resultado", JsonType, nullable=False),
    UniqueConstraint("corrida_id", "numero_factura"),
)

tarifario_version = Table(
    "tarifario_version",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("nombre", String(200), nullable=False),
    Column("archivo", String(300), nullable=False),
    Column("sha256", String(64), nullable=False, unique=True),
    Column("uvb_anio", Integer),
    Column("uvb_valor", Numeric(14, 2)),
    Column("cargado_en", DateTime(timezone=True), nullable=False),
    Column("avisos", JsonType),
)

tarifario_item = Table(
    "tarifario_item",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("version_id", Integer, ForeignKey("tarifario_version.id"), nullable=False),
    Column("anexo", String(100), nullable=False),
    Column("codigo_institucional", String(20), nullable=False),
    Column("cups", String(20), nullable=False),
    Column("descripcion", Text, nullable=False),
    Column("tarifa_uvb", Numeric(14, 4), nullable=False),
    Column("valor", Numeric(16, 2), nullable=False),
    Column("fila", Integer),
    UniqueConstraint("version_id", "anexo", "codigo_institucional"),
)
Index("ix_tarifario_item_version_cups", tarifario_item.c.version_id, tarifario_item.c.cups)


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def conectar(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    from . import referencia  # noqa: F401  registra las tablas de reglas, catálogos y contratos

    engine = create_engine(url, future=True)
    # Crea las tablas que falten; no altera las existentes (ver ARQUITECTURA_REGLAS.md, "Migraciones").
    metadata.create_all(engine)
    return engine


# --- tarifario ---------------------------------------------------------------


def guardar_tarifario(engine: Engine, res: ResultadoImportacion, nombre: str, uvb_anio: int | None) -> int:
    with engine.begin() as con:
        existente = con.execute(
            select(tarifario_version.c.id).where(tarifario_version.c.sha256 == res.sha256)
        ).scalar_one_or_none()
        if existente is not None:
            return existente
        version_id = con.execute(
            insert(tarifario_version).values(
                nombre=nombre,
                archivo=res.archivo,
                sha256=res.sha256,
                uvb_anio=uvb_anio,
                uvb_valor=res.uvb_valor,
                cargado_en=ahora(),
                avisos=res.avisos,
            )
        ).inserted_primary_key[0]
        con.execute(
            insert(tarifario_item),
            [
                {
                    "version_id": version_id,
                    "anexo": it.anexo,
                    "codigo_institucional": it.codigo_institucional,
                    "cups": it.cups,
                    "descripcion": it.descripcion,
                    "tarifa_uvb": it.tarifa_uvb,
                    "valor": it.valor,
                    "fila": it.fila,
                }
                for it in res.items
            ],
        )
    return version_id


def cargar_tarifario(engine: Engine, version_id: int | None = None) -> Tarifario | None:
    with engine.connect() as con:
        if version_id is None:
            version_id = con.execute(select(func.max(tarifario_version.c.id))).scalar_one_or_none()
        if version_id is None:
            return None
        filas = con.execute(select(tarifario_item).where(tarifario_item.c.version_id == version_id)).mappings()
        items = [
            ItemTarifa(
                anexo=f["anexo"],
                codigo_institucional=f["codigo_institucional"],
                cups=f["cups"],
                descripcion=f["descripcion"],
                tarifa_uvb=Decimal(f["tarifa_uvb"]),
                valor=Decimal(f["valor"]),
                fila=f["fila"],
            )
            for f in filas
        ]
    return Tarifario(items, version_id)


# --- corridas y resultados ---------------------------------------------------


def iniciar_corrida(engine: Engine, corrida_id: str, version: str) -> None:
    with engine.begin() as con:
        con.execute(insert(corridas).values(id=corrida_id, inicio=ahora(), version_pipeline=version))


def cerrar_corrida(engine: Engine, corrida_id: str, resumen: dict) -> None:
    with engine.begin() as con:
        con.execute(corridas.update().where(corridas.c.id == corrida_id).values(fin=ahora(), resumen=resumen))


def previos_por_cufe(engine: Engine, cufe: str, corrida_id: str) -> list[tuple[str, str]]:
    """(corrida_id, numero_factura) de corridas anteriores con el mismo CUFE."""
    with engine.connect() as con:
        filas = con.execute(
            select(resultados.c.corrida_id, resultados.c.numero_factura).where(
                resultados.c.cufe == cufe, resultados.c.corrida_id != corrida_id
            )
        ).all()
    return [(f[0], f[1]) for f in filas]


def guardar_resultado(engine: Engine, r: ResultadoFactura) -> None:
    with engine.begin() as con:
        con.execute(
            insert(resultados).values(
                corrida_id=r.corrida_id,
                numero_factura=r.factura,
                cufe=r.cufe,
                estado=r.estado,
                creado=ahora(),
                resultado=r.model_dump(mode="json"),
            )
        )
