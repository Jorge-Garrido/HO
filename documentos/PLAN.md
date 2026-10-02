# Plan: validador de consistencia de documentos de facturación en salud

## Instrucciones para Claude Code

- Lee todo el documento antes de escribir código. Empieza por las Fases 0 y 1.
- Antes de codificar cada fase, resume qué vas a hacer y lista tus dudas. No avances de fase hasta cumplir los criterios de aceptación.
- Python 3.11+, `pydantic`, `pytest`, `lxml`, `pymupdf`, `openpyxl`, `sqlalchemy`. Sin datos personales en logs ni en el repositorio.
- **Estado:** prototipo de Fases 0 y 1 implementado en `src/validador` (ver sección 13).
- Decisiones de diseño abiertas: ver sección 12. Pregunta antes de decidirlas por tu cuenta.

## 1. Contexto y objetivo

Una IPS factura servicios a una EPS. Cada factura llega en una carpeta (`FE4417830/`) con estos archivos:

| Archivo | Documento | Naturaleza |
|---|---|---|
| `FE….json` | **RIPS** (Res. 2275/2023) | Digital, estructurado. **Fuente principal**: lo genera el sistema del hospital con toda la información de la factura |
| `FE…_CUV.json` | Respuesta de validación del Ministerio (CUV) | Digital. `ResultadosValidacion` puede no venir |
| `FE….xml` | `AttachedDocument` (UBL 2.1 DIAN) | Digital. `Invoice` y `ApplicationResponse` embebidos en CDATA. **Respaldo del RIPS** y única fuente del CUFE junto con el PDF |
| `FEV_…pdf` | Factura (representación gráfica) | Texto digital, 1-2 páginas. Hay **dos plantillas** (`214.000,00` y `79,000.00`) |
| `HEV_…pdf` | Historia clínica | Texto digital, 2-10 páginas. **Puede faltar** (apoyo diagnóstico) o venir **escaneada** |
| `PDE_…pdf` | Autorización de la EPS | Encabezado digital + cuerpo como imagen. Algunos son escaneo completo sin encabezado |
| `PDX_`, `CRC_`, `RAN_` | Resultados de apoyo diagnóstico, otros soportes | Opcionales; aún no se validan |

**Jerarquía de fuentes:** RIPS → XML (respaldo si no hay RIPS) → PDF factura → HC → autorización. Todas se cruzan entre sí; la jerarquía solo decide el valor final cuando no hay mayoría.

**Objetivo:**
1. Verificar que los datos cuadren entre los cuatro documentos.
2. Identificar cuál documento es el sospechoso cuando no cuadran.
3. Clasificar cada hallazgo (info / aviso / grave).
4. Guardar un JSON con el resultado de cada corrida.
5. Ofrecer una lista descargable de casos con posible error, y datos para indicadores.

**Volumen:** ~17.000 facturas al mes (~570 al día).

**Principio rector:** lo digital se valida primero. El escaneo va al final porque es la fuente menos fiable.

## 2. Campos a validar

| Campo | Fuentes | Comparación | Si difiere |
|---|---|---|---|
| `cufe` | XML (`cbc:UUID`), PDF factura | exacta, 96 caracteres | grave |
| `numero_factura` | RIPS, XML (`cbc:ID`), CUV, PDF factura, nombre de carpeta | exacta | grave |
| `paciente_id` (tipo + número) | RIPS (`usuarios[]`), XML (nota `ID:`), PDF factura, HC, autorización | exacta, dígito a dígito | grave |
| `paciente_nombre` | XML, PDF factura, HC, autorización (el RIPS no trae nombre) | por tokens (el orden cambia entre documentos) | aviso |
| `numero_autorizacion` | RIPS (`numAutorizacion`), XML (`AAJ`), nota del XML, PDF factura, autorización | exacta (conjunto si hay varias) | grave |
| líneas: código, cantidad, valor | RIPS (por servicio), XML (`InvoiceLine`), PDF factura (tabla) | agrupado por código: suma de cantidades y valores | grave (cantidad: aviso) |
| `valor_total` | RIPS (Σ `vrServicio` − Σ `valorPagoModerador`), XML (`PayableAmount`), PDF factura | numérica exacta | grave |
| `nit_emisor`, `nit_eps` | RIPS (emisor), XML, PDF factura | exacta, ignorando puntos y dígito de verificación | grave |
| `fechas` (ingreso, egreso, emisión) | XML, PDF factura, HC | exacta por día | aviso |
| `id_interno_paciente` | PDF factura (`CONSECUTIVO INTERNO: 113070-1`), HC (`Identificador único`), encabezado de la autorización | comparar la parte antes del guion | grave |

