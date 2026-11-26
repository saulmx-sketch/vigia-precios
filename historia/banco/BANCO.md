Con dos años parecidos hubo 6 pronósticos que se pudieron comparar (casos F, J). El precio más bajo real cayó dentro del rango esperado en 2, por debajo de lo más bajo esperado en 4 y por encima en 0. El piso central se equivocó en promedio 29 % (sin importar el signo) y quedó por arriba del real en 6 de 6.
Con un solo año parecido hubo 8 pronósticos comparables (casos G, H, I, K): el real cayó dentro del rango en 3, debajo en 3 y encima en 2; error promedio del central 17 %, alto en 5 de 8.
Error promedio del central según la fecha del pronóstico (todos los casos comparables): 1 nov: 24 % en 3 pronósticos; 15 nov: 23 % en 6 pronósticos; 25 nov: 20 % en 5 pronósticos.
El mismo pronóstico del Fold7 acierta o falla según qué precios cuenten como reales: con solo Amazon (caso J) lo más barato fue $28,000 y el central quedó +6 %; contando la tienda de Samsung con cupón (caso F) lo más barato fue $19,985 el 23 dic 2025 y el central quedó +48 %, y ese día el vigía lo habría marcado "sin confirmar".

# Banco de prueba del pronóstico (2026-11-25)

Qué se hizo: para cada caso se armó, con los precios de Amazon México leídos de las gráficas de Keepa (y, en los Fold, también los del foro y de la prensa ya reunidos), el pronóstico que prospectiva.py habría dado el 1, el 15 y el 25 de noviembre de esa temporada, usando solo lo que se sabía hasta ese día. El pronóstico dice qué tan bajo se espera que llegue el precio de ahí al 31 de enero: un valor central y un rango (de lo más bajo a lo más alto esperado). Se compara con lo más barato que de verdad hubo después. Un año parecido es el modelo anterior (o la misma consola un año antes) en las mismas fechas, puesto a la escala del precio de hoy; solo se usa si tiene datos en al menos 15 de los 30 días previos y en 20 días del tramo que se quiere pronosticar. Con esos mismos cálculos, rehechos cada día, se simularon los avisos de ganga y de comprar. Holgura usada (la del módulo hoy): 0.12; sesgo: 0.15.

Temporada elegida para la Xbox Series S: la que más años parecidos pudo usar en sus tres fechas y, a igualdad, la de más días con precio del 1 nov al 31 ene. Por temporada (años parecidos usados en las tres fechas; días con precio): 2020-21: 0; 42; 2021-22: 2; 30; 2022-23: 0; 8; 2023-24: 0; 25; 2024-25: 0; 20; 2025-26: 0; 0. Se usó 2021-22.

Cómo leer la tabla: "Topes calculados ese día" son los del día del pronóstico; los avisos de las dos columnas siguientes salen de rehacer el cálculo cada día de la temporada con lo sabido el día anterior, así que puede haber avisos aunque el día del pronóstico no hubiera topes. "Sin confirmar" es el nivel que el vigía usa para un precio tan bajo que pide revisar quién vende.

## Resultados por caso y fecha

