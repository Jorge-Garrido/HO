# Motor de reglas normativas: arquitectura, base de datos y versiones

Documento de referencia para desarrollo y mantenimiento. Explica cómo se guardan las reglas,
los catálogos y los contratos, cómo cambian sin tocar código, cómo se prueba un cambio antes de
ponerlo en producción y qué queda registrado de cada factura validada.

Versión del pipeline: 0.2.0 · Esquema de resultados: 2 · Línea base anterior: tag git `linea-base-v0`.

---

## 1. Qué cambió y por qué

Hasta la versión 0.1 el validador comparaba documentos entre sí (RIPS, XML, PDF, HC,
autorización): ¿cuadran factura, paciente, autorización, líneas y valores? Las reglas vivían en
código (`pipeline.py`) y en `config.py`.

La matriz normativa (`SEMCOL_Consulta_Validaciones_Normativas.xlsx`) pide algo más: validar el
**contenido** contra la norma (Res. 948/2026, Res. 2284/2023) y decir **qué glosa** produciría
cada falla. Como las normas cambian, esas reglas no pueden estar en código.

La versión 0.2 agrega un **motor de reglas normativas** en el que:

- las reglas, los catálogos (CIE-10, CUPS, tablas SISPRO) y los contratos con cada EPS son
  **datos versionados en la base**, con vigencia legal;
- un cambio de ley es una **versión nueva de datos**, probada en sombra sobre facturas reales y
  aprobada por una persona, no un despliegue de código;
- cada factura guarda **qué versión** de reglas, catálogos y contrato se le aplicó.

El cruce entre documentos (votación, OCR, tarifario) **no cambió**: la comparación contra la
línea base da resultados idénticos en las 149 facturas de muestra (sección 12).

---

## 2. Capas: qué es código y qué es dato

```
┌──────────────────────────── CÓDIGO (cambia con PR, pruebas y despliegue) ────────────────────────────┐
│ ingest/*            Extractores: RIPS, XML, PDF, HC, autorización (OCR)                               │
│ reglas/expediente   Vista canónica de la factura: RUTAS que las reglas pueden leer                    │
│ reglas/primitivas   Operaciones genéricas: requerido, comparar, rango, en_catalogo, relacion_catalogo…│
│ reglas/condicion    Lenguaje de condiciones (JSONLogic reducido, sin eval)                            │
│ reglas/motor        Aplica reglas → hallazgos con norma y glosa                                       │
│ severity/attribution/compare   Cruce entre documentos (sin cambios)                                   │
└───────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────────────── DATOS EN LA BASE (versionados, con vigencia y auditoría) ─────────────────┐
│ regla_conjunto / regla   Reglas normativas: primitiva + parámetros + condición + vigencia + cita      │
│ catalogo_version / item  CIE10, CUPSRIPS, tablas SISPRO, relaciones propias, glosas 2284              │
│ contrato_version         Parámetros del acuerdo con cada EPS (autorización, orden, tarifa…)           │
│ tarifario_version / item Tarifario institucional (ya existía)                                         │
│ auditoria                Quién creó, probó, aprobó, activó o retiró qué y cuándo                       │
└───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Necesidad | ¿Código o dato? | Quién |
|---|---|---|
| Cambiar severidad, mensaje, glosa o vigencia de una regla | Dato | Mantenimiento |
| Nueva regla con primitivas y rutas existentes | Dato | Mantenimiento o agente + aprobación |
| Derogar una regla desde una fecha | Dato | Mantenimiento o agente + aprobación |
| Nueva versión de CIE-10, CUPS o una tabla SISPRO | Dato | Mantenimiento |
| Nuevo contrato o cambio de condiciones con una EPS | Dato | Facturación + mantenimiento |
| Leer un dato que hoy no se extrae (p. ej. el profesional en la HC) | Código: extractor + entrada en `RUTAS` | Desarrollo |
| Nueva operación (p. ej. similitud semántica, cálculo SOAT) | Código: primitiva o plugin | Desarrollo |
| Regla que solo una EPS tiene y no cabe en parámetros | Código: plugin activado por contrato | Desarrollo |

---

## 3. Flujo de una factura

```
carpeta FE…/
   │
   ▼