Notas importantes sobre el XML:

- El `AttachedDocument` trae el `Invoice` en un CDATA dentro de `cbc:Description`, y el `ApplicationResponse` en otro CDATA. Hay que parsear dos veces.
- **CUPS:** en algunas facturas `BuyersItemIdentification` es un código interno (ej. `39143`) y el CUPS real (6 dígitos) va al inicio del texto de la descripción. En otras coincide con el código del ítem. Extrae el CUPS con una regla que pruebe ambos y registra de dónde salió. Si ninguno es CUPS se usa el ID tal cual: medicamentos (CUM, `20085777-3`) e insumos (`S55114`) no son CUPS. Con RIPS, el código sale directo de `codConsulta` / `codProcedimiento` / `codTecnologiaSalud`.
- El XML agrupa cantidades (1 línea con cantidad 3) y el RIPS las separa (3 servicios): se compara agrupando por código.
- Extensión del sector salud: `CODIGO_PRESTADOR`, `MODALIDAD_PAGO`, `COBERTURA_PLAN_BENEFICIOS`, `NUMERO_CONTRATO` (el contrato liga la factura con su tarifario).

Notas sobre el RIPS y el CUV:

- `numDocumentoIdentificacion` **dentro de cada servicio es el profesional**, no el paciente. El paciente está en `usuarios[]`.
- El CUV trae notificaciones recurrentes (`FED137` contrato no registrado en SIIFA, `RVC0xx`): mismo tratamiento que `FAJ43b`/`RUT01` (info con contador). `ResultState=false` o clase de rechazo: grave. `PathFuente` no es consistente; no se parsea.
- **Respuesta de la DIAN:** extrae todos los `ResponseCode` del `ApplicationResponse`. Los códigos `FAJ43b` (nombre no corresponde al RUT) y `RUT01` (validación de RUT próximamente disponible) aparecieron en las facturas de ejemplo. Trátalos como **info/aviso recurrente**, no como error de la factura. Debe existir una vista que agregue cuántas veces aparece cada código.

**Historia clínica (HC):** no exijas igualdad de código CUPS. Las órdenes de la HC suelen ser de servicios futuros (por ejemplo, la consulta de control). Valida contra la HC solo:
- identidad del paciente,
- especialidad de la consulta,
- tipo de consulta (primera vez o control),
- fecha dentro del periodo de la factura.

## 3. Arquitectura y estructura del repositorio

```
src/validador/
  schemas.py            # modelos pydantic (campo extraído, resultado por campo, resultado por factura)
  config.py             # umbrales, campos críticos, proveedores (todo configurable)
  ingest/
    xml_parser.py       # AttachedDocument -> JSON (parseo doble de CDATA)
    pdf_text.py         # texto digital con PyMuPDF; detecta páginas sin texto
    pdf_images.py       # extrae imágenes incrustadas y su posición (get_image_info / extract_image)
    hc_parser.py        # reglas de extracción de la historia clínica
    factura_pdf_parser.py
  normalize.py          # mayúsculas, tildes, espacios, quitar puntos y guiones, decimales
  compare.py            # exacta, Hamming, Levenshtein, tokens, numérica
  attribution.py        # votación para decidir qué documento es el sospechoso
  severity.py           # matriz de severidad (sección 6)
  ocr/
    paddle.py           # detección + lectura con cajas (PaddleOCR)
    preprocess.py       # enderezar, escalar x2-x3, escala de grises, binarizar, quitar ruido
    tesseract.py        # relectura de recortes, solo dígitos (--psm 7, whitelist)
    digits.py           # comparación y lectura dígito a dígito (clasificador tipo MNIST, opcional)
    llm_fallback.py     # modelo grande por API, recibe solo el recorte
    locate.py           # buscar en las cajas la línea más parecida al valor esperado
  pipeline.py           # orquesta etapas A -> B -> C
  ingest/rips.py, ingest/cuv.py, ingest/autorizacion_header.py, ingest/discover.py
  tarifario/
    importer.py         # Excel -> validación -> tabla versionada (el Excel nunca se lee en ejecución)
    matcher.py          # tarifa por CUPS + descripción
  store/
    db.py               # SQLAlchemy Core: SQLite en desarrollo, Postgres (JSONB) en producción con solo cambiar la URL
  report.py             # JSON de la corrida + lista de casos con posible error
  corrections/
    adapter.py          # interfaz CorrectionAdapter (sección 7)
    export.py           # adaptador que exporta propuestas a CSV/JSON
  api/                  # FastAPI (Fase 5)
tests/
data/samples/           # documentos de ejemplo ANONIMIZADOS
```