| Caso | Pronóstico del | Años parecidos usados | Piso esperado: bajo / central / alto | Lo más barato real después | Error del central | ¿Dónde cayó? | Topes calculados ese día (ganga / comprar / mirar) | Primer aviso de ganga | Primer aviso de comprar | ¿Avisó de comprar el día más barato? |
|---|---|---|---|---|---|---|---|---|---|---|
| F Fold7 512 GB, temporada 2025-26 | 1 nov 2025 | Fold6, Fold5 | $27,201 / $29,544 / $31,886 | $19,985 el 23 dic 2025 (Samsung) | +48 % | por debajo de lo más bajo esperado | $27,700 / $30,100 / $32,500 | $28,000 el 28 nov 2025 (+40 % sobre el real) | $28,000 el 28 nov 2025 (+40 % sobre el real) | no (ese día ya estaba en sin confirmar) |
| F Fold7 512 GB, temporada 2025-26 | 15 nov 2025 | Fold6, Fold5 | $26,461 / $29,608 / $32,756 | $19,985 el 23 dic 2025 (Samsung) | +48 % | por debajo de lo más bajo esperado | $26,900 / $30,200 / $33,400 | $28,000 el 28 nov 2025 (+40 % sobre el real) | $28,000 el 28 nov 2025 (+40 % sobre el real) | no (ese día ya estaba en sin confirmar) |
| F Fold7 512 GB, temporada 2025-26 | 25 nov 2025 | Fold6, Fold5 | $29,071 / $31,058 / $33,045 | $19,985 el 23 dic 2025 (Samsung) | +55 % | por debajo de lo más bajo esperado | $29,600 / $31,600 / $33,700 | $28,000 el 28 nov 2025 (+40 % sobre el real) | $28,000 el 28 nov 2025 (+40 % sobre el real) | no (ese día ya estaba en sin confirmar) |
| G S25 Ultra 256 GB, temporada 2025-26 | 1 nov 2025 | ninguno (G quedó sin año parecido; se previeron 2) | sin pronóstico | $21,100 el 17 dic 2025 (Amazon) | - | - | sin topes | $22,760 el 18 nov 2025 (+8 % sobre el real) | $28,600 el 12 nov 2025 (+36 % sobre el real) | no |
| G S25 Ultra 256 GB, temporada 2025-26 | 15 nov 2025 | S23 Ultra (G quedó con un año; se previeron 2) | $25,759 / $29,623 / $33,178 | $21,100 el 17 dic 2025 (Amazon) | +40 % | por debajo de lo más bajo esperado | $23,000 / $30,200 / $32,000 | $22,760 el 18 nov 2025 (+8 % sobre el real) | $28,600 el 16 nov 2025 (+36 % sobre el real) | no |
| G S25 Ultra 256 GB, temporada 2025-26 | 25 nov 2025 | ninguno (G quedó sin año parecido; se previeron 2) | sin pronóstico | $21,100 el 17 dic 2025 (Amazon) | - | - | sin topes | $21,100 el 19 dic 2025 (+0 % sobre el real) | $21,100 el 19 dic 2025 (+0 % sobre el real) | no |
| H S24 Ultra 256 GB, temporada 2024-25 | 1 nov 2024 | ninguno (H quedó sin año parecido; se previeron 1) | sin pronóstico | $17,610 el 12 nov 2024 (Amazon) | - | - | sin topes | no hubo | $17,610 el 13 nov 2024 (+0 % sobre el real) | no (ese día ya estaba en mirar) |
| H S24 Ultra 256 GB, temporada 2024-25 | 15 nov 2024 | S23 Ultra | $15,381 / $17,688 / $19,810 | $20,030 el 22 ene 2025 (Amazon) | -12 % | por encima de lo más alto esperado | $15,600 / $18,000 / $19,100 | no hubo | no hubo | no |
| H S24 Ultra 256 GB, temporada 2024-25 | 25 nov 2024 | S23 Ultra | $15,297 / $17,591 / $19,702 | $20,030 el 22 ene 2025 (Amazon) | -12 % | por encima de lo más alto esperado | $15,600 / $17,900 / $19,100 | no hubo | no hubo | no |
| I Fold6 512 GB, temporada 2024-25 | 1 nov 2024 | Fold5 | $31,874 / $36,655 / $41,054 | $31,270 el 16 nov 2024 (Amazon) | +17 % | por debajo de lo más bajo esperado | $32,500 / $37,300 / $41,800 | $31,270 el 16 nov 2024 (+0 % sobre el real) | $35,010 el 12 nov 2024 (+12 % sobre el real) | sí |
| I Fold6 512 GB, temporada 2024-25 | 15 nov 2024 | Fold5 | $33,660 / $38,709 / $43,354 | $31,270 el 16 nov 2024 (Amazon) | +24 % | por debajo de lo más bajo esperado | $34,300 / $39,400 / $42,100 | $31,270 el 16 nov 2024 (+0 % sobre el real) | $31,270 el 16 nov 2024 (+0 % sobre el real) | sí |
| I Fold6 512 GB, temporada 2024-25 | 25 nov 2024 | Fold5 | $31,130 / $35,800 / $40,096 | $31,590 el 8 dic 2024 (Amazon) | +13 % | dentro del rango | $31,200 / $36,500 / $38,800 | no hubo | $35,010 el 3 dic 2024 (+11 % sobre el real) | sí |
| J Fold7 512 GB, temporada 2025-26, solo Amazon | 1 nov 2025 | Fold6, Fold5 | $27,201 / $29,544 / $31,886 | $28,000 el 28 nov 2025 (Amazon) | +6 % | dentro del rango | $27,700 / $30,100 / $32,500 | $28,000 el 28 nov 2025 (+0 % sobre el real) | $28,000 el 28 nov 2025 (+0 % sobre el real) | sí |
| J Fold7 512 GB, temporada 2025-26, solo Amazon | 15 nov 2025 | Fold6, Fold5 | $26,461 / $29,608 / $32,756 | $28,000 el 28 nov 2025 (Amazon) | +6 % | dentro del rango | $26,900 / $30,200 / $33,400 | $28,000 el 28 nov 2025 (+0 % sobre el real) | $28,000 el 28 nov 2025 (+0 % sobre el real) | sí |
| J Fold7 512 GB, temporada 2025-26, solo Amazon | 25 nov 2025 | Fold6, Fold5 | $29,071 / $31,058 / $33,045 | $28,000 el 28 nov 2025 (Amazon) | +11 % | por debajo de lo más bajo esperado | $29,600 / $31,600 / $33,700 | $28,000 el 28 nov 2025 (+0 % sobre el real) | $28,000 el 28 nov 2025 (+0 % sobre el real) | sí |
| K Xbox Series S, temporada 2021-22 | 1 nov 2021 | ninguno (K quedó sin año parecido; se previeron 2) | sin pronóstico | $6,490 el 19 nov 2021 (Amazon) | - | - | sin topes | no hubo | $6,700 el 13 nov 2021 (+3 % sobre el real) | no (ese día ya estaba en comprar) |
| K Xbox Series S, temporada 2021-22 | 15 nov 2021 | Series S un año antes (K quedó con un año; se previeron 2) | $6,050 / $6,958 / $7,793 | $6,490 el 19 nov 2021 (Amazon) | +7 % | dentro del rango | $6,100 / $7,000 / $7,300 | no hubo | $6,730 el 18 nov 2021 (+4 % sobre el real) | no (ese día ya estaba en comprar) |
| K Xbox Series S, temporada 2021-22 | 25 nov 2021 | Series S un año antes (K quedó con un año; se previeron 2) | $5,278 / $6,070 / $6,798 | $6,490 el 26 nov 2021 (Amazon) | -6 % | dentro del rango | $5,300 / $6,100 / $6,300 | no hubo | no hubo | no |