[A] Extracción (ingest/*)  ──►  DocumentoExtraido por fuente
   │                            (rips.datos y hc.texto quedan en memoria, nunca se guardan)
   ▼
[B] Cruce entre documentos  ──►  campos[], lineas[], hallazgos[] (documentos, DIAN, CUV, HC)
   │  severity.py, attribution.py, tarifario
   ▼
[C] Motor de reglas (nuevo) ──►  hallazgos[] tipo "norma" con regla, norma y glosa
   │  1. expediente.construir(docs)        → rutas: factura.*, usuario.*, servicio.*, hc.*…
   │  2. fecha_referencia = primera atención (decide vigencias)
   │  3. contrato = el de la EPS (NIT) vigente en esa fecha, si hay
   │  4. por cada regla vigente: condición → primitiva → cumple/falla/no_evaluable/…
   ▼
ResultadoFactura (JSON + base) ── reglas: {conjuntos, catalogos, contrato_version_id, por_estado}
   │
   ▼
Reportes: REPORTE <EPS>.xlsx, _REVISION.txt, resumen.json (glosas potenciales por código)
```

La etapa C es opcional: `--sin-reglas`, o `reglas_habilitadas = false` en la configuración.

---

## 4. El expediente: lo que las reglas pueden leer

Las reglas nunca abren archivos. Leen **rutas** del expediente. La lista completa sale con
`validador reglas rutas` y vive en `reglas/expediente.py::RUTAS`.

| Prefijo | Contenido | Ejemplo |
|---|---|---|
| `factura.*` | XML/FEV: número, fechas, periodo, NIT, extensión sector salud | `factura.periodo_inicio`, `factura.modalidad_pago` |
| `rips.*` | Transacción del RIPS y listas completas | `rips.numFactura`, `rips.usuarios`, `rips.servicios.consultas` |
| `usuario.*` | Campo del usuario con el nombre del DT1 | `usuario.codSexo`, `usuario.fechaNacimiento` |
| `servicio.*` | Campo del servicio con el nombre del DT1 + `tipo`, `codigo`, `fecha` | `servicio.codConsulta`, `servicio.fecha` |
| `derivado.*` | Calculado en código | `derivado.edad_anios`, `derivado.requiere_autorizacion` |
| `documentos.presentes` | Prefijos de soportes en la carpeta | `HEV`, `PDE`, `OPF` |
| `hc.*` | Historia clínica digital | `hc.texto`, `hc.digital` |
| `cuv.codigos` | Códigos del Mecanismo Único de Validación | `RVC028` |
| `contrato.*` | Parámetros del contrato vigente | `contrato.autorizacion.vigencia_dias` |

Los nombres RIPS se conservan como en el Documento Técnico 1, para que una regla escrita a
partir de la norma use el mismo nombre que la norma.

**Privacidad:** `describir_valor` nunca muestra en un hallazgo valores de `usuario.*` (fecha de
nacimiento, documento); muestra el nombre del campo. `DocumentoExtraido.datos` y `.texto`
están marcados `exclude=True` y no se serializan (hay una prueba que lo verifica).

---

## 5. Anatomía de una regla

```yaml
- id: RIPS-FECHA-PERIODO            # único; varias versiones pueden convivir (ver vigencia)
  version: 1
  nombre: Fecha de atención dentro del periodo facturado
  matriz: [CONS-004]                # ids de la matriz SEMCOL que cubre (reporte de cobertura)
  norma: {documento: "Res. 948/2026 DT1", numeral: "§4.3.1 C02", pagina: "28", texto_literal: "…"}
  vigencia: {desde: 2026-05-14, hasta: null}   # se compara con la fecha de ATENCIÓN
  alcance: servicio                 # factura: una vez · servicio: en cada servicio RIPS
  tipos_servicio: []                # vacío = todos (consultas, procedimientos, …)
  cuando: null                      # condición opcional (sección 5.3)
  primitiva: rango                  # operación genérica (sección 5.1)
  params: {valor: servicio.fecha, desde: factura.periodo_inicio, hasta: factura.periodo_fin, tipo: fecha}
  resultado:
    tipo: rechazo_muv               # rechazo_muv | notificacion | validacion | glosa_potencial
    severidad: grave                # ok | info | aviso | grave
    codigo_glosa: null              # código del manual de glosas (Res. 2284 Anexo 3)
    cuv_equivalente: []             # si el CUV ya trae este código, no se duplica
    documento: rips                 # documento sospechoso
    mensaje: La fecha de atención del RIPS está fuera del periodo de facturación del XML.
    verificar: "RIPS: fechaInicioAtencion | XML: cac:InvoicePeriod"
    accion: Corregir la fecha en el RIPS o el periodo de la factura.
  activa: true
```

El modelo `reglas/modelos.py::Regla` valida todo al importar: la primitiva existe, sus
parámetros son válidos, la condición está bien formada y las rutas existen. Una regla inválida
no entra a la base.

### 5.1 Primitivas disponibles

`validador reglas primitivas` las lista con sus parámetros.

| Primitiva | Qué verifica | Ejemplo de uso |
|---|---|---|
| `requerido` | Los campos tienen valor | numAutorizacion si el contrato la exige |
| `nulo` | Los campos vienen vacíos | numFEVPagoModerador si no hay moderador |
| `formato` | Expresión regular | incapacidad ∈ {SI, NO} |
| `comparar` | Campo vs campo o valor (`==`, `<=`, `empieza_por`, `contiene`…) | nacimiento ≤ atención; codPrestador empieza por CODIGO_PRESTADOR |
| `rango` | Campo entre dos límites | fecha de atención dentro de InvoicePeriod |
| `distintos` | Sin repetidos | diagnósticos relacionados ≠ principal |
| `consecutivo` | 1..n sin saltos (por usuario) | consecutivo de consultas |
| `en_catalogo` | Código existe en el catálogo vigente | CUPS en CUPSRips, CIE-10 |
| `relacion_catalogo` | Atributos del ítem vs expediente | CIE-10 vs sexo y edad; tipoUsuario vs cobertura |
| `documento_presente` | Soporte en la carpeta | HEV para consulta, OPF sin autorización |
| `secciones_presentes` | Secciones en el texto del soporte | contenido mínimo de la HC (2284 Anexo 1) |

### 5.2 Resultado de evaluar una regla

| Estado | Significado | ¿Hallazgo? |
|---|---|---|
| `falla` | La factura incumple | Sí, tipo `norma`, con regla, norma y glosa |
| `reportada_muv` | Falla, pero el CUV ya trae un código equivalente | No (el Ministerio ya lo validó) |
| `no_evaluable` | Falta un dato, un catálogo o el contrato | No; queda en `reglas.no_evaluables` con el motivo |
| `cumple` | Cumple | No |
| `no_aplica` | No vigente en la fecha, otro tipo de servicio o `cuando` falso | No |

En alcance `servicio`, si un servicio falla, la regla falla (un solo hallazgo que lista los
servicios). Precedencia: falla > reportada_muv > no_evaluable > cumple > no_aplica.

**"no_evaluable" nunca es un error de la factura.** Es la señal de qué hay que cargar o extraer:
`resumen.json → reglas_no_evaluables` cuenta cuántas facturas no se pudieron evaluar por regla.

### 5.3 Condiciones (`cuando`)

JSONLogic reducido y seguro (sin `eval`): `var`, `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`,
`and`, `or`, `!`, `presente`, `ausente`.

```yaml
cuando: {">": [{var: servicio.valorPagoModerador}, 0]}
cuando: {in: [consultas, {var: factura.tipos_servicio}]}
cuando: {and: [{"==": [{var: derivado.requiere_autorizacion}, false]},
               {"==": [{var: contrato.orden_medica.requerida_sin_autorizacion}, true]}]}
```

Lógica de tres valores: si la condición necesita un dato que no está, la regla queda
`no_evaluable`, nunca `falla`.

### 5.4 Vigencia y versiones de una regla

- La **vigencia** es legal: desde cuándo rige la obligación. Se compara con la **fecha de
  referencia** de la factura (la primera fecha de atención; sin RIPS, el inicio del periodo
  facturado; si no, la emisión).
- Una regla puede tener **varias versiones** en el mismo conjunto si sus vigencias no se cruzan.
  Así una norma nueva no cambia la validación de facturas atendidas antes:

```yaml
- {id: RIPS-X, version: 1, vigencia: {desde: 2024-01-01, hasta: 2026-05-13}, …}   # norma anterior
- {id: RIPS-X, version: 2, vigencia: {desde: 2026-05-14}, …}                     # norma nueva
```

---

## 6. Base de datos

SQLAlchemy Core. SQLite en desarrollo, PostgreSQL en producción (JSON → JSONB). Misma base para
todo: `VALIDADOR_DB_URL`.

### 6.1 Tablas

```
corridas ─┐                     tarifario_version ──< tarifario_item
          └──< resultados       (ya existían)

regla_conjunto ──< regla        catalogo_version ──< catalogo_item       contrato_version
      │ base_id (versión de la que parte)
      └──────────── auditoria (entidad, entidad_id) ──── también catálogos y contratos
```

| Tabla | Una fila es | Columnas clave |
|---|---|---|
| `corridas` | Una ejecución de `procesar` | id, inicio, fin, versión del pipeline, resumen (JSON) |
| `resultados` | El resultado de una factura en una corrida | corrida_id, numero_factura, cufe, estado, **resultado (JSON completo)** |
| `tarifario_version` / `tarifario_item` | Una importación del Excel de tarifas / un ítem | sha256, UVB · cups, descripción, valor |
| `regla_conjunto` | **Una versión** de un conjunto de reglas | nombre, version, **estado**, origen, base_id, sha256, **contenido (JSON del conjunto)**, creado_por |
| `regla` | Una regla dentro de una versión de conjunto | conjunto_id, regla_id, version, primitiva, vigente_desde/hasta, codigo_glosa, norma, contenido |
| `catalogo_version` | Una importación de un catálogo | nombre, etiqueta, fuente, sha256, **vigente_desde/hasta**, items |
| `catalogo_item` | Un código | version_id, codigo, descripcion, **atributos (JSON)** |
| `contrato_version` | Una versión del contrato con una EPS | eps_nit, numero_contrato, version, vigencia, **activo**, contenido (JSON) |
| `auditoria` | Un evento | fecha, **usuario**, entidad, entidad_id, accion, detalle (JSON) |

### 6.2 Principios

1. **Inmutable.** No existe función para editar una versión guardada. Corregir es crear otra.
2. **Sin duplicados.** Cada versión guarda el sha256 de su contenido canónico; importar dos
   veces lo mismo devuelve la versión existente.
3. **Trazable.** Cada versión sabe de cuál parte (`base_id`), de dónde vino (`origen`:
   archivo, agente o manual, más `origen_detalle`) y quién la creó.
4. **Auditado.** Toda transición queda en `auditoria` con usuario (`--usuario`,
   `VALIDADOR_USUARIO` o el usuario del sistema) y detalle (p. ej. el resumen de la sombra).
5. **Reproducible.** Cada `resultados.resultado.reglas` guarda:

```json
"reglas": {
  "conjuntos": {"normativa": 1},          // id de regla_conjunto usado
  "catalogos": {"CIE10": 4},              // id de catalogo_version usado
  "contrato_version_id": 2,
  "fecha_referencia": "2026-06-10",
  "por_estado": {"falla": ["HC-CONTENIDO-MINIMO"], "cumple": [...], "no_evaluable": [...]},
  "no_evaluables": {"CIE10-EN-CATALOGO": "catálogo CIE10 no cargado…"}
}
```

   y cada hallazgo normativo guarda `regla: {id, version, conjunto_version_id, tipo_resultado,
   codigo_glosa, norma, verificar, accion}`. Con eso, una glosa de la EPS se puede contrastar
   meses después con exactamente lo que el validador sabía ese día.

### 6.3 Consultas útiles (SQL)

```sql
-- Historia de una regla en todas las versiones
SELECT c.version, c.estado, r.version, r.vigente_desde, r.vigente_hasta, r.norma
FROM regla r JOIN regla_conjunto c ON c.id = r.conjunto_id WHERE r.regla_id = 'RIPS-FECHA-PERIODO';

-- Quién aprobó la versión vigente
SELECT a.fecha, a.usuario, a.accion, a.detalle FROM auditoria a
JOIN regla_conjunto c ON a.entidad = 'regla_conjunto' AND a.entidad_id = c.id
WHERE c.estado = 'vigente';

-- Glosas potenciales por código en una corrida (Postgres)
SELECT h->'regla'->>'codigo_glosa' AS glosa, count(*)
FROM resultados, jsonb_array_elements(resultado->'hallazgos') h
WHERE corrida_id = '…' AND h ? 'regla' GROUP BY 1;
```

### 6.4 Migraciones

`db.conectar()` ejecuta `metadata.create_all`, que crea las tablas que falten y **no altera las
existentes**. Las tablas de esta versión son todas nuevas, así que una base 0.1 se actualiza
sola al conectar. **Antes del primer despliegue a producción** hay que adoptar Alembic con una
migración base de este esquema; desde ahí, todo cambio de columnas va en una migración versionada
en git. Los resultados viejos (`schema_version: "1"`) siguen siendo legibles: los campos nuevos
son opcionales.

---

## 7. Ciclo de vida de un conjunto de reglas

```
            importar / proponer
                   │
                   ▼
   ┌─────────── borrador ───────────┐
   │               │ probar (sombra) │ retirar
   │               ▼                 ▼
   │           en_prueba ◄─┐     retirada
   │               │ probar┘         ▲
   │               │ aprobar         │
   │               ▼                 │ (al activar otra)
   │            aprobada ──activar──► vigente
   └────────────────────────────────────────┘
```

| Paso | Comando | Control |
|---|---|---|
| Crear versión | `validador reglas importar archivo.yaml` o `reglas proponer` | Validación completa del modelo y de las rutas |
| Prueba en sombra | `validador reglas probar <id> <carpetas…>` | Compara con la vigente sobre facturas reales; guarda un informe JSON y lo registra en la auditoría |
| Aprobar | `validador reglas aprobar <id> --nota "…"` | Exige estar en `en_prueba` (es decir, con sombra). Con `aprobacion_cuatro_ojos = true`, no puede aprobar quien creó |
| Activar | `validador reglas activar <id>` | Retira la vigente anterior en la misma transacción: siempre hay una sola vigente |
| Volver atrás | `validador reglas restaurar <id anterior>`: copia ese contenido como versión nueva (borrador, origen `restauracion`) y se repite sombra → aprobar → activar | Una versión retirada no se reactiva directamente: el retorno también queda probado y auditado |
| Consultar | `reglas listar`, `reglas mostrar <id>`, `reglas bitacora <id>`, `reglas historia <REGLA-ID>`, `reglas cobertura` | — |

`procesar` usa las versiones **vigentes**. Para validar con una versión no vigente (p. ej. para
mostrarle a auditoría el efecto completo en el Excel): `procesar … --reglas <id>`.

---

## 8. Catálogos

Un catálogo es una lista de códigos con descripción y **atributos libres**. El importador
(`reglas/catalogos.py`) acepta CSV o XLSX: la columna de código, la de descripción y todas las
demás pasan a atributos.

```bash
validador catalogo importar CIE10_SISPRO.xlsx --nombre CIE10 \
    --col-codigo Codigo --col-descripcion Nombre \
    --vigente-desde 2026-01-01 --etiqueta "SISPRO 2026-01" --fuente "web.sispro.gov.co tabla CIE10"
validador catalogo listar
```

Cada importación es una versión nueva (el mismo archivo dos veces no se duplica). En cada
factura se usa la versión vigente en su fecha de referencia, así que actualizar CUPS no cambia
facturas ya validadas.

### Ejemplos

**CIE-10 con restricciones de sexo y edad** (lo usa `CIE10-SEXO-EDAD`). Las columnas del
archivo deben llamarse `sexo`, `edad_minima` y `edad_maxima` (renombrarlas antes de importar si
la tabla oficial usa otros encabezados):

| codigo | descripcion | sexo | edad_minima | edad_maxima |
|---|---|---|---|---|
| N40X | Hiperplasia de la próstata | M | | |
| O800 | Parto único espontáneo | F | 10 | 60 |
| E119 | Diabetes mellitus tipo 2 | A | | |

`A` (ambos) y `Z` son comodines: no restringen. Atención: el RIPS usa `codSexo` F/M; la
tabla debe usar los mismos códigos.

**CUPSRIPS** (lo usan `CUPS-EN-CATALOGO` y `CUPS-SEXO`): `codigo`, `descripcion` y, si se
quiere validar sexo, `sexo`.

**Relación propia `TIPO_USUARIO_COBERTURA`** (la usa `TIPO-USUARIO-COBERTURA`, equivale a
RVC005). La mantiene facturación, porque no es una tabla oficial única:

| codigo | descripcion | coberturas |
|---|---|---|
| 01 | Contributivo cotizante | Plan de beneficios en salud financiado con UPC, … |
| 04 | Subsidiado | Plan de beneficios en salud financiado con UPC, … |

`contiene` compara sin tildes y sin distinguir mayúsculas.

**Tablas del DT1** (`RIPSFINALIDADCONSULTAVERSION2`, `RIPSCAUSAEXTERNAVERSION2`…): se descargan
de las tablas de referencia de SISPRO y se importan con el nombre que usa la regla.

**`GLOSAS_RES2284`**: generado de la hoja 07 del Excel SEMCOL
(`referencia/catalogos/glosas_res2284_consulta.csv`). Hoy es referencia; ninguna regla lo consulta.

---

## 9. Contratos por EPS

Las reglas son las mismas para todas las EPS; lo que cambia entre contratos son **parámetros**
(`reglas/contratos.py::Contrato`). Plantilla: `referencia/contratos/EJEMPLO_contrato.yaml`
(usa un NIT ficticio a propósito).

| Bloque | Parámetros | Lo usan |
|---|---|---|
| `autorizacion` | `requiere` (todos/ninguno/lista), `cups_requieren`, `cups_excentos`, `tipos_servicio_*`, `vigencia_dias` | `derivado.requiere_autorizacion` → `AUTORIZACION-REQUERIDA`, `ORDEN-SIN-AUTORIZACION` |
| `orden_medica` | `requerida_sin_autorizacion`, `vigencia_dias` | `ORDEN-SIN-AUTORIZACION` |
| `tarifa` | `metodo` (tarifario_institucional/soat/iss2001/propio), `tarifario_version_id`, `ajuste_pct` | Pendiente: plugin de tarifa por contrato |
| `soportes_requeridos` | Prefijos por tipo de servicio | Reglas futuras de soportes |
| `parametros` | Libre: `contrato.parametros.<x>` | Cualquier regla |
| `plugins` | Nombres de reglas en código específicas de esa EPS | Pendiente (sección 13) |

```bash
validador contrato importar referencia/contratos/NUEVAEPS_2026.yaml --usuario facturacion
validador contrato listar
```

**Selección:** por NIT de la EPS (`factura.nit_eps`) y vigencia en la fecha de referencia. Si el
XML trae `NUMERO_CONTRATO` y existe un contrato con ese número, ese tiene prioridad sobre el
genérico de la EPS. Importar una versión nueva del mismo contrato desactiva la anterior (queda
en la historia).

Sin contrato cargado, las reglas que dependen de él quedan `no_evaluable` (hoy: 2 reglas).

---

## 10. Agente normativo

Propone cambios a partir del texto de una norma nueva. **No activa nada.** Prompt y controles:
`documentos/prompts/agente_normativo.md`.

```
norma (PDF/texto) ─► contexto-agente ─► modelo ─► propuesta.json ─► reglas proponer ─► borrador
                                                                      │  • cita literal verificada en el PDF
                                                                      │  • regla válida (modelo)
                                                                      │  • rutas existentes, si no → requiere_extractor
                                                                      ▼
                                                              probar (sombra) ─► aprobar (humano) ─► activar
```

Prueba hecha con una propuesta simulada: la regla con cita real entró como borrador; la que
citaba un texto que no estaba en la norma se rechazó, y el campo inexistente quedó listado como
"requiere extractor".

Pendiente: elegir el proveedor del modelo (PLAN.md §12.4) e implementar la llamada. Todo lo
demás (contexto, validación, verificación de citas, borrador, sombra) ya está.

---

## 11. Recetas de cambio

**Sale una norma que cambia una obligación desde una fecha**

1. `validador reglas contexto-agente --salida ctx.json` y correr el agente, o escribir el cambio a mano.
2. En el YAML: poner `vigencia.hasta` a la versión vigente y agregar la versión nueva con
   `version` + 1 y `vigencia.desde` igual a la fecha de entrada en vigencia.
3. `reglas importar` (o `reglas proponer`) → `reglas probar <id> <facturas>` → revisar el informe → `aprobar` → `activar`.

**Una regla genera falsos positivos**: corregir parámetros o severidad en el YAML, mismo id,
`version` + 1 y la misma vigencia (reemplaza a la anterior en la versión nueva del conjunto),
y luego importar, probar, aprobar y activar.

**Llega una tabla nueva de CUPS o CIE-10**: `catalogo importar … --vigente-desde <fecha>`. No hay
que tocar reglas.

**Firma un contrato nuevo con una EPS**: copiar el ejemplo, llenarlo y `contrato importar`.

**La norma exige un dato que no se extrae**: desarrollo agrega el extractor, la entrada en
`RUTAS` y una prueba. Después la regla es solo dato.

**Verificar que un cambio de código no altera resultados**:

```bash
validador procesar <carpeta> --salida salidas/antes        # con la versión anterior (git checkout <tag>)
validador procesar <carpeta> --salida salidas/despues
python scripts/comparar_corridas.py salidas/antes/<corrida> salidas/despues/<corrida>
```

El script separa lo que ya existía (debe ser idéntico) de los hallazgos nuevos de reglas, y
termina con código 1 si hay diferencias no explicadas.

---

## 12. Verificación de esta versión

| Verificación | Resultado |
|---|---|
| Pruebas | 82 (62 previas + 20 del motor de reglas) |
| Línea base | git tag `linea-base-v0`; corridas en `salidas/linea_base/` (base de datos aparte) |
| Comparación (`scripts/comparar_corridas.py`) | **149/149 facturas con el resultado previo idéntico** (NUEVA EPS 101, COOSALUD 48) |
| Hallazgos nuevos | 6, todos `S00201` aviso: 2 consultas sin HC en la carpeta, 4 HC sin el contenido mínimo (p. ej. solo consentimiento informado) |
| Cambios de estado | 5 facturas `info → aviso` |
| Reglas sin catálogo ni contrato | Cumplen en las 98 facturas con RIPS dentro de su vigencia |
| Cobertura de la matriz SEMCOL | 23/35 (17 por reglas, 8 por el cruce entre documentos, que se solapan en 2) |

COOSALUD no trae RIPS: las reglas por servicio quedan `no_evaluable` ("sin RIPS"), que es lo
esperado.

---

## 13. Pendientes y decisiones

| Tema | Estado |
|---|---|
| Fechas de vigencia de las reglas | Se usó la fecha de expedición de cada norma (hoja 08). **Jurídica debe confirmar** fechas de entrada en vigencia y transición de la Res. 948/2026 |
| Cargar catálogos oficiales (CIE10, CUPSRIPS, tablas DT1) | Mecanismo listo; falta descargar las tablas de SISPRO y mapear columnas |
| Contratos reales por EPS | Mecanismo listo; falta transcribir los contratos |
| Tarifa por contrato (SOAT ± %, ISS…) | Modelo listo (`tarifa.metodo`); falta el plugin de cálculo |
| Plugins por EPS | Campo `plugins` en el contrato; falta el registro de plugins (cuando aparezca el primer caso real) |
| Extractores pendientes para la matriz | Profesional y diagnóstico en la HC (CONS-020/021), vigencia y cantidad de la autorización (CONS-012/031) |
| Pertinencia (CL0201) | Primitiva semántica (LLM con revisión humana), después de lo anterior |
| Agente normativo | Falta el proveedor del modelo y la llamada |
| Alembic | Antes del primer despliegue a producción |
| `aprobacion_cuatro_ojos` | `false` en desarrollo; **`true` en producción** (TOML de `VALIDADOR_CONFIG`) |
| Reglas de 0.1 aún en código | `_hallazgos_hc` (tipo de consulta por prefijo CUPS 8902/8903), `codigos_recurrentes`, documentos esperados: candidatas a migrar a reglas y catálogos, con comparación contra la línea base |
| Glosas reales de las EPS | Guardarlas permitiría medir la precisión de cada regla y ajustar severidades con datos |
