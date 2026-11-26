La prueba hacia atrás no alcanza para certificar topes calculados: en el único caso probado sin conocer la respuesta (Fold7 2025-26, un solo año parecido) el piso pronosticado quedó 32 % arriba del mínimo real y la primera etiqueta de ganga, 40 % arriba; para la PS5 2025-26 no había año parecido con datos. Por eso las etiquetas de ganga y comprar siguen siendo las de a mano y solo se amplía el tope de mirar donde hay dos años parecidos (Fold8).

# Prueba hacia atrás de prospectiva.py (2026-11-25)

Cada día de la temporada se calcularon los topes solo con lo que se sabía antes de ese día (datos de fecha anterior), y se miró el precio válido más barato de ese día. Hay aviso cuando el precio entra a un nivel (mirar, comprar, ganga, sin confirmar) o cuando, ya avisado, baja 300 pesos o más. Un aviso de "sin confirmar" también es un correo enviado, pero no cuenta como compra. Se comparan tres juegos de topes: "a mano" (porcentajes fijos de la lista), "calculado" (la terna completa del pronóstico; los días sin año parecido no hay topes calculados y se cuentan aparte) y "activo" (lo que hoy usa el vigía: ganga y comprar a mano, y mirar ampliado solo con dos años parecidos). "Con sobreprecio" = el primer aviso de ganga quedó más de 10 % arriba del mínimo real de la temporada. VERDE/ROJO por caso usa la vara de la fase 2: informa, no decide.

Holgura entre topes (s): 0.120; sesgo del piso: 0.150 (de 2 errores del piso: media sin signo 0.164 (s acotada entre 0.05 y 0.12), media con signo +0.164 (sesgo acotado a 0.15)).

## Caso A: Fold7 512 GB, temporada 2025-26

- Lo más barato de la temporada: $19,985 el 23 dic 2025 en Samsung (historia/foro_fold7.csv:1120831).
- Piso pronosticado el 2025-11-15: $26,461 (analogo); lo más bajo real después: $19,985 el 2025-12-23, error +32.4 %.
- Topes a mano: VERDE. 2 avisos (mirar 1, ganga 1).
  - Avisos: 2025-12-23 ganga $19,985; 2026-01-06 mirar $22,539.
  - Cazó el mínimo.
  - Primer aviso de ganga: 2025-12-23, $19,985 (ganga), 0 % arriba del mínimo real.
  - Primer aviso de comprar o ganga: 2025-12-23, $19,985 (ganga), 0 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $19,985 el 2025-12-23, $0 más que el mínimo.
- Topes calculado: VERDE con sobreprecio. 5 avisos (comprar 3, ganga 2).
  - Avisos: 2025-11-28 comprar $28,000; 2025-12-04 comprar $28,000; 2025-12-16 ganga $28,000; 2025-12-23 ganga $19,985; 2026-01-06 comprar $22,539.
  - Cazó el mínimo.
  - Primer aviso de ganga: 2025-12-16, $28,000 (ganga), 40 % arriba del mínimo real.
  - Primer aviso de comprar o ganga: 2025-11-28, $28,000 (comprar), 40 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $28,000 el 2025-11-28, $8,015 más que el mínimo.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.
  - Días sin topes calculados (sin año parecido utilizable o terna fuera de orden): 19.
- Topes activo: VERDE. 2 avisos (mirar 1, ganga 1).
  - Avisos: 2025-12-23 ganga $19,985; 2026-01-06 mirar $22,539.
  - Cazó el mínimo.
  - Primer aviso de ganga: 2025-12-23, $19,985 (ganga), 0 % arriba del mínimo real.
  - Primer aviso de comprar o ganga: 2025-12-23, $19,985 (ganga), 0 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $19,985 el 2025-12-23, $0 más que el mínimo.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.

## Caso B: PS5 Digital, temporada 2025-26

- Lo más barato de la temporada: $6,675 el 1 dic 2025 en Walmart (historia/foro_ps5.csv:1112722).
- Piso pronosticado el 2025-11-15: $6,699 (estimacion); lo más bajo real después: $6,675 el 2025-12-01, error +0.4 %.
- Topes a mano: VERDE. 5 avisos (mirar 2, comprar 2, ganga 1).
  - Avisos: 2025-11-04 comprar $6,699; 2025-11-11 mirar $7,780; 2025-11-12 mirar $7,370; 2025-11-13 comprar $7,150; 2025-11-17 ganga $6,900.
  - Cazó el mínimo.
  - Primer aviso de ganga: 2025-11-17, $6,900 (ganga), 3 % arriba del mínimo real.
  - Primer aviso de comprar o ganga: 2025-11-04, $6,699 (comprar), 0 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $6,699 el 2025-11-04, $24 más que el mínimo.
- Topes calculado: ROJO (no aviso de comprar o ganga el dia del minimo ni antes a menos de 5 % de el). 0 avisos (ninguno).
  - No cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: no hubo.
  - Nunca avisó de comprar ni de ganga.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.
  - Días sin topes calculados (sin año parecido utilizable o terna fuera de orden): 109.
