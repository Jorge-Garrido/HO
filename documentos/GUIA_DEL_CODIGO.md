# Guía del código: cómo funciona, cómo generaliza y cómo se mantiene

Para desarrollo y mantenimiento. Complementa `ARQUITECTURA_REGLAS.md` (motor de reglas y base) y
`PLAN_PARAMETROS_EN_BD.md` (parámetros por EPS). Estado al 2026-10-02: ~8.400 líneas en `src/`,
111 pruebas, probado con 4 EPS (NUEVA EPS, COOSALUD, FAMISANAR, FOMAG) y 2 IPS (Hospital San Rafael
y una óptica): 7.624 facturas.

---

## 1. Mapa del código

```
src/validador/
  base/          config.py (configuración), schemas.py (modelos Pydantic de entrada y salida)
  ingest/        lectura de cada documento
    discover.py      encuentra las facturas en cualquier árbol de carpetas y zips
    rips.py, cuv.py, xml_parser.py      fuentes estructuradas (RIPS, CUV, factura electrónica)
    pdf_text.py      texto digital de un PDF; detecta páginas escaneadas
    generico.py      campos de un PDF digital con el localizador (sin plantillas)
    factura_pdf_parser.py, hc_parser.py, autorizacion_header.py   lecturas específicas que aún existen
    autorizacion_ocr.py  autorización escaneada (usa ocr/)
    unificado.py     PDF con todos los soportes juntos: lo separa por páginas
  data_preprocessing/  layout.py (palabras con posición → filas), normalize.py (limpieza), recorte.py (imagen)
  localizar.py   EL CORAZÓN DE LA GENERALIZACIÓN: encuentra un campo en cualquier formato
  ocr/           motor.py (PP-OCR), tesseract.py, cascada.py (lecturas sucesivas), a_layout.py, imagenes.py
  compare.py     comparadores (exacto, tokens, numérico, distancia de dígitos)
  attribution.py votación: qué documento es el sospechoso
  severity.py    resultado de cada campo: ok / info / aviso / grave
  pipeline.py    orquesta una factura de principio a fin
  reglas/        motor de reglas normativas, catálogos, contratos, parámetros, sombra
  store/         db.py (resultados, tarifario), referencia.py (reglas, parámetros, catálogos, contratos, auditoría)
  report.py, salida_errores.py, explicar.py   JSON, Excel, _REVISION.txt y explicación en lenguaje claro
  corrections/   propuestas de corrección (solo se exportan; nunca se aplican solas)
  execute/       cli.py y cli_referencia.py (comandos)
```

---

## 2. Recorrido de una factura

```
discover.descubrir(lote)                                 ¿qué archivos son de qué factura?
   │  agrupa por número de factura; prefijos DIAN aprendidos; zips; alias; deduplica
   ▼
pipeline.procesar_carpeta(factura)
   │
   ├─ 1. extraer()                                       LEER cada documento
   │     RIPS, CUV, XML ─────────────── estructurados: valores de referencia
   │     vocabulario de la EPS (parámetros por alcance) se activa con el NIT del XML
   │     PDF factura, HC, autorización ─ generico.completar → localizar.verificar / extraer
   │     autorización escaneada ──────── ocr.cascada (PP-OCR → recorte → Tesseract → dígito a dígito)
   │
   ├─ 2. severity.evaluar_campo() por campo              COMPARAR entre fuentes
   │     votación (attribution.votar) · autoridad de la EPS · subconjuntos · escaneo recortado
   │     _desempatar_identidad(): documento distinto → ¿misma fecha de nacimiento y sexo?
   │
   ├─ 3. comparar_lineas()                               SERVICIOS facturados
   │     agrupa por código; _emparejar_codigos() une el mismo ítem con códigos distintos
   │     tarifario (solo del prestador al que pertenece)
   │
   ├─ 4. hallazgos                                       documentos faltantes, DIAN, CUV, HC, versiones distintas
   │
   ├─ 5. motor de reglas normativas                      NORMA (Res. 948, Res. 2284) como datos
   │     expediente → reglas vigentes por fecha de atención → hallazgos con norma y glosa
   │
   └─ ResultadoFactura (JSON) ─► base de datos · Excel · _REVISION.txt · propuestas de corrección
```

**Principio clave: verificar antes que extraer.** El RIPS y el XML son estructurados y confiables
para leer. Para cada PDF, el validador primero **busca el valor que ya conoce** (por ejemplo, el
número de autorización del RIPS) junto a una etiqueta del campo. Solo si no lo encuentra extrae lo
que haya junto a la etiqueta, para mostrar la diferencia. Por eso funciona en formatos nunca vistos:
no necesita saber dónde está el dato, solo confirmar que está.

