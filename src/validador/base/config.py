"""Configuración: umbrales, campos críticos, severidades y códigos recurrentes.

Todo lo que es regla de negocio vive aquí y no en el código. Los valores por
defecto se pueden sobrescribir con un archivo TOML indicado en la variable de
entorno ``VALIDADOR_CONFIG`` (mismas claves que el modelo ``Config``).
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Severidad = Literal["ok", "info", "aviso", "grave"]
Comparador = Literal["exacta", "tokens", "numerica", "fecha", "conjunto"]


class ReglaCampo(BaseModel):
    comparador: Comparador = "exacta"
    si_difiere: Severidad = "grave"
    si_falta_en_todas: Severidad = "info"


class ReglasTarifa(BaseModel):
    # Puntaje mínimo de similitud de descripción (0-1) para aceptar un ítem del tarifario.
    score_minimo: float = 0.5
    # Ventaja mínima del mejor candidato sobre el segundo cuando hay varios con el mismo CUPS.
    margen_minimo: float = 0.1
    # La base de tarifas actual es parcial (anexos institucionales), por eso no es grave.
    si_valor_difiere: Severidad = "aviso"
    # Diferencias de redondeo (la tarifa se redondea a la centena): no se reportan.
    tolerancia_pesos: int = 100
    si_no_determinable: Severidad = "info"
    si_no_en_tarifario: Severidad = "info"


class Config(BaseModel):
    # Orden de preferencia para desempatar y para el valor final.
    # El RIPS (JSON) lo genera el sistema del hospital: es la fuente principal.
    # El XML queda como respaldo cuando no hay RIPS.
    prioridad_fuentes: list[str] = [
        "rips",
        "xml",
        "pdf_factura",
        "cuv",
        "hc",
        "autorizacion_digital",
        "autorizacion_scan",
        "carpeta",
    ]

    campos: dict[str, ReglaCampo] = Field(
        default_factory=lambda: {
            "numero_factura": ReglaCampo(),
            "cufe": ReglaCampo(),
            "nit_emisor": ReglaCampo(),
            "nit_eps": ReglaCampo(),
            "paciente_id": ReglaCampo(),
            "paciente_nombre": ReglaCampo(comparador="tokens", si_difiere="aviso"),
            "numero_autorizacion": ReglaCampo(comparador="conjunto"),
            "id_interno_paciente": ReglaCampo(),
            "valor_total": ReglaCampo(comparador="numerica"),
            "fecha_ingreso": ReglaCampo(comparador="fecha", si_difiere="aviso"),
            "fecha_egreso": ReglaCampo(comparador="fecha", si_difiere="aviso"),
            "fecha_emision": ReglaCampo(comparador="fecha", si_difiere="aviso"),
        }
    )

    # Formatos válidos (regex sobre el valor normalizado). Si no cumple: grave.
    formatos: dict[str, str] = {
        "cufe": r"^[0-9a-f]{96}$",
        "numero_autorizacion": r"^\d{6,12}(,\d{6,12})*$",
        "nit_emisor": r"^\d{6,10}$",
        "nit_eps": r"^\d{6,10}$",
        "paciente_id": r"^[A-Z]{2,3} [0-9A-Z]{3,20}$",
    }

    # Documentos esperados por prefijo de archivo (nomenclatura de soportes).
    documentos_obligatorios: list[str] = ["FEV"]
    # Sin HEV es normal en apoyo diagnóstico (traen PDX): solo info.
    documentos_esperados: list[str] = ["HEV", "PDE"]
    # Algunas EPS (p. ej. COOSALUD) no piden RIPS ni CUV en el paquete: el XML hace de respaldo.
    si_falta_rips: Severidad = "info"
    si_falta_cuv: Severidad = "info"
    # Sin XML ni RIPS pero con la factura en PDF (p. ej. un PDF unificado): se valida con el
    # PDF, pero no se puede verificar el CUFE ante la DIAN.
    si_solo_pdf: Severidad = "aviso"

    # Códigos DIAN y CUV que aparecen en casi todas las facturas: info con contador.
    codigos_recurrentes: dict[str, Severidad] = {
        "FAJ43b": "info",
        "RUT01": "info",
        "FED137": "info",
    }
    # Clases del CUV que se consideran rechazo.
    cuv_clases_rechazo: list[str] = ["RECHAZADO"]
    # Respuesta DIAN de documento validado.
    dian_codigos_aceptado: list[str] = ["02"]

    tarifa: ReglasTarifa = ReglasTarifa()

    # Si el valor de una línea difiere entre fuentes digitales.
    lineas_si_codigo_falta: Severidad = "grave"
    lineas_si_valor_difiere: Severidad = "grave"
    lineas_si_cantidad_difiere: Severidad = "aviso"
    lineas_si_fecha_fuera_periodo: Severidad = "aviso"

    hc_si_tipo_consulta_difiere: Severidad = "aviso"
    hc_si_fecha_fuera_periodo: Severidad = "aviso"

    # Campos de los PDF digitales: "generica" (localizador por posiciones + diccionario de
    # etiquetas, sin plantillas) o "plantilla" (parsers específicos de este hospital).
    extraccion_pdf: Literal["generica", "plantilla"] = "generica"

    # OCR de la autorización escaneada (cascada PP-OCR + Tesseract).
    ocr_habilitado: bool = True
    ocr_cache_dir: str = "salidas/ocr_cache"
    # El documento de la EPS es la autoridad para ciertos campos: si su lectura es confiable,
    # su valor es la referencia aunque las fuentes digitales (del mismo sistema) digan otra cosa.
    fuente_autoridad: dict[str, str] = {"numero_autorizacion": "autorizacion_scan"}
    min_confianza_autoridad: float = 0.85
    cups_no_en_autorizacion: Severidad = "aviso"
    # Solo el escaneo difiere, con lectura de un solo motor, y las digitales coinciden: casi
    # siempre es error de OCR. El plan decía "aviso"; se deja "info" para no llenar la revisión
    # de ruido. Si dos motores confirman la lectura distinta, es "aviso".
    si_ocr_incierto_difiere: Severidad = "info"
    autorizacion_no_legible: Severidad = "aviso"

    db_url: str = "sqlite:///salidas/validador.db"


def _cargar() -> Config:
    ruta = os.environ.get("VALIDADOR_CONFIG")
    datos: dict = {}
    if ruta:
        datos = tomllib.loads(Path(ruta).read_text(encoding="utf-8"))
    if url := os.environ.get("VALIDADOR_DB_URL"):
        datos["db_url"] = url
    return Config.model_validate(datos)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return _cargar()
