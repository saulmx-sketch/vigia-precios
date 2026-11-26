#!/usr/bin/env bash
# Avisos del vigia (issues que le llegan al dueno como correo), sin repetir: si ya hay uno abierto con el
# mismo titulo, no abre otro. Lo usan relevo.sh, el trabajo "salvavidas" y el paso de pruebas de vigia.yml.
#
#   bash .github/scripts/aviso.sh relanzada            la cadena se detuvo y se relanzo sola
#   bash .github/scripts/aviso.sh detenida             la cadena esta detenida y no se pudo relanzar
#   bash .github/scripts/aviso.sh libre "Titulo" "Cuerpo"
#
# Datos de la corrida que termino sin sucesora (opcionales; si faltan se busca la ultima terminada):
#   TERMINO_ID, TERMINO_URL, TERMINO_CONCLUSION, TERMINO_HORA (ISO 8601 UTC), TERMINO_EVENTO
# RELEVO_SECO=1: no abre nada; imprime lo que haria.
# ESPERA_REINTENTO: segundos base entre reintentos (15 por defecto; la simulacion lo pone en 0).

TITULO_RELANZADA="La cadena del vigía se detuvo y se relanzó sola"
TITULO_DETENIDA="El vigía está detenido: no se pudo relanzar"

: "${ESPERA_REINTENTO:=15}"

# Hora de la Ciudad de Mexico (UTC-6 fijo: sin horario de verano desde 2022).
hora_mx() {
  local iso="$1" s
  s=$(date -u -d "$iso" +%s 2>/dev/null) || { echo "$iso"; return; }
  date -u -d "@$((s - 21600))" '+%d-%m-%Y a las %H:%M (hora de la Ciudad de México)'
}

conclusion_en_lego() {
  case "$1" in
    success) echo "terminó bien, pero no dejó lanzada la siguiente" ;;
    failure) echo "falló" ;;
    cancelled) echo "se canceló (a mano o porque llegó a su tope de tiempo)" ;;
    timed_out) echo "se cortó por tope de tiempo" ;;
    "") echo "terminó (GitHub no dijo cómo)" ;;
    *) echo "terminó con el estado \"$1\"" ;;
  esac
}

# Llena TERMINO_* con la ultima corrida terminada de la cadena (no de horario) si no vinieron dados.
completar_termino() {
  if [ -n "$TERMINO_ID" ] && [ "$TERMINO_EVENTO" != "schedule" ]; then
    return 0
  fi
  local linea i
  for i in 1 2 3; do
    linea=$(gh run list --repo "$GITHUB_REPOSITORY" --workflow vigia.yml --status completed --limit 30 \
      --json databaseId,url,conclusion,updatedAt,event \
      --jq '[.[] | select(.event != "schedule")][0] | select(. != null) | "\(.databaseId)\t\(.url)\t\(.conclusion)\t\(.updatedAt)"') && break
    linea=""
    [ "$i" -lt 3 ] && sleep $((i * ESPERA_REINTENTO))
  done
  if [ -n "$linea" ] && [ "${linea%%$'\t'*}" != "null" ]; then
    IFS=$'\t' read -r TERMINO_ID TERMINO_URL TERMINO_CONCLUSION TERMINO_HORA <<< "$linea"
  else
    TERMINO_ID=""; TERMINO_URL=""; TERMINO_CONCLUSION=""; TERMINO_HORA=""
  fi
}

parrafo_termino() {
  if [ -n "$TERMINO_ID" ]; then
    echo "La última corrida de la cadena, la número $TERMINO_ID, $(conclusion_en_lego "$TERMINO_CONCLUSION") el $(hora_mx "$TERMINO_HORA"). Liga: $TERMINO_URL"
  else
    echo "No pude saber cuál fue la última corrida de la cadena (GitHub no respondió la consulta)."
  fi
}

# abrir_aviso "titulo" "cuerpo": abre el issue salvo que ya haya uno abierto con el mismo titulo.
abrir_aviso() {
  local titulo="$1" cuerpo="$2" abiertos i
  cuerpo="@$GITHUB_REPOSITORY_OWNER

$cuerpo"
  abiertos=""
  for i in 1 2 3; do
    # Busqueda por titulo (no los 100 mas nuevos: en Black Friday los avisos de precio lo desplazarian);
    # la busqueda es aproximada, la igualdad exacta la decide el grep de abajo.
    abiertos=$(gh issue list --repo "$GITHUB_REPOSITORY" --state open --limit 100 --search "\"$titulo\" in:title" \
      --json title --jq '.[].title') && break
    abiertos=""
    [ "$i" -lt 3 ] && sleep $((i * ESPERA_REINTENTO))
  done
  if printf '%s\n' "$abiertos" | grep -Fxq -- "$titulo"; then
    echo "aviso ya abierto, no se repite: $titulo"
    return 0
  fi
  if [ "${RELEVO_SECO:-}" = "1" ]; then
    echo "[seco] abriria el aviso: $titulo"
    printf '%s\n' "$cuerpo" | sed 's/^/[seco]   /'
    return 0
  fi
  for i in 1 2 3; do
    if gh issue create --repo "$GITHUB_REPOSITORY" --title "$titulo" --body "$cuerpo"; then
      echo "aviso abierto: $titulo"
      return 0
    fi
    [ "$i" -lt 3 ] && sleep $((i * ESPERA_REINTENTO))
  done
  echo "no se pudo abrir el aviso: $titulo"
  return 1
}

aviso_relanzada() {
  completar_termino
  abrir_aviso "$TITULO_RELANZADA" "La cadena del vigía de precios (la que revisa las tiendas cada ~15 minutos) se había detenido: ninguna corrida quedó esperando a la siguiente. Ya se relanzó sola; no tienes que hacer nada.

$(parrafo_termino)

Quién la relanzó: ${QUIEN_RELANZA:-el relevo automático}. Cierra este aviso cuando lo hayas leído; si vuelve a abrirse seguido, algo en GitHub está fallando."
}

aviso_detenida() {
  completar_termino
  abrir_aviso "$TITULO_DETENIDA" "La cadena del vigía de precios está detenida y no se pudo relanzar sola (GitHub rechazó el pedido cinco veces). Mientras siga así no se revisan precios.

$(parrafo_termino)

Para relanzarlo a mano: en el repositorio, Actions > vigia > Run workflow (botón verde).

Quién lo intentó: ${QUIEN_RELANZA:-el relevo automático}."
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "$1" in
    relanzada) aviso_relanzada ;;
    detenida) aviso_detenida ;;
    libre) abrir_aviso "$2" "$3" ;;
    *) echo "uso: aviso.sh relanzada|detenida|libre \"titulo\" \"cuerpo\""; exit 2 ;;
  esac
fi
