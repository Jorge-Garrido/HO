#!/usr/bin/env bash
# Base de datos nueva para una prueba: tarifario + reglas (v1 vigente; v2 candidata si se indica).
#   source scripts/preparar_db_prueba.sh salidas/prueba_X [referencia/reglas/normativa.yaml]
N=$1; REGLAS=${2:-referencia/reglas/normativa.yaml}
rm -rf "$N"; mkdir -p "$N"
export VALIDADOR_DB_URL=sqlite:///$N/validador.db VALIDADOR_USUARIO=${VALIDADOR_USUARIO:-$USER}
V=.venv/bin/validador
$V tarifario importar documentos/base_cups.xlsx --uvb-anio 2025 >/dev/null
$V reglas importar "$REGLAS" >/dev/null
$V reglas probar 1 "COOSALUD EXITOSAS" --limite 3 --workers 1 --salida "$N/sombra" >/dev/null 2>&1
$V reglas aprobar 1 >/dev/null && $V reglas activar 1
