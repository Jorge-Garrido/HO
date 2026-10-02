"""Motor de reglas: aplica los conjuntos de reglas vigentes al expediente de una factura.

Por cada regla el resultado es uno de:

- cumple
- falla          → un Hallazgo (tipo "norma") con la regla, la norma y el código de glosa
- no_aplica      → la regla no está vigente en la fecha, no es del tipo de servicio o su
                   condición ``cuando`` es falsa
- no_evaluable   → falta un dato, un catálogo o un contrato (queda en el resumen, no es error)
- reportada_muv  → falla, pero el CUV ya trae un código equivalente: no se duplica

En alcance "servicio" la regla se evalúa en cada servicio RIPS y produce un solo hallazgo que
lista los servicios que fallan. Precedencia del estado agregado:
falla > reportada_muv > no_evaluable > cumple > no_aplica.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..base.schemas import DocumentoExtraido, Hallazgo, ReferenciaRegla, ResumenReglas
from . import condicion, expediente
from .catalogos import Catalogos
from .contratos import Contratos
from .expediente import Contexto, Expediente
from .modelos import ConjuntoReglas, Regla
from .primitivas import REGISTRO, Evaluacion, Recursos

PRECEDENCIA = ["falla", "reportada_muv", "no_evaluable", "cumple", "no_aplica"]
MAX_DETALLES = 5


@dataclass
class ConjuntoCargado:
    conjunto: ConjuntoReglas
    version_id: int | None  # None: cargado desde archivo, sin base de datos


class MotorReglas:
    def __init__(
        self,
        conjuntos: list[ConjuntoCargado],
        catalogos: Catalogos | None = None,
        contratos: Contratos | None = None,
    ):
        self.conjuntos = conjuntos
        self.catalogos = catalogos or Catalogos()
        self.contratos = contratos or Contratos()

    def evaluar(self, docs: dict[str, DocumentoExtraido], presentes: dict[str, list[str]]) -> tuple[list[Hallazgo], ResumenReglas]:
        # Primero sin contrato, para conocer la fecha y la EPS; luego con el contrato vigente.
        exp = expediente.construir(docs, presentes)
        nit = exp.factura.get("factura.nit_eps")
        numero = exp.factura.get("factura.numero_contrato")
        encontrado = self.contratos.buscar(nit, numero, exp.fecha_referencia) if len(self.contratos) else None
        if encontrado is not None:
            exp = expediente.construir(docs, presentes, encontrado[0])

        recursos = Recursos(self.catalogos, exp.fecha_referencia)
        resumen = ResumenReglas(
            conjuntos={c.conjunto.nombre: c.version_id for c in self.conjuntos if c.version_id is not None},
            contrato_version_id=encontrado[1] if encontrado else None,
            fecha_referencia=exp.fecha_referencia,
        )
        por_estado: dict[str, list[str]] = defaultdict(list)
        hallazgos: list[Hallazgo] = []
        cuv = set(exp.factura.get("cuv.codigos") or [])
        for cargado in self.conjuntos:
            for regla in cargado.conjunto.reglas:
                if not regla.aplica_en(exp.fecha_referencia):
                    # Otra versión de la misma regla puede ser la vigente; se cuenta una vez.
                    if not any(o.id == regla.id and o.aplica_en(exp.fecha_referencia) for o in cargado.conjunto.reglas):
                        if regla.id not in por_estado["no_aplica"]:
                            por_estado["no_aplica"].append(regla.id)
                    continue
                estado, detalles = self._evaluar_regla(regla, exp, recursos)
                if estado == "falla" and cuv & set(regla.resultado.cuv_equivalente):
                    estado = "reportada_muv"
                por_estado[estado].append(regla.id)
                if estado == "no_evaluable":
                    resumen.no_evaluables[regla.id] = detalles[0] if detalles else ""
                if estado == "falla":
                    hallazgos.append(self._hallazgo(regla, cargado.version_id, detalles))
        resumen.por_estado = {e: sorted(por_estado[e]) for e in PRECEDENCIA if por_estado.get(e)}
        resumen.catalogos = dict(sorted(recursos.catalogos_usados.items()))
        return hallazgos, resumen

    def _evaluar_regla(self, regla: Regla, exp: Expediente, recursos: Recursos) -> tuple[str, list[str]]:
        primitiva = REGISTRO[regla.primitiva]
        params = primitiva.Params.model_validate(regla.params)
        if regla.alcance == "factura":
            contextos = [("", exp.factura)]
        elif exp.factura.get("factura.tipos_servicio") is None:
            return "no_evaluable", ["sin RIPS: no hay servicios que evaluar"]
        else:
            contextos = [
                (f"{ctx.get('servicio.tipo')} #{ctx.get('servicio.indice')} ({ctx.get('servicio.codigo') or 's/c'})", ctx)
                for ctx in exp.servicios
                if not regla.tipos_servicio or ctx.get("servicio.tipo") in regla.tipos_servicio
            ]
        estados: dict[str, list[str]] = defaultdict(list)
        for etiqueta, ctx in contextos:
            ev = self._evaluar_contexto(regla, primitiva, params, ctx, recursos)
            estados[ev.estado].append(f"{etiqueta}: {ev.detalle}" if etiqueta and ev.detalle else ev.detalle)
        for estado in PRECEDENCIA:
            if estado in estados:
                return estado, estados[estado]
        return "no_aplica", []

    @staticmethod
    def _evaluar_contexto(regla: Regla, primitiva, params, ctx: Contexto, recursos: Recursos) -> Evaluacion:
        if regla.cuando is not None:
            try:
                if not condicion.evaluar(regla.cuando, ctx):
                    return Evaluacion("no_aplica")
            except condicion.Indeterminado as e:
                return Evaluacion("no_evaluable", f"condición sin dato: {e.ruta}")
        return primitiva.evaluar(ctx, params, recursos)

    @staticmethod
    def _hallazgo(regla: Regla, conjunto_version_id: int | None, detalles: list[str]) -> Hallazgo:
        r = regla.resultado
        extra = f" (y {len(detalles) - MAX_DETALLES} más)" if len(detalles) > MAX_DETALLES else ""
        detalle = "; ".join(d for d in detalles[:MAX_DETALLES] if d) + extra
        return Hallazgo(
            tipo="norma",
            resultado=r.severidad,
            motivo=f"{r.mensaje} [{detalle}]" if detalle else r.mensaje,
            documento_sospechoso=r.documento,
            regla=ReferenciaRegla(
                id=regla.id,
                version=regla.version,
                conjunto_version_id=conjunto_version_id,
                tipo_resultado=r.tipo,
                codigo_glosa=r.codigo_glosa,
                norma=str(regla.norma),
                verificar=r.verificar,
                accion=r.accion,
            ),
        )
