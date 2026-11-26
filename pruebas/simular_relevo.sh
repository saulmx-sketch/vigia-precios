#!/usr/bin/env bash
# Simula .github/scripts/relevo.sh con un gh falso (pruebas/gh_falso/gh) en ocho escenarios y compara
# lo que hizo con lo esperado. Sin red. Uso (Git Bash o Linux):  bash pruebas/simular_relevo.sh
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR_SIM:-/tmp}/relevo_sim.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
chmod +x "$RAIZ/pruebas/gh_falso/gh" 2>/dev/null
export PATH="$RAIZ/pruebas/gh_falso:$PATH"
export GITHUB_REPOSITORY="falso/vigia-precios" GITHUB_REPOSITORY_OWNER="duenofalso"
export ESPERA_REINTENTO=0 ESPERA_RECUENTO=0
TAB=$'\t'
ULT="ultima=111${TAB}https://github.com/falso/vigia-precios/actions/runs/111${TAB}failure${TAB}2026-11-25T15:30:00Z"
fallos=0

# caso NOMBRE ESCENARIO SALIDA_ESPERADA LANZA(0|1) ISSUE(0|1) TEXTO_EN_SALIDA [VARIABLES...]
caso() {
  local nombre="$1" esc="$2" sal_esp="$3" lanza_esp="$4" issue_esp="$5" texto="$6"; shift 6
  export FALSO_DIR="$TMP/$nombre"; mkdir -p "$FALSO_DIR"
  export ESCENARIO="$FALSO_DIR/escenario" FALSO_LOG="$FALSO_DIR/log"
  printf '%s\n' "$esc" > "$ESCENARIO"; : > "$FALSO_LOG"
  local salida sal
  salida=$(env "$@" bash "$RAIZ/.github/scripts/relevo.sh" 2>&1); sal=$?
  local lanzo=0 abrio=0
  grep -q "^gh workflow run" "$FALSO_LOG" && lanzo=1
  grep -q "^gh issue create" "$FALSO_LOG" && abrio=1
  local ok=1 motivo=""
  [ "$sal" = "$sal_esp" ] || { ok=0; motivo="$motivo salida=$sal(esperada $sal_esp)"; }
  [ "$lanzo" = "$lanza_esp" ] || { ok=0; motivo="$motivo lanzo=$lanzo(esperado $lanza_esp)"; }
  [ "$abrio" = "$issue_esp" ] || { ok=0; motivo="$motivo issue=$abrio(esperado $issue_esp)"; }
  # El texto se busca en lo que imprimio y en lo que le paso a gh (el cuerpo del aviso va ahi).
  if [ -n "$texto" ] && ! { printf '%s\n' "$salida"; cat "$FALSO_LOG"; } | grep -Fq -- "$texto"; then
    ok=0; motivo="$motivo falta el texto [$texto]"
  fi
  if [ "$ok" = 1 ]; then
    echo "OK    $nombre (salida $sal, lanzo $lanzo, issue $abrio)"
  else
    echo "FALLA $nombre:$motivo"; echo "$salida" | sed 's/^/      | /'; fallos=$((fallos + 1))
  fi
}

caso 1_otra_viva          "cuentas=1"                         0 0 0 "la cadena siguio"
caso 2_lanza_bien         "$(printf 'cuentas=0 0\nlanza=ok\n%s' "$ULT")" 0 1 1 "aviso abierto: La cadena del vigía se detuvo y se relanzó sola"
caso 3_lanza_falla_5      "$(printf 'cuentas=0 0\nlanza=falla\n%s' "$ULT")" 1 1 1 "aviso abierto: El vigía está detenido: no se pudo relanzar"
caso 4_consulta_falla     "cuentas=x x x x x"                 1 0 0 "no se pudo contar"
caso 5_cadena_lanzo_tarde "cuentas=0 1"                       0 0 0 "la cadena lanzo tarde"
caso 6_aviso_ya_abierto   "$(printf 'cuentas=0 0\nlanza=ok\nabiertos=Otro aviso\nabiertos=La cadena del vigía se detuvo y se relanzó sola\n%s' "$ULT")" 0 1 0 "aviso ya abierto, no se repite"
caso 7_cancelada          "$(printf 'cuentas=0 0\nlanza=ok')" 0 1 1 "se canceló (a mano o porque llegó a su tope de tiempo)" \
  TERMINO_ID=222 TERMINO_URL=https://github.com/falso/vigia-precios/actions/runs/222 TERMINO_CONCLUSION=cancelled TERMINO_HORA=2026-11-25T15:30:00Z TERMINO_EVENTO=workflow_dispatch
caso 8_tope_de_tiempo     "$(printf 'cuentas=0 0\nlanza=ok')" 0 1 1 "se cortó por tope de tiempo" \
  TERMINO_ID=333 TERMINO_URL=https://github.com/falso/vigia-precios/actions/runs/333 TERMINO_CONCLUSION=timed_out TERMINO_HORA=2026-11-25T15:30:00Z TERMINO_EVENTO=workflow_dispatch

# Extra: en seco no lanza ni abre, pero dice lo que haria.
caso 9_seco               "$(printf 'cuentas=0 0\nlanza=ok\n%s' "$ULT")" 0 0 0 "[seco] lanzaria" RELEVO_SECO=1

# Extra: el aviso del caso 2 nombra la corrida, su liga, su conclusion, la hora de Mexico y al dueno.
for t in "la número 111" "actions/runs/111" "falló el 25-11-2026 a las 09:30 (hora de la Ciudad de México)" "@duenofalso"; do
  grep -Fq -- "$t" "$TMP/2_lanza_bien/log" || { echo "FALLA aviso del caso 2 sin [$t]"; fallos=$((fallos+1)); }
done
n=$(grep -c "^gh workflow run" "$TMP/3_lanza_falla_5/log")
[ "$n" = 5 ] && echo "OK    3_lanza_falla_5 intento lanzar $n veces" || { echo "FALLA 3_lanza_falla_5 intento $n veces (esperadas 5)"; fallos=$((fallos+1)); }
n=$(grep -c "^gh run list --repo [^ ]* --workflow vigia.yml --limit" "$TMP/4_consulta_falla/log")
[ "$n" = 5 ] && echo "OK    4_consulta_falla consulto $n veces" || { echo "FALLA 4_consulta_falla consulto $n veces (esperadas 5)"; fallos=$((fallos+1)); }
echo "--- cuerpo del aviso del caso 2 (lo que recibiria gh issue create) ---"
grep "^gh issue create" -A12 "$TMP/2_lanza_bien/log" | sed 's/^/      | /'
echo
if [ "$fallos" = 0 ]; then echo "simulacion del relevo: todos los casos OK"; else echo "simulacion del relevo: $fallos caso(s) fallaron"; fi
[ "$fallos" = 0 ]