## 4. Esquema JSON

**Campo extraído (normalizado):**

```json
{
  "campo": "numero_autorizacion",
  "valor": "17458248",
  "valor_crudo": "17458248",
  "fuente": "xml",
  "pagina": null,
  "bbox": null,
  "confianza": 1.0,
  "etapa": "digital"
}
```

**Resultado por factura (lo que se guarda por corrida).** Además de lo de abajo, el prototipo guarda: `schema_version`, `fuente_principal` (`rips`/`xml`), `documentos_presentes`, `documentos_faltantes`, `requiere_ocr`, `lineas[]` (código, cantidad y valor por fuente, resultado de tarifa), `hallazgos[]` (documentos, DIAN, CUV, HC, duplicados), `cuv{}` y `tarifario_version_id`. `valores_por_fuente` incluye `rips`, `xml_nota` y `cuv`. `estado` admite también `info`. Si hay varias fuentes sospechosas van separadas por coma.

```json
{
  "corrida_id": "2026-09-28T10:00:00Z-0001",
  "factura": "FE0000000",
  "cufe": "…",
  "estado": "ok | aviso | grave",
  "campos": [
    {
      "campo": "numero_autorizacion",
      "valor_final": "17458248",
      "valores_por_fuente": {"xml": "17458248", "pdf_factura": "17458248", "autorizacion_scan": "17468248"},
      "resultado": "aviso",
      "motivo": "escaneo difiere en 1 digito; xml y pdf digitales coinciden",
      "etapa_resolucion": "paddle | tesseract | digitos | llm | humano | digital",
      "documento_sospechoso": "autorizacion_scan",
      "distancia": 1,
      "confianza": 0.98
    }
  ],
  "dian": {"codigo": "02", "avisos": ["FAJ43b", "RUT01"]},
  "propuestas_correccion": [],
  "tiempos_ms": {"digital": 120, "ocr": 2400},
  "version_pipeline": "0.1.0"
}
```

Guarda un JSON por factura y un resumen por corrida (conteos por estado, por etapa que resolvió y por documento sospechoso) para los indicadores.

## 5. Pipeline

### Etapa A: extracción digital
1. Parsear el XML a JSON (doble CDATA).
2. Extraer texto del PDF de factura y de la HC con PyMuPDF, normalizar y llevar al esquema.
3. Detectar páginas sin texto útil (poco texto + una imagen que cubre casi toda la página) y marcarlas para OCR.
4. Del PDF de autorización, extraer el encabezado digital (identificador único, cédula, nombre) como fuente digital adicional.
5. El CUFE se lee del XML y del PDF de factura, ambos digitales. Compáralos directamente.

### Etapa B: cruce de las fuentes digitales
1. Comparar XML, PDF factura y HC campo por campo.
2. Consultar la base de datos por CUFE para detectar duplicados y facturas ya procesadas. (No se usa para "corregir" el CUFE; ya viene digital.)
3. Ejecutar `attribution.py` para identificar el documento sospechoso (sección 7).
4. Si todo cuadra entre digitales, estos datos pasan a ser el **valor de referencia** para el escaneo.

### Etapa C: OCR en cascada (solo para lo que falta o no cuadra)
Por cada campo del escaneo, se avanza a la siguiente etapa solo si la anterior no confirmó el valor:

1. **PaddleOCR** sobre la imagen extraída del PDF (resolución original). Enderezar primero (el texto puede venir girado 90°). Guardar cajas y confianza. Buscar en las cajas la línea más parecida al valor de referencia (`locate.py`).
2. **Tesseract** sobre el recorte de esa caja: reescalar x2-x3, escala de grises, binarizar, quitar ruido y líneas de tabla, restringir a dígitos (`--psm 7`, whitelist `0123456789`). Usar un motor distinto al de la etapa 1 para no repetir el mismo error.
3. **Dígito a dígito:** si la longitud coincide con la esperada y falla una posición, releer solo ese dígito. Opción A: alternativas por carácter de Tesseract (`lstm_choice_mode=2`). Opción B: clasificador pequeño tipo MNIST (solo si A no basta; medir antes de invertir).
4. **Modelo grande por API:** mandar únicamente el recorte del campo (nunca la página). Requiere que el proveedor cumpla las condiciones de la sección 10.
5. **Revisión humana:** si tras todo esto persiste la discrepancia.

