# Respuestas grabadas (pruebas sin red)

Medidas el 25 nov 2026 desde una conexion de casa, con una peticion simple de `vigia.pedir`, y recortadas a lo que
leen las funciones. Las usa `pruebas/casos_confirmacion.py`.

| Archivo | De donde sale | Tocado a mano |
|---|---|---|
| `liv_pdp_1156059630.html` | ficha de Liverpool del PlayStation Portal ("Consola PS5", $5,999) | solo recortada: los datos de Next.js (`self.__next_f.push`) reducidos al articulo y sus variantes |
| `liv_pdp_1203150349.html` | ficha de Liverpool del Fold8 512 GB (3 colores, $44,999, vende Liverpool) | solo recortada |
| `liv_pdp_1207352230_alterada.html` | ficha real de un tercero de su marketplace (PATISH, PS5 Wolverine $19,999) | ALTERADA: titulo cambiado a "Consola PS5 Digital..." y precio a $8,999, para que pase titulo y piso y se pruebe que el tercero no se publica |
| `liv_busca_ps5.html` | busqueda de Liverpool "playstation+5": renglones reales del Portal y del bundle | AGREGADOS dos renglones inventados: el Portal titulado "Consola PS5 Digital" a $6,999 y el tercero de arriba a $8,999 |
| `liv_busca_fold8.html` | busqueda de Liverpool "galaxy+z+fold8" | solo recortada |
| `amz_busca_ps5.html` | bloque real de la busqueda de Amazon del articulo B0FTP523RH | ALTERADO el precio de la busqueda ($12,499 a $8,999), para probar que manda el de la ficha |
| `amz_ficha_B0FTP523RH.html` | ficha real de Amazon (PS5 Digital, $12,499, vende Amazon Mexico) | solo recortada (titulo, precio a pagar, caja de compra) |
| `ele_api_1302051913.json` | API de Elektra por articulo (Fold8 512 Lavanda, vende Systore $26,999) | solo recortada |
| `cos_api_648324.json` | API de producto de Costco (Fold8 512 Grafito, $44,999 - $7,000) | solo recortada |
| `gp_pdp_fortnite.html` | ficha de Gameplanet (ld+json) | descripcion recortada |
| `sam_catalogo.json` | catalogo de Samsung de los 10 modelos de productos.json | solo recortado |
