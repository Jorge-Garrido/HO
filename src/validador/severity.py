"""Matriz de severidad (sección 6 del plan). Las reglas vienen de config.py."""

from __future__ import annotations

import re

from .attribution import votar
from .compare import distancia_digitos, iguales
from .base.config import Config, Severidad
from .base.schemas import CampoExtraido, ResultadoCampo

ORDEN: dict[str, int] = {"ok": 0, "info": 1, "aviso": 2, "grave": 3}
FUENTES_OCR = {"autorizacion_scan"}


def peor(*resultados: Severidad) -> Severidad:
    return max(resultados, key=lambda r: ORDEN[r], default="ok")


def _por_prioridad(valores: dict[str, str], cfg: Config) -> str | None:
    for fuente in cfg.prioridad_fuentes:
        if fuente in valores:
            return valores[fuente]
    return next(iter(valores.values()), None)


def _recortado(parcial: str, completo: str) -> bool:
    """'7196996' es '17196996' sin sus primeros dígitos: el recorte de la imagen cortó el borde
    izquierdo. Solo el inicio: un dígito de más al final en el RIPS es un error de digitación
    que la autorización de la EPS debe señalar (ver test_ocr)."""
    return 1 <= len(completo) - len(parcial) <= 2 and completo.endswith(parcial)