Comparación numérica:
- Mismo largo: distancia de Hamming (posiciones que difieren).
- Distinto largo: Levenshtein.
- Confusiones típicas del OCR (5↔6, 1↔7, 0↔8, 3↔8): penalizar menos, porque son más probablemente errores de lectura.
- **No usar coseno para números.** Para nombres, comparar por conjunto de tokens.

## 6. Reglas de severidad

| Situación | Resultado |
|---|---|
| Error tipográfico en nombres o texto libre | **aviso** (no se presenta como error) |
| Escaneo difiere, pero las fuentes digitales coinciden entre sí | **aviso** (error de OCR corregido con las digitales) |
| Diferencia de 1 dígito en cédula, autorización, CUPS, NIT o CUFE tras agotar la cascada | **grave** |
| Las fuentes digitales discrepan entre sí | **grave** |
| Código DIAN `FAJ43b` / `RUT01` | **info**, con contador de recurrencia |
| Formato inválido (longitud o caracteres imposibles) | **grave** |

Las reglas y los campos críticos deben vivir en `config.py`, no en el código.

## 7. Qué documento está errado y cómo se corrige

### Atribución del documento sospechoso
Para cada campo comparado entre 3 o 4 fuentes, se vota por mayoría. La fuente que difiere de las demás es la sospechosa. Si hay empate (por ejemplo dos fuentes con valores distintos), no se atribuye y el caso queda como grave para revisión humana. El resultado se guarda en `documento_sospechoso`.

### Acción sugerida según el documento errado
Estas son propuestas, no acciones automáticas. **Confirmar los procedimientos con contabilidad y con el área jurídica antes de implementar mensajes definitivos:**

| Documento sospechoso | Acción propuesta |
|---|---|
| Error de OCR del escaneo (digitales coinciden) | Ninguna corrección en origen; solo aviso |
| XML / factura electrónica | El XML **no se edita**: el CUFE es un hash del contenido y la DIAN ya lo validó. Normalmente se corrige con nota crédito o débito, o refacturando. Generar la propuesta, no ejecutarla |
| Historia clínica | Corregir en el sistema clínico, normalmente con nota aclaratoria y no sobrescribiendo. Generar la propuesta |
| Autorización escaneada | Re-escanear o re-subir, corregir metadatos en el gestor documental, o solicitar nueva autorización a la EPS |

### Corrección en los sistemas de origen
- Definir la interfaz `CorrectionAdapter` con `propose()` y `apply()`.
- `apply()` **deshabilitado por defecto**. Se activa solo por sistema, con aprobación humana explícita y registro de auditoría (quién, cuándo, qué valor cambió).
- Primera implementación: `export.py`, que genera CSV/JSON descargable de las propuestas.
- Escritura por API a los sistemas fuente: solo cuando se confirme que cada sistema tiene API, con credenciales y permisos de escritura. No implementarla en las primeras fases.

## 8. Fases y criterios de aceptación

| Fase | Contenido | Criterio de aceptación | Esfuerzo (estimado) |
|---|---|---|---|
| **0** | Esquema JSON, `xml_parser` (doble CDATA, códigos DIAN), `pdf_text`, `normalize` | Las dos facturas de ejemplo producen JSON con todos los campos de la sección 2 y el CUPS extraído correctamente | 1 semana |
| **1** | `hc_parser`, `factura_pdf_parser`, `compare`, `attribution`, `severity`, base SQLite por CUFE | Cruce digital sobre los casos de ejemplo; detección de duplicados; salida en el esquema de la sección 4 | 1 semana |
| **2** | `pdf_images`, `paddle`, `locate`, `preprocess` | Sobre el conjunto de prueba, se mide qué % de campos del escaneo se confirman en esta etapa | 2 semanas |
| **3** | `tesseract`, `digits`, `llm_fallback`, cascada completa | Reporte por etapa: % resuelto, % que pasa a humano, tiempo por página | 2 semanas |
| **4** | Reglas de severidad, reporte agregado, lista descargable, pruebas | Todas las reglas de la sección 6 cubiertas con pruebas | 1 semana |
| **5** | API (FastAPI), cola de trabajos, autenticación, S3, despliegue | Carga por lote, consulta de resultados y descarga de casos con posible error | 2-3 semanas |

