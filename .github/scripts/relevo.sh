#!/usr/bin/env bash
# Relevo de la cadena del vigia. Corre cuando termina una corrida de vigia.yml (relevo.yml, evento
# workflow_run) o a mano. Solo relanza y avisa: nunca revisa tiendas ni guarda estado (un solo escritor).
#
# 1. Cuenta las corridas de vigia.yml no terminadas que no son de horario (5 reintentos).
# 2. Si hay alguna viva: no hace nada.
# 3. Si no hay: espera ESPERA_RECUENTO s (45) y vuelve a contar, por si la cadena lanzo tarde.
# 4. Si sigue en cero: lanza vigia.yml en main (5 reintentos) y abre el aviso de cadena relanzada;
#    si no puede lanzar, abre el aviso de vigia detenido y sale con error.
# 5. Si no pudo contar: no lanza (la cadena ya lanza a ciegas en ese caso) y sale con error.
#
# RELEVO_SECO=1: no lanza ni abre nada; imprime lo que haria.

set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aviso.sh
. "$DIR/aviso.sh"

: "${ESPERA_RECUENTO:=45}"
: "${TERMINO_ID:=}" "${TERMINO_URL:=}" "${TERMINO_CONCLUSION:=}" "${TERMINO_HORA:=}" "${TERMINO_EVENTO:=}"

contar_vivas() {
  local out i
  for i in 1 2 3 4 5; do
    out=$(gh run list --repo "$GITHUB_REPOSITORY" --workflow vigia.yml --limit 30 --json databaseId,status,event \
      --jq '[.[] | select(.event != "schedule") | select(.status != "completed")] | length') && {
      echo "$out"
      return 0
    }
    sleep $((i * ESPERA_REINTENTO))
  done
  return 1
}

if [ -n "$TERMINO_ID" ]; then
  echo "termino la corrida $TERMINO_ID (evento $TERMINO_EVENTO, conclusion $TERMINO_CONCLUSION)"
fi

if ! vivas=$(contar_vivas); then
  echo "no se pudo contar las corridas vivas de la cadena tras 5 intentos: no se lanza nada"
  exit 1
fi
echo "corridas vivas de la cadena (primera cuenta): $vivas"
if [ "$vivas" != "0" ]; then
  echo "la cadena siguio: nada que hacer"
  exit 0
fi

sleep "$ESPERA_RECUENTO"
if ! vivas=$(contar_vivas); then
  echo "no se pudo contar en la segunda cuenta tras 5 intentos: no se lanza nada"
  exit 1
fi
echo "corridas vivas de la cadena (segunda cuenta): $vivas"
if [ "$vivas" != "0" ]; then
  echo "la cadena lanzo tarde: nada que hacer"
  exit 0
fi

QUIEN_RELANZA="el relevo automático (flujo relevo)"
if [ "${RELEVO_SECO:-}" = "1" ]; then
  echo "[seco] lanzaria: gh workflow run vigia.yml --repo $GITHUB_REPOSITORY --ref main"
  aviso_relanzada
  exit 0
fi

for intento in 1 2 3 4 5; do
  if gh workflow run vigia.yml --repo "$GITHUB_REPOSITORY" --ref main; then
    echo "la cadena estaba detenida: relanzada (intento $intento)"
    aviso_relanzada
    exit 0
  fi
  sleep $((intento * ESPERA_REINTENTO))
done
echo "no se pudo relanzar la cadena tras 5 intentos"
aviso_detenida
exit 1