---

## 3. Cómo generaliza: seis mecanismos

### 3.1 Estructura de carpetas (`discover.py`)

| Problema real | Mecanismo |
|---|---|
| Una carpeta por factura (NUEVA EPS) o todo mezclado por mes y municipio (COOSALUD) | Agrupa **todos** los archivos del árbol por número de factura, tomado del nombre del archivo o de la carpeta más cercana |
| RIPS en una carpeta hermana (`RIPS/`, `X-RIPS/CONT`) | Igual: el número de factura los une |
| Numeración DIAN distinta por IPS (`FE`, `FEAV`, `FCOB`) | `patron_factura()` **aprende los prefijos** de los nombres de XML y RIPS del lote |
| `CUV_FEAV1.json` o `FEAV1_CUV.json` | Clasificación por contenido del nombre (contiene "CUV") |
| Soportes sin prefijo (`PDE.pdf`, `HISTORIA.pdf`, `ORDEN N.pdf`, `AUT.pdf`) | Primera palabra + **alias** (parámetro) |
| Paquetes zip por factura o por lote | Se descomprimen una vez en caché (nunca en el origen) |
| La misma factura dos veces (zip + carpeta, "COPIA") | Deduplicación por contenido; si el contenido difiere se reporta "versiones distintas" |
| `.xlsx` de radicación, `desktop.ini` | Lista de ignorados (parámetro) |

### 3.2 Lectura de PDF sin plantillas (`localizar.py`)

El localizador trabaja con **palabras y sus posiciones**, no con coordenadas fijas de un formato.

1. **Etiquetas** por campo, de la más específica a la más general (`NOMBRE DEL PACIENTE` > `PACIENTE` > `NOMBRE`).
2. **Contextos negativos:** palabras que invalidan una etiqueta en su fila. "Autorización de
   *numeración* … *desde* … *hasta*" es la resolución de la DIAN, no una autorización de servicios.
   "*Acompañante* … identificación" no es el paciente.
3. **Etiquetas genéricas:** `Nombre` suelto encabeza también la tabla de impuestos; sin dos puntos no basta.
4. **Forma del valor:** un nombre no tiene números, comas ni más de 6 palabras; termina donde empieza
   otra etiqueta (`NO.`, `TIPO`, `EDAD`); un número de autorización tiene el formato de la configuración.
5. **Geometría:** el valor a la derecha de la etiqueta o, en tablas, en la celda de abajo. La
   celda se delimita por los **huecos entre palabras**: dentro de una celda el espacio es normal y
   entre columnas es grande.
6. **Prosa contra formulario:** una palabra suelta sin dos puntos rodeada de texto corrido
   ("el paciente cancela…") no es una etiqueta.

Las listas de los puntos 1-3 son **parámetros** con alcance (global, prestador o EPS). Los umbrales de 4 y 5 hoy son constantes del código (ver §8).

### 3.3 Comparación entre fuentes (`severity.py`, `attribution.py`)

- **Votación:** si 3 de 4 documentos dicen lo mismo, el cuarto es el sospechoso.
- **Autoridad:** para el número de autorización manda el documento de la EPS, si el OCR lo leyó
  con seguridad (dos motores distintos y sin duda en los dígitos que difieren).
- **Subconjuntos:** el RIPS trae una autorización por servicio y el PDF muestra una; no es contradicción (*aviso*).
- **Desempate de identidad:** documento distinto en la HC o la autorización → si la fecha de
  nacimiento y el sexo coinciden con el RIPS, es la misma persona (*aviso*); si no, posible soporte
  de otra persona (*grave*, S00202).

### 3.4 Servicios facturados (`pipeline.comparar_lineas`)

Las líneas se agrupan por código (el XML agrupa cantidades y el RIPS las separa). Cuando el mismo
ítem tiene códigos distintos en cada fuente, `_emparejar_codigos` lo une si el **valor coincide** y además:
- uno es prefijo del otro (código interno recortado en el RIPS → *grave*);
- las descripciones se parecen, o el valor es único entre las líneas sin pareja (interno vs CUM → *aviso*).

### 3.5 Norma como datos (`reglas/`)

Las reglas de la Res. 948 y la Res. 2284 son primitivas genéricas (`requerido`, `rango`,
`en_catalogo`, `secciones_presentes`…) con parámetros, vigencia legal y cita. Una regla nueva no
requiere código. Detalle en `ARQUITECTURA_REGLAS.md`.

### 3.6 Parámetros por alcance (`reglas/parametros.py`)