- Topes activo: VERDE. 5 avisos (mirar 2, comprar 1, ganga 1, sin confirmar 1).
  - Avisos: 2025-11-04 sin confirmar $6,699; 2025-11-11 mirar $7,780; 2025-11-12 mirar $7,370; 2025-11-13 comprar $7,150; 2025-11-17 ganga $6,900.
  - Cazó el mínimo.
  - Primer aviso de ganga: 2025-11-17, $6,900 (ganga), 3 % arriba del mínimo real.
  - Primer aviso de comprar o ganga: 2025-11-13, $7,150 (comprar), 7 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $7,150 el 2025-11-13, $475 más que el mínimo.
  - Avisos de "sin confirmar" enviados: 1. Días con un precio válido real en "sin confirmar": 2025-11-04 $6,699.

## Caso C: Fold8 Ultra 512 GB, del 15 oct 2026 a hoy

- Lo más barato de la temporada: $23,464 el 15 oct 2026 en Samsung (historia/foro_fold8.csv:1198861).
- Piso pronosticado el 2026-11-15: $21,897 (analogo); lo más bajo real después: $32,399 el 2026-11-23, error -32.4 % (parcial: la temporada no ha terminado).
- Topes a mano: VERDE. 1 avisos (comprar 1).
  - Avisos: 2026-10-15 comprar $23,464.
  - Cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: 2026-10-15, $23,464 (comprar), 0 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $23,464 el 2026-10-15, $0 más que el mínimo.
- Topes calculado: ROJO (no aviso de comprar o ganga el dia del minimo ni antes a menos de 5 % de el). 1 avisos (mirar 1).
  - Avisos: 2026-10-15 mirar $23,464.
  - No cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: no hubo.
  - Nunca avisó de comprar ni de ganga.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.
- Topes activo: VERDE. 1 avisos (comprar 1).
  - Avisos: 2026-10-15 comprar $23,464.
  - Cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: 2026-10-15, $23,464 (comprar), 0 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $23,464 el 2026-10-15, $0 más que el mínimo.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.

## Caso D: Fold8 512 GB, del 15 oct 2026 a hoy

- Lo más barato de la temporada: $31,020 el 11 nov 2026 en Amazon (historia/keepa_fold8.csv:B0H773GGWG:2026-11-11).
- Piso pronosticado el 2026-11-15: $21,283 (analogo); lo más bajo real después: $31,639 el 2026-11-19, error -32.7 % (parcial: la temporada no ha terminado).
- Topes a mano: ROJO (no aviso de comprar o ganga el dia del minimo ni antes a menos de 5 % de el). 0 avisos (ninguno).
  - No cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: no hubo.
  - Nunca avisó de comprar ni de ganga.
- Topes calculado: ROJO (no aviso de comprar o ganga el dia del minimo ni antes a menos de 5 % de el). 0 avisos (ninguno).
  - No cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: no hubo.
  - Nunca avisó de comprar ni de ganga.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.
- Topes activo: ROJO (no aviso de comprar o ganga el dia del minimo ni antes a menos de 5 % de el). 0 avisos (ninguno).
  - No cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: no hubo.
  - Nunca avisó de comprar ni de ganga.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.

## Caso E: PS5 Digital, del 15 oct 2026 a hoy

- Lo más barato de la temporada: $7,717 el 13 nov 2026 en Walmart (historia/foro_ps5.csv:1208849).
- Piso pronosticado el 2026-11-15: $7,187 (analogo); lo más bajo real después: $10,730 el 2026-11-16, error -33.0 % (parcial: la temporada no ha terminado).
- Topes a mano: VERDE. 1 avisos (comprar 1).
  - Avisos: 2026-11-13 comprar $7,717.
  - Cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: 2026-11-13, $7,717 (comprar), 0 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $7,717 el 2026-11-13, $0 más que el mínimo.
- Topes calculado: ROJO (no aviso de comprar o ganga el dia del minimo ni antes a menos de 5 % de el). 1 avisos (mirar 1).
  - Avisos: 2026-11-24 mirar $11,000.
  - No cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: no hubo.
  - Nunca avisó de comprar ni de ganga.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.
  - Días sin topes calculados (sin año parecido utilizable o terna fuera de orden): 36.
- Topes activo: VERDE. 1 avisos (comprar 1).
  - Avisos: 2026-11-13 comprar $7,717.
  - Cazó el mínimo.
  - Primer aviso de ganga: no hubo.
  - Primer aviso de comprar o ganga: 2026-11-13, $7,717 (comprar), 0 % arriba del mínimo real.
  - Quien compra en el primer aviso de comprar o ganga paga $7,717 el 2026-11-13, $0 más que el mínimo.
  - Avisos de "sin confirmar" enviados: 0. Días con un precio válido real en "sin confirmar": ninguno.

## Límites de esta prueba

1. Son dos temporadas (Fold7 2025-26 y PS5 2025-26): sirve para detectar un método malo, no para certificar uno bueno.
2. Los topes a mano se calibraron en noviembre de 2026 viendo ya la historia del Fold7, así que su resultado en el caso A no es una prueba limpia (conocían la respuesta); el método nuevo sí se probó sin verla, usando solo al Fold6.
3. La serie de Amazon sale de una gráfica leída por píxeles (± $50, ± 1 día) y los precios de la tienda de Samsung con cupón solo se conocen por notas de prensa de días sueltos, así que el mínimo real de una temporada puede haber sido más bajo de lo registrado.
4. No existe ninguna temporada pasada con dos años parecidos con datos, que es la situación del Fold8 hoy: ese caso no se pudo probar hacia atrás.
