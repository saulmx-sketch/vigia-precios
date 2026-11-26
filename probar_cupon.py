"""Prueba al momento uno o varios codigos de cupon en el carrito de Samsung para cada variante vigilada:
el cupon solo, y combinado con los que ya trae cupones_samsung.txt (el resultado no depende del orden).

Uso:  python probar_cupon.py CODIGO [OTRO_CODIGO ...]
Sirve cuando llega un aviso de la app o una captura con un codigo nuevo: dice en un minuto si mejora algo.
Arreglo 25-nov-2026: samsung_carrito devuelve 6 valores (base, total, puestos, fin, probados, solos); antes se
desempacaban 4 y todo SKU caia en "no se pudo armar carrito".
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
# junto a vigia.py se usa ese; si se corre desde otra carpeta, la copia de trabajo de la linea B
sys.path.insert(0, AQUI if os.path.exists(os.path.join(AQUI, "vigia.py")) else r"C:\Users\searm\vigia-lineas\B")
import vigia  # noqa: E402

if __name__ == "__main__":
    nuevos = [c.strip().upper() for c in sys.argv[1:]]
    if not nuevos:
        sys.exit(__doc__)
    for prod in vigia.cargar_config():
        cfg = prod["tiendas"].get("Samsung")
        if not cfg:
            continue
        conocidos = [c for c in vigia.cupones_candidatos(prod, {}) if c not in nuevos]
        vistos = set()
        for sku, (vid, color, _pagina) in cfg["skus"].items():
            if vid in vistos:
                continue
            nombre = vigia.var_por_id(prod, vid)["nombre"]
            try:
                base, solo, puestos_solo, _, _, _ = vigia.samsung_carrito(sku, nuevos)
            except Exception as ex:   # variante que no esta a la venta: se prueba con el siguiente SKU
                print("%-34s %s: no se pudo armar carrito (%s)" % (nombre, sku, str(ex)[:50]))
                continue
            vistos.add(vid)
            _, sin, puestos_sin, _, _, _ = vigia.samsung_carrito(sku, conocidos)
            _, con, puestos_con, _, _, _ = vigia.samsung_carrito(sku, conocidos + nuevos)
            veredicto = "MEJORA ${:,.0f}".format(sin - con) if con < sin - 1 else "no mejora"
            print("%-34s sin cupon %9.2f | solo el nuevo %9.2f %s | mejor sin el %9.2f %s | mejor con el %9.2f %s -> %s"
                  % (nombre, base, solo, puestos_solo, sin, puestos_sin, con, puestos_con, veredicto))