Años parecidos que no se pudieron usar, y por qué:

- G, 1 nov 2025: S24 Ultra: solo 15 dias con dato del 2024-11-03 al 2025-02-01 (hacen falta 20)
- G, 1 nov 2025: S23 Ultra: solo 13 de los 30 dias que terminan el 2023-11-04 tienen dato (hacen falta 15)
- G, 15 nov 2025: S24 Ultra: solo 8 dias con dato del 2024-11-17 al 2025-02-01 (hacen falta 20)
- G, 25 nov 2025: S24 Ultra: solo 13 de los 30 dias que terminan el 2024-11-26 tienen dato (hacen falta 15)
- G, 25 nov 2025: S23 Ultra: solo 14 de los 30 dias que terminan el 2023-11-28 tienen dato (hacen falta 15)
- H, 1 nov 2024: S23 Ultra: solo 12 de los 30 dias que terminan el 2023-11-03 tienen dato (hacen falta 15)
- K, 1 nov 2021: Series S un año antes: solo 7 de los 30 dias que terminan el 2020-11-02 tienen dato (hacen falta 15)
- K, 1 nov 2021: Series S dos años antes: solo 0 de los 30 dias que terminan el 2019-11-04 tienen dato (hacen falta 15)
- K, 15 nov 2021: Series S dos años antes: solo 0 de los 30 dias que terminan el 2019-11-18 tienen dato (hacen falta 15)
- K, 25 nov 2021: Series S dos años antes: solo 0 de los 30 dias que terminan el 2019-11-28 tienen dato (hacen falta 15)

