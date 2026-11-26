#!/usr/bin/env bash
# Relevo de la cadena del vigia. Corre cuando termina una corrida de vigia.yml (relevo.yml, evento
# workflow_run) o a mano. Solo relanza y avisa: nunca revisa tiendas ni guarda estado (un solo escritor).
#
# 0. Si vigia.yml esta desactivado en Actions (apagado a proposito): no hace nada.
# 1. Cuenta las corridas de vigia.yml en main no terminadas, que no son de horario ni la que acaba de
#    terminar (TERMINO_ID) (5 reintentos).
# 2. Si hay alguna viva: no hace nada.
# 3. Si no hay: espera ESPERA_RECUENTO s (45) y vuelve a contar, por si la cadena lanzo tarde.
# 4. Si sigue en cero: lanza vigia.yml en main (5 reintentos; antes de cada reintento vuelve a contar y
#    para si ya hay una viva) y abre el aviso de cadena relanzada; si no puede lanzar, abre el aviso de
#    vigia detenido y sale con error.
# 5. Si no pudo contar: si la corrida que termino concluyo "success" (su ultimo paso lanzo la siguiente o
#    vio otra viva), se asume la cadena viva y sale con 0; si no, lanza a ciegas como en 4. Dos cadenas
#    se funden solas en un ciclo; una muerta no avisa a nadie.
#
# RELEVO_SECO=1: no lanza ni abre nada; imprime lo que haria.

set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aviso.sh
. "$DIR/aviso.sh"

: "${ESPERA_RECUENTO:=45}"
: "${TERMINO_ID:=}" "${TERMINO_URL:=}" "${TERMINO_CONCLUSION:=}" "${TERMINO_HORA:=}" "${TERMINO_EVENTO:=}"
: "${QUIEN_RELANZA:=el relevo automático (flujo relevo)}"

# Plan B: el relevo lo lanzo la propia corrida (TERMINO_ID = inputs.corrida) y no trae como termino.
# Se pregunta a GitHub, para el texto del aviso y para la regla de "sin cuenta" (success = cadena viva).
if [ -n "$TERMINO_ID" ] && [ -z "$TERMINO_CONCLUSION" ]; then
  if vista=$(gh run view "$TERMINO_ID" --repo "$GITHUB_REPOSITORY" --json status,conclusion,url,updatedAt,event \
      --jq '"\(.status)\t\(.conclusion // "-" | if . == "" then "-" else . end)\t\(.url)\t\(.updatedAt)\t\(.event)"'); then
    IFS=$'\t' read -r v_estado v_conclusion TERMINO_URL TERMINO_HORA TERMINO_EVENTO <<< "$vista"
    [ "$v_conclusion" != "-" ] && TERMINO_CONCLUSION="$v_conclusion"
    echo "la corrida $TERMINO_ID que lanzo este relevo esta: $v_estado ${TERMINO_CONCLUSION:-(sin conclusion aun)}"
  else
    echo "no se pudo leer la corrida $TERMINO_ID que lanzo este relevo: se sigue sin su conclusion"
  fi
fi

# Una consulta: imprime cuantas corridas de la cadena siguen vivas.
contar_una() {
  gh run list --repo "$GITHUB_REPOSITORY" --workflow vigia.yml --branch main --limit 30 --json databaseId,status,event \
    --jq "[.[] | select(.databaseId != ${TERMINO_ID:-0}) | select(.event != \"schedule\") | select(.status != \"completed\")] | length"
}

contar_vivas() {
  local out i
  for i in 1 2 3 4 5; do
    out=$(contar_una) && { echo "$out"; return 0; }
    [ "$i" -lt 5 ] && sleep $((i * ESPERA_REINTENTO))
  done
  return 1
}

# Lanza vigia.yml en main (5 intentos) y avisa. Sale del script.
lanzar_y_avisar() {
  local intento otras
  if [ "${RELEVO_SECO:-}" = "1" ]; then
    echo "[seco] lanzaria: gh workflow run vigia.yml --repo $GITHUB_REPOSITORY --ref main"
    aviso_relanzada
    exit 0
  fi
  for intento in 1 2 3 4 5; do
    if [ "$intento" -gt 1 ]; then
      # Un 500 puede llegar despues de que GitHub ya creo la corrida: antes de reintentar, mirar.
      if otras=$(contar_una) && [ "$otras" != "0" ]; then
        echo "antes del intento $intento ya hay $otras corrida(s) viva(s): el lanzamiento anterior si entro"
        aviso_relanzada
        exit 0
      fi
    fi
    if gh workflow run vigia.yml --repo "$GITHUB_REPOSITORY" --ref main; then
      echo "la cadena estaba detenida: relanzada (intento $intento)"
      aviso_relanzada
      exit 0
    fi
    [ "$intento" -lt 5 ] && sleep $((intento * ESPERA_REINTENTO))
  done
  echo "no se pudo relanzar la cadena tras 5 intentos"
  aviso_detenida
  exit 1
}

# Sin cuenta: decide segun como termino la corrida.
sin_cuenta() {
  if [ "$TERMINO_CONCLUSION" = "success" ]; then
    echo "no se pudo contar ($1); la corrida terminó bien, se asume cadena viva"
    exit 0
  fi
  echo "no se pudo contar ($1) y la corrida no terminó bien (conclusion '${TERMINO_CONCLUSION}'): se lanza a ciegas"
  lanzar_y_avisar
}

if [ -n "$TERMINO_ID" ]; then
  echo "termino la corrida $TERMINO_ID (evento $TERMINO_EVENTO, conclusion $TERMINO_CONCLUSION)"
fi

if estado=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/vigia.yml" --jq .state); then
  echo "estado del flujo vigia: $estado"
  if [ "$estado" != "active" ]; then
    echo "vigía apagado a propósito: nada que hacer"
    exit 0
  fi
else
  echo "no se pudo leer el estado del flujo vigia: se sigue como si estuviera activo"
fi

if ! vivas=$(contar_vivas); then
  sin_cuenta "primera cuenta, 5 intentos"
fi
echo "corridas vivas de la cadena (primera cuenta): $vivas"
if [ "$vivas" != "0" ]; then
  echo "la cadena siguio: nada que hacer"
  exit 0
fi

sleep "$ESPERA_RECUENTO"
if ! vivas=$(contar_vivas); then
  sin_cuenta "segunda cuenta, 5 intentos"
fi
echo "corridas vivas de la cadena (segunda cuenta): $vivas"
if [ "$vivas" != "0" ]; then
  echo "la cadena lanzo tarde: nada que hacer"
  exit 0
fi

lanzar_y_avisar