Lo que cambia entre EPS (etiquetas, alias, contextos, vocabulario) es una versión en la base con
alcance **eps > prestador > global**. El vocabulario se resuelve **por factura**, con la EPS y el
prestador del XML, y solo afecta a esa factura.

---

## 4. Qué pasa con un formato de EPS nuevo

### 4.1 Lo que ya funciona sin tocar nada

Estructuras de carpetas nuevas, numeraciones DIAN nuevas, zips, RIPS/XML/CUV (son estándar),
etiquetas habituales en documentos de salud, tablas y formularios digitales, autorizaciones
escaneadas con etiquetas reconocibles.

### 4.2 Cómo se detecta que algo no generalizó

Las señales salen de la corrida misma (`scripts/agrupar_discrepancias.py`):

| Señal | Ejemplo real | Qué era |
|---|---|---|
| Miles de **archivos omitidos** | FOMAG: 19.003 | Prefijo de factura desconocido (`FCOB`) |
| **Un mismo hallazgo en casi todas** las facturas de un lote | FAMISANAR: nombre en 97 % | La palabra "paciente" en prosa se leía como etiqueta |
| **Un mismo valor** en todas las facturas | FOMAG: autorización `18764…` en 766 | La resolución de la DIAN se leía como autorización |
| Regla normativa que falla en casi todo | HC sin "examen físico" en 3.194 | La óptica titula distinto ("agudeza visual") |
| Muchas reglas **no evaluables** | — | Falta un dato que el formato no trae |

Un error real casi nunca afecta al 90 % de un lote; un patrón masivo casi siempre es formato.

### 4.3 Qué se hace en cada caso

```
¿El problema es del formato?
 ├─ nombre de archivo o carpeta  → parámetro: alias, ignorados             (minutos, sin código)
 ├─ etiqueta con otro nombre      → parámetro: etiqueta del campo (alcance EPS si es solo de ella)
 ├─ etiqueta que se confunde      → parámetro: contexto negativo o etiqueta genérica
 ├─ sección de HC con otro título → regla: sinónimo en secciones_presentes (versión nueva de reglas)
 ├─ dato que hoy no se extrae     → CÓDIGO: extractor + ruta del expediente + prueba
 └─ estructura nueva de documento → CÓDIGO genérico (sirve para todas) + prueba + regresión
```

En la prueba con 4 EPS no fue necesario código específico de ninguna. Los arreglos de código
fueron genéricos y sirven para todas: prefijos aprendidos, celdas por huecos, emparejamiento de
códigos, prosa contra etiqueta.

### 4.4 Costo observado

| Formato | Antes → después (facturas con *grave*) | Qué se tocó | Tiempo |
|---|---|---|---|
| Óptica en COOSALUD | 99 % con discrepancias → 491 *grave* (de los cuales 417 son un error real sistemático) | 4 arreglos genéricos + sinónimos de HC | ~1 día |
| FAMISANAR (misma IPS) | 97 % → 281 | 1 arreglo genérico (prosa) | horas |
| FOMAG (misma IPS, otra EPS) | 842 → 71 | 3 arreglos genéricos + 1 alias | horas |

Cada formato nuevo costó menos que el anterior, porque los arreglos genéricos se acumulan.

---

## 5. Pruebas: cómo saber que un cambio es bueno

### 5.1 Las cinco capas

| Capa | Qué responde | Herramienta | Cuándo |
|---|---|---|---|
| **1. Unitarias** (111) | ¿Cada pieza hace lo que dice? Un caso por cada problema real encontrado | `pytest` | Cada cambio |
| **2. Regresión** | ¿Cambió algo que no debía? | `scripts/comparar_corridas.py` contra `linea-base-v0` | Cada cambio de código |
| **3. Sombra** | ¿Qué cambia una versión nueva de reglas o parámetros sobre facturas reales? | `validador reglas probar` / `validador parametros probar` | Antes de aprobar datos |
| **4. Corrida masiva por lote** | ¿Generaliza a formatos nuevos? | `procesar --por-lote` + `agrupar_discrepancias.py` | Al incorporar una EPS |
| **5. Revisión a ojo / conjunto etiquetado** | ¿Acierta de verdad? | Revisión humana de una muestra | Periódica; antes de cambiar una severidad |

**Reglas que se siguieron en la prueba y deben mantenerse:**
- Una prueba unitaria que **falle sin el arreglo** (verificado con la del número cortado del OCR).
- La regresión se corre **después de cada cambio**, no al final. Así se detectó que "un dígito
  distinto = aviso" escondía un error real (FE4421223) y se descartó.