## Sensibilidad: el pronóstico de hoy del Fold8 Ultra 512 GB (25 nov 2026)

| Variante | Años parecidos usados | Bajo | Central | Alto | Cambio del central contra la base |
|---|---|---|---|---|---|
| Base (lo que usa prospectiva.py: Fold7 a 364 días y Fold6 a 728) | Fold7 ($19,680), Fold6 ($28,439) | $19,680 | $24,059 | $28,439 | +0 (+0 %) |
| (a) se añade el Fold5 a 1,092 días | Fold7 ($19,680), Fold6 ($28,439), Fold5 ($32,327) | $19,680 | $28,439 | $32,327 | +4380 (+18 %) |
| (b) se quita el Fold6 | Fold7 ($19,680) | $17,113 | $19,680 | $22,041 | -4380 (-18 %) |
| (c) se quita el Fold7 | Fold6 ($28,439) | $24,729 | $28,439 | $31,852 | +4380 (+18 %) |
| (d) desfases 7 días antes (357 y 721) | Fold7 ($20,455), Fold6 ($32,492) | $20,455 | $26,474 | $32,492 | +2414 (+10 %) |
| (d) desfases 7 días después (371 y 735) | Fold7 ($18,768), Fold6 ($25,886) | $18,768 | $22,327 | $25,886 | -1732 (-7 %) |

Con un solo año parecido el rango ya no sale de dos años: lo más bajo esperado es el central entre (1 + sesgo) y lo más alto, el central por (1 + holgura).

## Límites

1. Son 6 casos (4 con dos años parecidos previstos) y 18 pronósticos en total; las tres fechas de un mismo caso comparten la misma temporada, así que no son pruebas independientes, y F y J son el mismo pronóstico comparado contra dos versiones del precio real (con y sin la tienda de Samsung): con dos años parecidos hay, en el fondo, una sola temporada probada, la del Fold7 2025-26.
2. Las series de Amazon no traen los cupones de la tienda de Samsung ni sus precios con descuento: el precio más bajo que de verdad se pudo pagar puede haber sido menor que el registrado.
3. Los precios salen de leer las gráficas por píxeles: fold5 ± $54 a $54, s23ultra ± $20 a $54, s24ultra ± $81 a $81, s25ultra ± $13 a $41, xboxs ± $15 a $17, xboxx ± $33 a $33, y las fechas ± 1 día.
4. S23 Ultra Lavanda: Amazon lo tiene fijo en $31,000 desde febrero de 2025, con el modelo ya viejo (parece precio de catálogo que nadie compra). Aquí no pesa: el S23 solo se usa como año parecido de temporadas 2023-24.
5. Xbox Series S: el mínimo de $4,540 (nov 2023) no se confirmó a ojo en la gráfica; desde 2022 Amazon aparece en días sueltos, así que esas temporadas se apoyan en pocos días.
6. Los S23 Ultra se anuncian como "Desbloqueado"; se aceptaron porque los vende Amazon con formato de título de Samsung México y precio mexicano (duda 1 de C9a).
7. Las listas de la Xbox Series S nueva ($7,300) y del S25 Ultra Jet Black ($32,990) son el arranque de esa ficha, no el lanzamiento del aparato (no se usan en este cálculo, que no depende de la lista).
8. Los pisos de cordura de los productos nuevos (Fold5 $12,000, S Ultra $8,000, Xbox $2,500 y $5,000) los fijó esta prueba; un precio por debajo se tira como lectura mala.
