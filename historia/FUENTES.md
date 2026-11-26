# Fuentes de historia/ (acopio A4, 25 nov 2026)

Copias de las páginas bajadas hoy (HTML y texto): `_cache_A/A4/` fuera del repositorio.

## Qué fuente dio qué archivo

- `antecedentes.csv` (44): seis documentos internos de investigación previos (precios del Fold8 del 23 oct, 13 nov y 19 nov de 2026; mercado de plegables del 23 oct; informe de consolas del 24 nov). Cada renglón cita documento y renglón, o la URL original que el documento daba. Fuera quedaron los puntos sin día exacto: preventa Fold7 jul-ago 2025 ($45,999/$41,399), pre-Buen Fin Fold7 nov 2025 ($22,916), Navidad Fold7 2025 ($20,608.56 y $19,195.50 Banorte), tienda de estudiantes Fold7 ($20,031/$21,996), Fold7 Liverpool $22,610 y Bodega $28,199, Fold8 ago-sep ($26,539/$27,530), canje jul-ago ($32,239), flash Ultra mediados de oct ($23,464), fin de oct ($26,080), principios de nov ($21,450), Buen Fin 2026 con Banorte ($20,000-22,500), estudiantes y Suburbia 13-18 nov ($32,399/$30,072/$30,899), PS5 Buen Fin 2026 ($6,818-8,133 con Santander o Banamex) y las notas de Keepa de `productos.json` (mínimo $6,900 en Black Friday 2025, típico $9,000, Hot Sale ~$8,300, $10,740 en Buen Fin 2026).
- `calendario.csv` (30): Uno TV, Xataka México, Expansión, nmas, adn40, InformaBTL, Emprendedor, Crónica (temporadas); Samsung Newsroom México (preventas y salidas Fold6/7/8); Gulf News (Fold8 global y Fold9 esperado); Ejecentral y LevelUp (cambios de precio PS5).
- `listas.csv` (24): Samsung Newsroom México (Fold6/7/8), JSON de samsung.com/mx/…/buy/ leído hoy (Fold8), Xataka, adn40, tusbuenasnoticias, Bloomberg Línea (PS5).
- `prensa.csv` (29): Xataka México, Samsung Newsroom, Chic Magazine, Posta, adn40, tusbuenasnoticias, LevelUp.

## Comparadores (petición simple con urllib, 25 nov 2026)

| Comparador | Respuesta medida | Uso |
|---|---|---|
| Keepa (gráfica pública) | 200, PNG 20,249 bytes, B0FTP523RH dominio com.mx | imagen de un año; sin serie en el cuerpo |
| MeliTrack | 200, 23,647 bytes (inicio) y 22,184 (búsqueda): cascarón sin precios | no da serie sin navegador |
| Idealo (idealo.com.mx) | 200, 222,183 bytes, título "INICIO - Idealo", trae captcha | no es el comparador europeo; sin datos |
| Google Shopping | 200, 94,199 bytes, página de reto | no se dejó |
| CamelCamelCamel | 403, 3,291 bytes (camelcamelcamel.com); mx.camelcamelcamel.com no existe (DNS) | no se dejó; no cubre México |
| Lowpi | 403, 3,297 bytes | no se dejó |
| Tiendeo | 200, 330,884 bytes | catálogos de folletos, sin historial |
| SoloTodo | 200, 1,868 bytes JSON de países: Chile, Centroamérica, Ecuador, R. Dominicana, Venezuela; sin México | no cubre México |
| Knasta | knasta.com.mx, www.knasta.com.mx y knasta.mx: el dominio no resuelve | no medido |
| Pricy | pricy.com.mx y pricy.mx: el dominio no resuelve | no medido |
| ShopMania | shopmania.com.mx: el dominio no resuelve | no medido |