- Una diferencia en la regresión se **explica**, no se acepta. Por ejemplo, FE4372078 es una mejora
  verificada: el OCR ahora lee un dato que antes no leía.

### 5.2 Lo que falta para pruebas realmente buenas

1. **Conjunto etiquetado anonimizado:** 100-200 facturas por formato con la respuesta correcta
   anotada (¿es real cada *grave*?). Hoy solo hay línea base de resultados, no de verdad.
   Con eso se mide **precisión** (cuántos *grave* son reales) y **cobertura** (cuántos errores reales se detectan).
2. **Glosas reales de las EPS** como etiqueta: la mejor medida de valor.
3. **Simulacro de EPS nueva:** procesar una EPS conocida con sus parámetros retirados y medir qué
   tan rápido se recupera (ver `PLAN_PARAMETROS_EN_BD.md` §4).
4. **Integración continua:** pruebas + regresión en cada commit. La regresión necesita datos
   anonimizados para correr fuera del equipo.
5. **Calibrar los umbrales** con el conjunto etiquetado (hoy se eligieron mirando los datos).

---

## 6. La base de datos en producción

### 6.1 Motor y tamaño

- **PostgreSQL** (JSON → JSONB), mismo código que SQLite en desarrollo; solo cambia `VALIDADOR_DB_URL`.
- **Resultados:** ~11 KB por factura (medido: mediana 10,7 KB, p95 12,2 KB). Con 17.000
  facturas al mes son ~190 MB/mes en JSON, ~4-5 GB/año contando índices.
- **Referencia** (reglas, parámetros, catálogos, contratos): unos pocos MB; CIE-10 y CUPS son los más grandes.

### 6.2 Tablas

```
OPERACIÓN                              REFERENCIA VERSIONADA (inmutable, con ciclo de vida)
corridas ──< resultados                regla_conjunto ──< regla
                                       parametro_conjunto
tarifario_version ──< tarifario_item   catalogo_version ──< catalogo_item
                                       contrato_version
                     auditoria (quién, cuándo, qué: toda transición de cualquier referencia)
```

| Tabla | Una fila es | Contiene datos de pacientes |
|---|---|---|
| `corridas` | una ejecución (lote) | no |
| `resultados` | el resultado de una factura en una corrida (JSON) | **sí**: documentos, nombres, valores por fuente |
| `regla_conjunto`, `regla` | una versión de reglas / una regla dentro de ella | no |
| `parametro_conjunto` | una versión de parámetros de lectura | no |
| `catalogo_version`, `catalogo_item` | una versión de CIE-10, CUPS, etc. / un código | no |
| `contrato_version` | una versión del contrato con una EPS | no |
| `tarifario_*` | una versión del tarifario / un ítem | no |
| `auditoria` | un evento: creada, prueba_sombra, aprobada, vigente, retirada | no (solo usuarios internos) |

Cada `resultados.resultado` guarda **con qué se validó**: `reglas.conjuntos`,
`reglas.catalogos`, `reglas.contrato_version_id`, `parametros_version_id`,
`tarifario_version_id` y `version_pipeline`. Una factura se puede revalidar exactamente como se
validó meses después, y una glosa se puede contrastar con lo que el sistema sabía ese día.

### 6.3 Seguridad y retención (datos sensibles, Ley 1581)

- `resultados` contiene datos de salud: cifrado en reposo, acceso por rol, conexión cifrada, y nada de datos personales en logs.
- **Separación por cliente (IPS):** una base o un esquema por cliente; un cliente no ve datos de otro.
- **Retención:** los resultados de facturas sin hallazgos se pueden resumir y borrar a los N días; los
  que tienen hallazgos se conservan hasta resolverse. Los documentos originales no se guardan en la
  base: se referencian donde ya viven (PLAN §10).
- **Copias de seguridad** diarias, cifradas y probadas.
- **Migraciones con Alembic** antes del primer despliegue (hoy `create_all` solo crea tablas nuevas).

### 6.4 Roles

| Rol | Puede |
|---|---|
| Operación (cliente) | procesar lotes, ver resultados y reportes de su IPS |
| Mantenimiento (ustedes) | importar y probar reglas, parámetros, catálogos y contratos |
| Aprobador (ustedes, otra persona) | aprobar y activar (`aprobacion_cuatro_ojos = true`) |
| Auditoría | leer la bitácora y los resultados históricos |

---

## 7. Cómo agregar cosas (recetas)

### 7.1 Una regla normativa

