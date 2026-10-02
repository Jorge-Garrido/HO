# Plan: todos los parámetros en la base de datos y simulacro de EPS nueva

Estado: **plan, no implementado** (2026-10-02). Sale de la prueba de generalización con
COOSALUD y FAMISANAR (5.812 facturas de otra IPS; ver sección 6).

## 1. Objetivo

1. Que **todo lo que cambia entre EPS, IPS o formatos** sea dato versionado en la base, con
   alcance, vigencia, auditoría y prueba en sombra, como ya lo son las reglas normativas, los
   catálogos y los contratos (`ARQUITECTURA_REGLAS.md`). El código solo contiene algoritmos
   genéricos.
2. Poder **"borrar" los parámetros de una EPS que ya conocemos** y volver a incorporarla como si
   fuera nueva, para medir cuánto tarda y qué tan bien la incorpora el agente, sin perder
   nada ni tocar producción.

## 2. Qué sigue hoy en código o configuración y a dónde va

| Parámetro | Hoy | Pasa a |
|---|---|---|
| Alias de soportes (`HISTORIA`→HEV, `AUTO`→PDE…) | `config.alias_soportes` | `parametro` tipo `alias_soporte` |
| Patrón del número de factura (`FE[A-Z]{0,3}\d+`) | `config.patron_factura` | `parametro` tipo `patron_factura` (por prestador) |
| Archivos a ignorar | `config.ignorar_archivos` | `parametro` tipo `ignorar_archivo` |
| Etiquetas del localizador (`NOMBRE DEL PACIENTE`, `CUFE`…) | `localizar.ETIQUETAS` | `parametro` tipo `etiqueta_campo` |
| Palabras que cortan un nombre, tipos de documento, alias PEP/PPT | `localizar`, `normalize` | `parametro` tipo `vocabulario` |
| Confusiones típicas del OCR (6/8, 0/Q…) | `compare.CONFUSIONES_OCR` | `parametro` tipo `confusion_ocr` |
| Zona del número en cada formato de autorización | no existe | `parametro` tipo `zona_ocr` (por formato) |
| Severidad y comparador por campo (`ReglaCampo`) | `config.campos` | `parametro` tipo `politica_campo` |
| Severidades de líneas, HC, OCR | `config.lineas_si_*`, `si_*` | `parametro` tipo `politica` |
| Códigos DIAN/CUV recurrentes | `config.codigos_recurrentes` | catálogo `CODIGOS_VALIDACION` |
| Documentos esperados por tipo de servicio | `config.documentos_*` | `contrato.soportes_requeridos` |
| Prestadores del tarifario | `config.tarifario_nits_prestador` | `contrato.tarifa` / tarifario por prestador |
| Sinónimos de secciones de HC | reglas (ya en la base) | se quedan; ganan alcance por especialidad |

`config.py` queda solo con lo de infraestructura: URL de la base, rutas de caché, hilos y la
activación del OCR.

## 3. Modelo de datos propuesto

```
parametro_conjunto   (id, version, estado, sha256, contenido JSON, creado_por, base_id)
                     mismo ciclo de vida que regla_conjunto: borrador → en_prueba → aprobada → vigente
parametro            (conjunto_id, tipo, clave, valor JSON, alcance, alcance_id, vigente_desde, vigente_hasta)
formato_documento    (id, alcance, tipo_documento, huella, descripcion, ejemplos_anonimizados)
```

- **Alcance** de cada parámetro: `global` | `prestador:<nit>` | `eps:<nit>` | `formato:<id>`.
  Se resuelve del más específico al más general (formato > eps > prestador > global). Así,
  un alias que solo usa FAMISANAR no contamina a las demás.
- **Huella de formato:** las etiquetas de plantilla más frecuentes de un tipo de documento
  (p. ej. "HISTORIA DE OFTALMOLOGIA", "CARDENAS VISION S.A.S"). Permite reconocer el formato
  de un documento y aplicar sus parámetros sin depender del nombre del archivo. Se calcula
  solo con texto de plantilla, nunca con datos del paciente.
- **Auditoría y sombra** iguales a las reglas: nada se activa sin probarse contra lo vigente
  y sin aprobación.
- **Resolución en ejecución:** al empezar una corrida se carga la versión vigente; cada
  resultado de factura guarda `parametros_version_id`, como ya guarda reglas, catálogos y contrato.

