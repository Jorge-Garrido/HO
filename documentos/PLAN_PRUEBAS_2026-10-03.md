# Plan de pruebas — 2026-10-03

Tres frentes: (A) volver a medir el OCR con los arreglos de hoy, (B) probar las carpetas que
faltan del disco externo y (C) empezar la migración de parámetros a la base.

Datos de pacientes: todo queda en `~/datos_disco_d/` y `salidas/` (fuera de git). Al terminar,
decidir qué se borra (sección D).

## Estado de partida (2026-10-02)

- Rama `motor-reglas`. Regresión contra `linea-base-v0`: 101/101 NUEVA EPS idénticas; COOSALUD
  local 47/48 idénticas + 1 mejora (FE4372078: el OCR ahora lee el documento del paciente que
  antes era "no legible"; coincide con las otras 4 fuentes). 99 pruebas.
- Arreglos de OCR hechos hoy, **sin medir aún sobre el disco**:
  1. Una diferencia del escaneo solo se confirma si la leen **dos motores distintos**
     (PP-OCR y Tesseract). Antes valían dos lecturas del mismo motor, que confunde la misma
     forma (6↔8, 0↔Q).
  2. Si la lectura no confirma, se relee la línea con la **caja ampliada** (1,5 altos de línea a
     cada lado): el detector a veces deja por fuera el primer dígito.
  3. El escaneo que lee el número sin sus primeros dígitos es *aviso* "verificar a ojo", no *grave*.
  4. Varias autorizaciones en un mismo servicio del RIPS (`123,456`) se separan.
  5. Código del RIPS recortado respecto al facturado (`P890207-1` → `P89020`) es *grave*;
     códigos de sistemas distintos (interno vs CUM) son *aviso*.

### Números de referencia: corrida `salidas/disco_d/final` (antes de los arreglos 1, 2, 4 y del criterio de 5)

| | COOSALUD | FAMISANAR |
|---|---|---|
| Facturas / fallaron | 3.732 / 0 | 2.080 / 0 |
| Estado grave / aviso / info | 586 / 532 / 2.614 | 291 / 283 / 1.506 |
| Autorización: confirmada por OCR | 3.367 de 3.564 leídas | 1.999 de 2.067 |
| Autorización: *grave* por OCR | 111 | 2 |
| Hallazgos OCR (= revisión humana) | 227 | 80 |
| Líneas *grave* "código recortado" | 417 lentes `P89020` + 228 medicamentos (error, ya corregido) | 192 `F89021` + 69 `FF8902` |

## A. Volver a medir el OCR (COOSALUD y FAMISANAR ya están copiados)

```bash
source scripts/preparar_db_prueba.sh salidas/disco_d2
VALIDADOR_USUARIO=agente-claude .venv/bin/validador reglas importar referencia/reglas/normativa.yaml   # v2 → id 2 (ya trae los sinónimos)
for E in COOSALUD FAMISANAR; do
  .venv/bin/validador procesar ~/datos_disco_d/$E --por-lote --reglas 2 \
     --salida salidas/disco_d2/ocr --errores salidas/disco_d2/ocr/con_error --workers 16 --sin-copiar
done
python scripts/resumen_por_eps.py salidas/disco_d2/ocr
```

La caché del OCR (`salidas/ocr_cache`) se reutiliza: solo se repite la cascada, no la lectura de
la página. Debería tardar minutos.

**Qué medir y comparar con la tabla de arriba:**

| Métrica | Esperado |
|---|---|
| Autorización *grave* por OCR (COOSALUD) | baja mucho desde 111: quedan solo diferencias que leen dos motores distintos |
| Hallazgos OCR (revisión humana) | bajan por la caja ampliada |
| Líneas *grave* de medicamentos | 0 (pasan a *aviso*) |
| Autorizaciones "digitales discrepan entre sí" (60) | pasan a *aviso* de subconjunto |
| Facturas que fallaron | 0 |

**Revisión a ojo (obligatoria):** abrir 10 autorizaciones que sigan *grave* y 10 que el OCR dio
por confirmadas, y anotar si el OCR acertó. Con eso se estima la precisión real. Es el primer
paso del conjunto etiquetado (plan de OCR, punto 5).

