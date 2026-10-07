# Resultados de las pruebas — estado al 2026-10-07

Resumen de lo que el validador encontró en las facturas reales. Detalle de cómo se llegó a estos
números en `PLAN_PRUEBAS_2026-10-03.md`; cómo funciona el código en `GUIA_DEL_CODIGO.md`.

Corrida de referencia: `salidas/disco_d2/v5` (2026-10-03, rama `motor-reglas`, reglas normativas
v2, parámetros desde la base). Los datos de pacientes están fuera del repositorio.

## 1. Volumen

| | Facturas | Fallaron | Con *grave* | Con *aviso* | Solo *info* / ok |
|---|---|---|---|---|---|
| COOSALUD (óptica) | 3.732 | 0 | 480 (12,9 %) | 621 | 2.631 |
| FAMISANAR (óptica) | 2.080 | 0 | 281 (13,5 %) | 291 | 1.508 |
| FOMAG (óptica) | 1.663 | 0 | 71 (4,3 %) | 230 | 1.362 |
| **Disco externo** | **7.475** | **0** | **832 (11,1 %)** | **1.142** | **5.501** |
| Línea base local: NUEVA EPS (San Rafael) + COOSALUD | 149 | 0 | — | — | — |
| **Total probado** | **7.624** | **0** | | | |

Tiempo promedio por factura: 400-590 ms con OCR de autorizaciones (COOSALUD), 190-200 ms
(FAMISANAR), 150-175 ms sin OCR (FOMAG).

## 2. Errores por rubro (facturas con al menos un aviso o grave del rubro)

| Rubro | Facturas | Qué es | Lectura |
|---|---|---|---|
| Línea: código distinto RIPS vs factura | 736 | Mismo ítem y valor, código distinto (`P89020` vs `P890207-1`; `F89021`, `FF8902`) | **Error real sistemático** del prestador: el RIPS recorta el código. Es el grueso de los *grave* |
| Hallazgo OCR | 331 | Lectura del escaneo de la autorización no concluyente | Revisión humana; no es error de la factura |
| Norma: HC-CONTENIDO-MINIMO | 317 | La HC no muestra todas las secciones mínimas | Glosa potencial S00201; pendiente de auditoría |
| Campo: nombre del paciente | 315 | Nombre distinto entre fuentes | Casi todo *aviso*: abreviaturas, orden, segundo nombre |
| Campo: autorización vs escaneo | 171 | Lo digital difiere de lo que dice el papel de la EPS | 26 *grave* de lectura segura (dos motores); a auditar |
| Campo: autorización digital | 132 | RIPS, XML y PDF no coinciden entre sí | |
| Línea: otras diferencias | 130 | Servicio que falta en una fuente, valor o cantidad distinta | |
| Campo: documento del paciente | 125 | Documento distinto entre fuentes | Con desempate por fecha de nacimiento y sexo |
| Norma: SOPORTE-HC-CONSULTA | 84 | Consulta facturada sin HC ni epicrisis | Glosa potencial S00201 |
| Norma: RIPS-MODERADOR-DOCUMENTO | 35 | Pago moderador sin su documento soporte (Res. 948/2026) | |
| Hallazgo: extracción | 23 | Un documento no se pudo leer | |
| Hallazgo: documentos | 20 | Falta RIPS, XML o FEV | |

Glosas potenciales S00201 (soportes): 401 facturas (COOSALUD 151, FAMISANAR 139, FOMAG 111).

**Muestra para revisión:** `scripts/muestra_por_rubro.py` arma una carpeta con 5 facturas por
rubro (graves primero, turnando EPS): documentos originales, `_REVISION.txt` y un `INDICE.csv`
para anotar si cada caso es real o falso positivo. La carpeta tiene datos de pacientes y no va al
repositorio.

```bash
.venv/bin/python scripts/muestra_por_rubro.py salidas/disco_d2/v5 ~/muestra_errores_AAAA-MM-DD --n 5
```

## 3. OCR de autorizaciones escaneadas

| | COOSALUD | FAMISANAR |
|---|---|---|
| Con lectura OCR | 3.561 | 2.067 |
| Confirmadas (OCR = digital) | 3.386 (95 %) | 1.999 (97 %) |
| *Grave* por OCR | 26 (antes 111) | 2 |
| Revisión a ojo de 6 casos | 2 errores de OCR, 2 dudosos, 1 real; FE4421223 (NUEVA EPS) real | |

FOMAG no trae autorizaciones escaneadas.

## 4. Cómo bajó el ruido (facturas con *grave*)

| Formato | Primera corrida | Final | Qué se hizo |
|---|---|---|---|
| COOSALUD | 99 % con discrepancias → 586 | **480** | 4 arreglos genéricos, sinónimos de HC, OCR con dos motores, desempate de identidad |
| FAMISANAR | 97 % con discrepancias → 291 | **281** | 1 arreglo genérico (prosa contra etiqueta), desempate de identidad |
| FOMAG | 842 | **71** | Prefijo DIAN aprendido, contexto negativo (resolución DIAN), encabezados, desempate de identidad |

El último cambio (desempate de identidad por fecha de nacimiento y sexo, más contexto de
acompañante) pasó 49 facturas de *grave* a *aviso* (COOSALUD 11, FAMISANAR 10, FOMAG 28); el número de facturas *info* no cambió.

## 5. Calidad del cambio

- 111 pruebas unitarias pasan.
- Regresión contra `linea-base-v0`: NUEVA EPS 101/101 idénticas; COOSALUD local 47/48 + 1 mejora
  explicada (FE4372078: el OCR ahora lee el documento del paciente).
- Parámetros desde la base: 7.475/7.475 facturas idénticas con y sin la migración.

## 6. Pendiente

1. Auditoría de la muestra por rubro y de los 26 *grave* de OCR → primera medida de **precisión**.
2. Carpetas copiadas sin procesar: Husrtunja (San Rafael, con tarifario), IPS_OFTALMOLOGICA, MAYO.
3. Conjunto etiquetado anonimizado para regresión fuera del equipo e integración continua.
4. Limpieza de datos de pacientes al cerrar las pruebas (`PLAN_PRUEBAS_2026-10-03.md` §D).