## 4. Simulacro: probar una EPS conocida como si fuera nueva

No se borra nada (la base es inmutable y auditada). Se trabaja sobre un **escenario aislado**:

```bash
validador escenario crear simulacro-famisanar --sin-alcance eps:830003564 --sin-alcance prestador:900012660
#   copia la base; en la copia, retira todos los parámetros, contratos y reglas con ese alcance
#   (quedan solo los globales: como si la EPS nunca se hubiera visto)
validador procesar ~/datos/FAMISANAR --escenario simulacro-famisanar --por-lote
#   línea base "en frío": tasa de discrepancias, no evaluables, omitidos
validador agente incorporar simulacro-famisanar --lote ~/datos/FAMISANAR/JUNIO-A\ RADICAR
#   el agente de incorporación propone parámetros (alias, etiquetas, sinónimos, zonas OCR)
validador escenario comparar simulacro-famisanar --contra produccion
#   ¿llegó a la misma calidad que los parámetros actuales? ¿con qué cambios? ¿cuánto tardó?
validador escenario borrar simulacro-famisanar
```

Lo que mide el simulacro:
- **Calidad en frío:** % de facturas con discrepancias y % de reglas no evaluables, solo con parámetros globales.
- **Calidad tras el agente,** comparada con producción (meta: igual o mejor).
- **Cuántos parámetros propuso el agente** y cuántos coinciden con los que ya existían.
- **Cuántos huecos necesitaron código** (deberían tender a cero).
- **Tiempo de incorporación.**

Es también la prueba de aceptación de cada versión del agente: con EPS conocidas se sabe la
respuesta correcta.

## 5. Agente de incorporación (uso ocasional)

- **Se activa** por alertas de estructura del lote: archivos omitidos, aumento de no evaluables,
  tasa de discrepancias muy por encima de lo normal, o un mismo hallazgo en casi todas las
  facturas. También a mano, al incorporar una EPS o IPS nueva.
- **Trabaja solo con rasgos de plantilla:** líneas repetidas en ≥ 30 % de los documentos,
  posiciones, conteos y valores enmascarados (dígitos → 9). Nunca con nombres ni documentos de
  pacientes. Es la misma técnica usada a mano en la prueba del 2026-10-02.
- **Propone** una versión de `parametro_conjunto` y, si hace falta, de reglas. La sombra y la
  aprobación son humanas. Si el problema exige código, deja un informe para desarrollo.

## 6. Lo aprendido en la prueba que motiva este plan

| Hallazgo | Tipo de arreglo hecho | Con este plan sería |
|---|---|---|
| Soportes sin prefijo (`HISTORIA.pdf`, `AUTO.pdf`, `ORDEN N.pdf`) | alias en `config.py` | parámetros con alcance `eps` |
| HC de optometría con otros títulos (agudeza visual, tratamiento) | versión 2 de reglas | igual (ya en la base) |
| Nombre leído de prosa ("paciente quien asiste…") | código genérico | igual (algoritmo) + vocabulario como dato |
| Códigos internos recortados en el RIPS | código genérico | igual (algoritmo) |
| Tarifario de otra IPS aplicado por error | `config` | `contrato.tarifa` por prestador |
| Número cortado por el recorte del OCR | código genérico | igual + `zona_ocr` por formato |
| Lotes "ORIGINAL" y "A RADICAR" con las mismas facturas | pendiente | radicación como entidad: misma factura en dos lotes se compara, no se duplica |

## 7. Orden sugerido

1. **Regresión pendiente** de los dos últimos cambios (OCR por motor y autorizaciones múltiples) contra `linea-base-v0`.
2. Tabla `parametro` + resolución por alcance + migrar `config` y `localizar.ETIQUETAS` (con regresión: 149/149 idénticas).
3. Alembic (el esquema empieza a crecer) y `parametros_version_id` en los resultados.
4. Escenarios (`escenario crear/comparar/borrar`) y alertas de estructura por lote.
5. Huella de formato y `zona_ocr`; mejoras de OCR baratas (recorte más ancho al releer, página a 300 dpi).
6. Agente de incorporación y simulacro con FAMISANAR y COOSALUD como prueba de aceptación.
7. Radicación como entidad (versiones ORIGINAL / A RADICAR).