def evaluar_campo(campo: str, extraidos: list[CampoExtraido], cfg: Config) -> ResultadoCampo:
    regla = cfg.campos[campo]
    valores = {e.fuente: e.valor for e in extraidos if e.valor}
    base = {"campo": campo, "valores_por_fuente": dict(valores)}

    if not valores:
        return ResultadoCampo(
            valor_final=None, resultado=regla.si_falta_en_todas, motivo="no encontrado en ninguna fuente", **base
        )

    patron = cfg.formatos.get(campo)
    # Una lectura de OCR con formato imposible es ruido de lectura, no un dato del documento.
    for f in [f for f in valores if f in FUENTES_OCR and patron and not re.fullmatch(patron, valores[f])]:
        del valores[f]
    confianza_ocr = {e.fuente: e.confianza for e in extraidos if e.fuente in FUENTES_OCR}
    invalidos = [f for f, v in valores.items() if patron and not re.fullmatch(patron, v)]
    validos = {f: v for f, v in valores.items() if f not in invalidos}

    # Fuente con autoridad (p. ej. el escaneo de la EPS para la autorización) leída con confianza.
    autoridad = cfg.fuente_autoridad.get(campo)
    ext_autoridad = next((e for e in extraidos if e.fuente == autoridad and e.valor), None)
    if ext_autoridad is not None and ext_autoridad.confianza >= cfg.min_confianza_autoridad and len(valores) > 1:
        ref = ext_autoridad.valor
        distintos = [f for f, v in valores.items() if f != autoridad and not iguales(regla.comparador, v, ref)]
        base_etapa = {"etapa_resolucion": ext_autoridad.etapa, "confianza": ext_autoridad.confianza}
        if not distintos:
            return ResultadoCampo(valor_final=ref, resultado="ok", motivo="coincide con el documento de la EPS", **base, **base_etapa)
        if regla.comparador == "conjunto":
            # Autorizaciones repartidas entre documentos (el RIPS trae una por servicio, el PDF y
            # el escaneo muestran una cada uno): si todo cabe en el conjunto mayor, no hay contradicción.
            conjuntos = {f: set(v.split(",")) for f, v in valores.items()}
            mayor = max(conjuntos.values(), key=len)
            if all(c <= mayor for c in conjuntos.values()) and len(mayor) > 1:
                parciales = [f for f, c in conjuntos.items() if c != mayor]
                return ResultadoCampo(
                    valor_final=",".join(sorted(mayor)), resultado=regla.si_subconjunto, documento_sospechoso=",".join(parciales),
                    motivo=f"{', '.join(parciales)} trae solo parte de las autorizaciones ({','.join(sorted(mayor))}); no hay valores contradictorios",
                    **base, **base_etapa,
                )
        digitales = {valores[f] for f in distintos}
        if len(digitales) == 1 and len(distintos) == len(valores) - 1:
            digital = next(iter(digitales))
            if regla.comparador == "conjunto" and set(ref.split(",")) < set(digital.split(",")):
                # El escaneo trae una de las autorizaciones del RIPS (una por servicio).
                return ResultadoCampo(
                    valor_final=digital, resultado=regla.si_subconjunto, documento_sospechoso=autoridad,
                    motivo=f"el documento de la EPS trae {ref}, una parte de {digital}: verificar el soporte de las demás",
                    **base, **base_etapa,
                )
            if "," not in digital and _recortado(ref, digital):
                # El escaneo lee el valor digital sin sus primeros dígitos: probablemente el recorte
                # cortó el número. No se toma como evidencia contra las fuentes digitales.
                return ResultadoCampo(
                    valor_final=digital, resultado=cfg.si_escaneo_recortado, documento_sospechoso=autoridad,
                    motivo=f"el escaneo parece cortado al inicio (lee {ref}; las fuentes digitales coinciden en {digital}); verificar a ojo",
                    distancia=distancia_digitos(ref, digital), **base, **base_etapa,
                )
        distancia = min(
            (distancia_digitos(ref, valores[f]) for f in distintos if "," not in valores[f] and "," not in ref), default=None
        )
        return ResultadoCampo(
            valor_final=ref,
            resultado=regla.si_difiere,
            motivo=f"el documento de la EPS dice {ref}; difiere: {', '.join(distintos)}",
            documento_sospechoso=",".join(distintos),
            distancia=distancia,
            **base,
            **base_etapa,
        )

    if regla.comparador == "conjunto" and len(validos) > 1 and not invalidos:
        conjuntos = {f: set(v.split(",")) for f, v in validos.items()}
        mayor = max(conjuntos.values(), key=len)
        if len({frozenset(c) for c in conjuntos.values()}) > 1 and all(c <= mayor for c in conjuntos.values()):
            parciales = [f for f, c in conjuntos.items() if c != mayor]
            completas = [f for f, c in conjuntos.items() if c == mayor]
            return ResultadoCampo(
                valor_final=",".join(sorted(mayor)),
                resultado=regla.si_subconjunto,
                motivo=f"{', '.join(parciales)} trae solo parte de los valores de {', '.join(completas)} (no hay valores contradictorios)",
                documento_sospechoso=",".join(parciales),
                **base,
            )

    voto = votar(valores, regla.comparador)
    if voto.valor_mayoria is not None and not voto.sospechosos:
        if invalidos:
            return ResultadoCampo(
                valor_final=voto.valor_mayoria,
                resultado="grave",
                motivo=f"formato inválido en todas las fuentes ({', '.join(invalidos)})",
                **base,
            )
        motivo = "coincide en todas las fuentes" if len(valores) > 1 else "una sola fuente"
        return ResultadoCampo(valor_final=voto.valor_mayoria, resultado="ok", motivo=motivo, **base)

    if voto.empate:
        final = _por_prioridad(validos or valores, cfg)
        return ResultadoCampo(
            valor_final=final,
            resultado="grave" if regla.si_difiere == "grave" else regla.si_difiere,
            motivo="las fuentes discrepan sin mayoría; requiere revisión humana",
            **base,
        )

    sospechosos = voto.sospechosos
    distancia = None
    if regla.comparador in ("exacta", "conjunto") and len(sospechosos) == 1:
        v = valores[sospechosos[0]]
        if "," not in v and "," not in voto.valor_mayoria:
            distancia = distancia_digitos(voto.valor_mayoria, v)

    if set(sospechosos) <= FUENTES_OCR:
        # Las fuentes digitales coinciden y solo el escaneo difiere. Si la lectura del escaneo
        # es de un solo motor, es probablemente un error de OCR (info); si dos motores la
        # confirman, el escaneo realmente dice otra cosa (aviso).
        confiable = all(confianza_ocr.get(f, 0) >= cfg.min_confianza_autoridad for f in sospechosos)
        resultado: Severidad = "aviso" if confiable else cfg.si_ocr_incierto_difiere
        motivo = (
            "el escaneo dice otro valor (confirmado por dos lecturas) y las fuentes digitales coinciden entre sí"
            if confiable else "el escaneo difiere pero las fuentes digitales coinciden (probable error de OCR)"
        )
    else:
        resultado = regla.si_difiere
        motivo = f"{', '.join(sospechosos)} difiere de la mayoría"
        if distancia is not None:
            motivo += f" (distancia {distancia})"
    if any(f in invalidos for f in sospechosos):
        resultado = "grave"
        motivo += "; formato inválido"
    if mayoria_invalida := [f for f in invalidos if f not in sospechosos]:
        resultado = "grave"
        motivo += f"; el valor de la mayoría tiene formato inválido ({', '.join(mayoria_invalida)})"
    return ResultadoCampo(
        valor_final=voto.valor_mayoria,
        resultado=resultado,
        motivo=motivo,
        documento_sospechoso=",".join(sospechosos),
        distancia=distancia,
        **base,
    )