Total estimado: 9-10 semanas-persona.

## 9. Pruebas y métricas

- **Conjunto de prueba:** 200-500 facturas reales con respuesta correcta anotada a mano, **anonimizadas** antes de entrar al repositorio (reemplazar nombres, cédulas, teléfonos y direcciones por datos falsos consistentes en todos los documentos de la factura).
- **Casos de regresión obligatorios** (con las facturas de ejemplo):
  - Escaneo con un dígito mal leído (`17468248` en vez de `17458248`) mientras XML y PDF digitales coinciden → resultado **aviso**, `documento_sospechoso = autorizacion_scan`, corregido con las fuentes digitales.
  - Autorización escaneada con texto rotado 90°.
  - CUPS que solo aparece en la descripción y no en el código del ítem.
  - Factura con códigos DIAN `FAJ43b` y `RUT01`.
  - Mismo nombre con distinto orden entre documentos → no debe generar error.
  - Caso sintético: un dígito distinto en una cédula entre XML y HC → **grave**.
- **Métricas:**
  - exactitud por campo y por etapa,
  - % de campos resueltos en cada etapa de la cascada,
  - % que llega a revisión humana,
  - tasa de falsas alarmas (grave que resultó ser correcto) y de errores no detectados,
  - tiempo por página y por factura,
  - costo por factura (incluyendo llamadas a la API grande).
- Medir con documentos reales antes de optimizar. No reescribir en otro lenguaje sin perfilar.

## 10. Seguridad y cumplimiento

- Los datos de salud son datos sensibles (Ley 1581 de 2012 en Colombia). Cifrado en tránsito y en reposo, control de acceso por rol, bucket privado.
- Sin datos personales en logs ni mensajes de error.
- El fallback por API externa recibe únicamente el recorte del campo. Usarlo solo con un proveedor que ofrezca acuerdo de tratamiento de datos y residencia adecuada; hacerlo configurable para poder desactivarlo.
- S3: regla de ciclo de vida que borre los documentos validados a los pocos días y conserve los erróneos hasta que se resuelvan. Descarga con URLs prefirmadas de corta vigencia.
- Guardar de cada factura el JSON de resultado, el CUFE e identificadores. Referenciar los originales en el sistema donde ya viven en lugar de copiarlos.

## 11. Infraestructura y costos de referencia (estimados)

- Volumen: ~17.000 facturas al mes, ~2-3 MB por factura, ~3-5% con posible error.
- Desarrollo y pruebas: PC local con GPU RTX 4080 Super (16 GB); los documentos no salen del equipo.
- Producción inicial: un VPS con Postgres o SQLite y copias automáticas, PaddleOCR y Tesseract en CPU, VLM-OCR en GPU por demanda solo para casos difíciles.
- Costo mensual aproximado: **US$30-55** (MVP), **US$60-110** (realista), **US$120-230** (con alta disponibilidad). S3 aporta ~US$2-5.
- El costo dominante no es la infraestructura sino la **revisión humana** de los casos graves.

## 11b. Tarifario (`base_cups.xlsx`)

- Es el **tarifario institucional** (3 anexos: consultas y procedimientos, laboratorio, paquetes; 513 ítems), en UVB 2025 = 12.110. `REDONDEADO` = UVB × 12.110 redondeado a la centena con empates hacia arriba (usar `Decimal` + `ROUND_HALF_UP`, no `round()`).
- **La tarifa depende del CUPS y de la descripción:** el CUPS no es único (`998702` tiene 6 tarifas, `908420` 19). La búsqueda: candidatos por CUPS → similitud de tokens con la descripción facturada (sin el sufijo de categoría `-CONSULTA - INTERCON`) → si la descripción es ambigua y el valor coincide con un único candidato, se toma ese (`por_valor`) → si no, `no_determinable` (info), nunca error.
- Solo cubre parte de lo facturado (en la muestra, 51 de 124 líneas tienen su CUPS). Por eso una diferencia de valor contra esta base es **aviso**, no grave, hasta tener el tarifario completo del contrato.
- Flujo: el Excel lo mantiene facturación → `validador tarifario importar` valida (rechaza código institucional repetido en el mismo anexo; avisa CUPS no estándar y redondeos inconsistentes) → tablas `tarifario_version` (sha256, UVB, fecha) y `tarifario_item` (CUPS como texto, valores `numeric`) → cada resultado guarda el `tarifario_version_id`.

