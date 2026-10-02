#!/usr/bin/env bash
# Copia carpetas de EPS del disco externo a local. Si el disco se desconecta, espera y retoma
# (rsync no repite lo ya copiado). Solo lee del disco. El destino queda FUERA del repositorio.
#   scripts/copiar_disco.sh FOMAG Husrtunja IPS_OFTALMOLOGICA MAYO
# Si WSL pierde el disco:  sudo umount -l /mnt/d; sudo mount -t drvfs D: /mnt/d
ORIGEN=${ORIGEN:-/mnt/d}
DESTINO=${DESTINO:-$HOME/datos_disco_d}
mkdir -p "$DESTINO"
for EPS in "$@"; do
  intento=0
  until rsync -rt --partial --no-inc-recursive "$ORIGEN/$EPS/" "$DESTINO/$EPS/" 2>>"$DESTINO/copiar.err"; do
    intento=$((intento+1))
    echo "$(date '+%H:%M:%S') $EPS: interrumpido (intento $intento); esperando a que el disco responda…"
    until ls "$ORIGEN/$EPS" >/dev/null 2>&1; do sleep 20; done
  done
  o=$(find "$ORIGEN/$EPS" -type f | wc -l); l=$(find "$DESTINO/$EPS" -type f | wc -l)
  echo "$(date '+%H:%M:%S') $EPS: COPIA COMPLETA — origen $o archivos, local $l archivos"
done
echo "TODO COPIADO"