```yaml
# referencia/reglas/normativa.yaml (agregar al final de 'reglas:')
- id: RIPS-INCAPACIDAD-SINO
  nombre: Incapacidad con valor SI o NO
  matriz: []
  norma: {documento: "Res. 948/2026 DT1", numeral: "§4.2 U09", pagina: "24"}
  vigencia: {desde: 2026-05-14}
  alcance: factura
  primitiva: formato
  params: {campo: usuario.incapacidad, patron: "^(SI|NO)$"}
  resultado:
    tipo: validacion
    severidad: aviso
    documento: rips
    mensaje: El campo incapacidad del usuario no es SI o NO.
```

```bash
validador reglas importar referencia/reglas/normativa.yaml      # versión nueva en borrador
validador reglas probar <id> <carpetas de facturas>             # sombra contra la vigente
validador reglas aprobar <id> && validador reglas activar <id>  # aprobación de otra persona en producción
```

Si la regla necesita un dato que no está en `validador reglas rutas`, es código (§7.5).

### 7.2 Un parámetro de lectura para una sola EPS

```yaml
# referencia/parametros/global.yaml (o un archivo propio de la EPS)
- tipo: etiqueta_campo
  clave: paciente_nombre
  valor: ["NOMBRE DEL AFILIADO", "AFILIADO"]
  alcance: eps:830003564          # solo FAMISANAR; las demás no cambian
```

```bash
validador parametros importar referencia/parametros/global.yaml
validador parametros probar <id> ~/datos/FAMISANAR      # compara factura por factura contra lo vigente
validador parametros aprobar <id> && validador parametros activar <id>
```

### 7.3 Un catálogo (CIE-10, CUPS, tablas SISPRO)

```bash
validador catalogo importar CIE10.xlsx --nombre CIE10 --col-codigo Codigo --col-descripcion Nombre \
   --vigente-desde 2026-01-01 --etiqueta "SISPRO 2026-01"
```
Las reglas que lo usan dejan de ser "no evaluables" sin cambiar nada más.

### 7.4 Un contrato con una EPS

Copiar `referencia/contratos/EJEMPLO_contrato.yaml`, llenarlo y `validador contrato importar …`.

### 7.5 Código (cuando no basta con datos)

| Necesidad | Dónde | Obligatorio |
|---|---|---|
| Leer un dato nuevo de un documento | `localizar.TIPOS` + etiquetas (parámetro) + `generico.CAMPOS_POR_DOCUMENTO`; para reglas, entrada en `expediente.RUTAS` | prueba unitaria + regresión |
| Operación nueva para reglas | `reglas/primitivas.py` (`@registrar`, `Params`, `rutas`, `evaluar`) | prueba unitaria |
| Tipo de parámetro nuevo | `reglas/parametros.py` (`TipoParametro`, validación, resolución) | prueba + semilla regenerada desde el código |
| Cambio en la comparación o la severidad | `severity.py`, `pipeline.py` | regresión **sin diferencias inexplicadas** |

Lista de verificación de un cambio de código:
1. Prueba unitaria que falla antes y pasa después.
2. `pytest` completo.
3. Regresión contra la línea base (`scripts/preparar_db_prueba.sh` + `procesar` + `comparar_corridas.py`).
4. Si toca la lectura: corrida por lote de las EPS del disco y comparación con la anterior.
5. Explicar cada diferencia en el commit.

---

## 8. Limitaciones conocidas

| Tema | Estado |
|---|---|
| NIT del San Rafael fijo en `config.tarifario_nits_prestador` | Pasar al contrato o al tarifario por prestador |
| Umbrales sin calibrar: `HUECO_COLUMNA` 2,5, `MAX_PALABRAS_ENCABEZADO` 3, `MAX_TOKENS_NOMBRE` 6, `SIMILITUD_DESCRIPCION` 0,5, `AMPLIACION_CAJA` 1,5, duda de Tesseract 90/5 | Pasar a parámetros y calibrar con un conjunto etiquetado |
| Listas por defecto duplicadas en el código y en la semilla | Quitarlas del código cuando la base sea obligatoria (hay una prueba que las mantiene iguales) |
| Descubrimiento solo con parámetros globales (no por EPS) | Suficiente hasta hoy; aplicar alcance EPS exigiría leer el XML antes de agrupar |
| Severidades por defecto elegidas con estas pruebas | Validar con auditoría |
| `hc_parser.py`, `factura_pdf_parser.py`, `autorizacion_header.py` (lecturas propias del San Rafael) | Siguen para las notas de la HC y las líneas del PDF; candidatos a reemplazar por el localizador |
| HC escaneadas | No se leen por OCR (solo las autorizaciones) |
| `.rar` | No se abren; se reportan como omitidos |