## 12. Decisiones pendientes (preguntar antes de asumir)

1. ¿Qué campos de la sección 2 son críticos para el negocio y cuáles solo avisan?
2. ¿Se puede recalcular el CUFE (SHA-384) con la clave técnica de la resolución? Sería una validación adicional fuerte.
3. ¿Qué sistemas fuente tienen API y con qué permisos (lectura o escritura)?
4. ¿Qué proveedor de modelo grande está permitido para datos de salud?
5. ¿Habrá formatos de autorización de otras EPS? Si son pocos y estables con mucho volumen, evaluar un detector propio (YOLO); si varían mucho, mantener el detector de texto genérico.
6. ¿Dónde se aloja producción (residencia de datos)?
7. ¿Cuál es el tarifario completo del contrato con cada EPS? El Excel actual no explica valores como 79.000 en consultas 890350/890250.
8. Las facturas son de 2026 y el tarifario usa UVB 2025: ¿el contrato fija ese año o hay que actualizar?
9. ¿Tolerancia de días para las notas de la HC fuera del periodo (p. ej. un resultado leído al día siguiente)? Hoy es 0 días.
10. `ACD627` está usado en dos anexos para servicios distintos: ¿cuál es el correcto?

## 13. Estado del prototipo (2026-09-28)

- Implementado: extracción de RIPS, CUV, XML, PDF factura (dos plantillas), HC y encabezado de autorización; comparación, votación, severidad, líneas, tarifario, duplicados por CUFE, propuestas de corrección (solo exportación), reporte (JSON por factura, `resumen.json`, `casos_con_posible_error.csv`, `propuestas_correccion.json`), CLI y 42 pruebas (incluye pruebas sobre los datos reales locales, que se omiten si la carpeta no existe).
- Sobre las 101 facturas de muestra: RIPS y XML coinciden línea a línea en todas; el PDF coincide con el XML en todas. Resultado: 7 graves (6 autorizaciones del RIPS distintas a XML/PDF, 1 recién nacido con RC en la factura y CN en HC/autorización), 15 avisos, 79 info.
- Localizador genérico (posiciones + diccionario de etiquetas, sin plantillas) reemplazó a los parsers por plantilla en los PDF digitales: 99,9-100 % contra los parsers anteriores, 0 confirmaciones falsas; funciona igual con COOSALUD (otro formato).
- OCR (Fases 2-3) implementado: PP-OCRv6 vía RapidOCR/ONNX en CPU (página completa, orientación por página, enderezado fino) → recorte limpio (OpenCV) → Tesseract (línea y solo dígitos) + reconocedor PP-OCR → dígito a dígito con alternativas LSTM. El documento de la EPS es la autoridad para el número de autorización cuando la lectura es confiable (dos motores). 0 confirmaciones falsas en ~930 controles negativos.
- Salida: `CON ERROR/<EPS>/` con copia de las carpetas con discrepancias, `_REVISION.txt` y `REPORTE <EPS>.xlsx`. Paralelo por procesos (`--workers`).
- Cambio respecto a la sección 6: "solo el escaneo difiere con lectura de un solo motor" es info (configurable `si_ocr_incierto_difiere`), no aviso.
- Estructura actual de `src/validador/`: `base/` (config, schemas) · `data_preprocessing/` (normalize, layout, recorte de imágenes) · `ingest/` (lectura de cada documento, PDF unificados) · `localizar.py` (localizador genérico) · `ocr/` (motor, cascada, Tesseract) · `compare.py`, `attribution.py`, `severity.py` (decisión) · `tarifario/` · `explicar.py`, `salida_errores.py`, `report.py` (salidas) · `store/` (base de datos) · `execute/` (línea de comandos; aquí irá la API).
- Pendiente: modelo pequeño por API para lo no concluyente, tarifario por contrato/EPS, API + dashboard + buzón (Fase 5) y despliegue.

Uso:

```
pip install -e ".[dev]"            # o ".[dev,postgres]" en producción
validador tarifario importar documentos/base_cups.xlsx --uvb-anio 2025
validador procesar "nuevaeps/NUEVA EPS EXITOSA"
VALIDADOR_DB_URL=postgresql+psycopg://usuario@host/validador validador procesar ...
```