**Si el OCR sigue siendo el cuello de botella**, siguiente mejora en orden:
1. Renderizar la página a 300 dpi cuando la imagen incrustada sea de menor resolución.
2. Guardar la zona del número por formato de autorización (`zona_ocr`, ver PLAN_PARAMETROS_EN_BD).
3. Solo con los datos anteriores: decidir si se necesita un modelo de visión (nube bajo demanda o
   modelo abierto; la GPU personal solo para desarrollo).

## B. Carpetas siguientes del disco: FOMAG, Husrtunja, IPS_OFTALMOLOGICA, MAYO

Hacer **una carpeta a la vez**, en este orden: Husrtunja primero, porque es del Hospital San
Rafael y le aplica el tarifario (contraste con NUEVA EPS); luego las demás.

```bash
# 1. Copia resistente a desconexiones (destino fuera del repo) y verificación de conteos
scripts/copiar_disco.sh Husrtunja
# 2. Inventario en seco: facturas, soportes, omitidos, conflictos
.venv/bin/python scripts/descubrir_lotes.py ~/datos_disco_d/Husrtunja/*
# 3. Validación digital (sin OCR), por lote
.venv/bin/validador procesar ~/datos_disco_d/Husrtunja --por-lote --reglas 2 --sin-ocr --sin-copiar \
   --salida salidas/disco_d2/husrtunja --errores salidas/disco_d2/husrtunja/con_error --workers 16
python scripts/agrupar_discrepancias.py salidas/disco_d2/husrtunja/2026-*
# 4. Si el ruido es alto: clasificar cada grupo como en la prueba de COOSALUD/FAMISANAR
#    (ruido de formato → alias/sinónimos/código genérico; error real → se deja)
# 5. Con OCR: muestra de 60 facturas, medir tiempo y confirmaciones; luego completo
```

**Criterios para cada carpeta:**
- 0 facturas que fallan; archivos omitidos explicados.
- Ningún grupo de discrepancias que afecte a casi todas las facturas de un lote. Si aparece, es
  formato y no error real.
- Todo arreglo de código: prueba unitaria y **regresión contra `linea-base-v0`** (y contra la
  corrida previa de COOSALUD/FAMISANAR) antes de seguir.
- Registrar por carpeta: qué se cambió, si fue dato (alias, sinónimo) o código genérico, y
  cuánto tiempo tomó. Es la medición real de "cuánto cuesta incorporar un formato".

## C. Migración de parámetros a la base (inicio)

Detalle en `documentos/PLAN_PARAMETROS_EN_BD.md`. Para mañana, solo el primer bloque:

1. Tablas `parametro_conjunto` y `parametro` con alcance (`global`, `prestador`, `eps`, `formato`)
   y el mismo ciclo de vida de las reglas (borrador → en_prueba → aprobada → vigente).
2. Importador desde YAML y una semilla `referencia/parametros/global.yaml` con lo que hoy está en
   `config.alias_soportes`, `patron_factura`, `ignorar_archivos` y `localizar.ETIQUETAS`.
3. Resolución por alcance (formato > eps > prestador > global) y `parametros_version_id` en el resultado.
4. Aceptación: regresión 149/149 contra `linea-base-v0` y la misma corrida de COOSALUD/FAMISANAR
   (sección A) sin diferencias.
5. Después (otro día): escenarios (`escenario crear --sin-alcance eps:<nit>`) para el simulacro de
   EPS nueva, y Alembic antes de producción.

## D. Limpieza de datos de pacientes

Al cerrar las pruebas, confirmar con el usuario y borrar:
- `~/datos_disco_d/` (copia del disco, ~15 GB + las carpetas nuevas)
- `salidas/descomprimidos/` (zips extraídos, ~5,8 GB)
- `salidas/disco_d*/` (resultados con datos de pacientes)
- `salidas/regresion*/` (corridas de verificación)

Conservar: `salidas/linea_base/` (referencia de regresión, datos de la muestra local) mientras no
exista un conjunto anonimizado.
