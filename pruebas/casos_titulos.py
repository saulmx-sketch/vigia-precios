# Titulos reales de las tiendas contra las reglas de productos.json.
# Lo corre `vigia.py --selftest` (funcion correr). A mano:  py -X utf8 pruebas\casos_titulos.py
# imprime ademas que cae en cada variante, con su precio, y lo descartado.
# Cada caso: {"tienda", "producto", "titulo", "precio", "vendedor", "espera", "origen"} y, si hace falta,
# "importado": true, o "espera_hoy": lo que el programa da hoy cuando no coincide con "espera" (hallazgo a la vista).
import json
import os

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)


def resolver(v):
    prods = {p["id"]: p for p in v.cargar_config(os.path.join(RAIZ, "productos.json"))}
    with open(os.path.join(AQUI, "casos_titulos.json"), encoding="utf-8") as f:
        casos = json.load(f)
    filas = []
    # Casos con tienda "sin dato" solo prueban el titulo (venian de variante() en el selftest viejo). Desde el 25 nov
    # 2026 una tienda sin regla de vendedor no es oficial (revisor R1-F3): para ellos no se exige vendedor oficial.
    solo_titulo = {}
    for pid, p in prods.items():
        q = json.loads(json.dumps(p))
        q["requisitos"]["solo_vendedor_oficial"] = False
        solo_titulo[pid] = q
    # Casos de una tienda sin lector cargado o sin regla de vendedor (lectores nuevos apagados: archivo ausente,
    # registrar roto o su prueba de arranque fallida) se saltan y se dice: el nucleo no depende de lectores_nuevos.py
    # (revisor R5-H1: si este paso falla en la nube, se apagan todos los avisos de precio).
    sin_lector = sorted({c["tienda"] for c in casos if c["tienda"] != "sin dato"
                         and (c["tienda"] not in v.LECTORES or c["tienda"] not in v.VENDEDOR_OFICIAL)})
    if sin_lector:
        n = sum(1 for c in casos if c["tienda"] in sin_lector)
        print("casos_titulos: %d casos saltados: tiendas sin lector cargado o sin regla de vendedor: %s"
              % (n, ", ".join(sin_lector)))
        casos = [c for c in casos if c["tienda"] not in sin_lector]
    for c in casos:
        prod = solo_titulo[c["producto"]] if c["tienda"] == "sin dato" else prods[c["producto"]]
        o = v.oferta(prod, c["tienda"], c["titulo"], c["precio"], "u", c["vendedor"],
                     importado=c.get("importado", False))
        filas.append((c, o["var"] if o else None, o))
    return prods, filas


def correr(v):
    _, filas = resolver(v)
    mal = []
    for c, sale, _ in filas:
        espera = c["espera_hoy"] if "espera_hoy" in c else c["espera"]
        if sale != espera:
            mal.append("%s | %s | $%s | vendedor %r | espera %s, sale %s" % (
                c["tienda"], c["titulo"][:80], c["precio"], c["vendedor"], espera, sale))
    assert not mal, "titulos reales que no dan lo esperado:\n  " + "\n  ".join(mal)
    return len(filas)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, RAIZ)
    import vigia
    prods, filas = resolver(vigia)
    print("QUE CAE EN CADA VARIANTE (%d casos de %s)" % (len(filas), ", ".join(sorted({c["tienda"] for c, _, _ in filas}))))
    for p in prods.values():
        for var in p["variantes"]:
            dentro = sorted((o["precio"], c["tienda"], c["titulo"]) for c, sale, o in filas
                            if c["producto"] == p["id"] and sale == var["id"])
            print("\n%s  (%d)" % (var["nombre"], len(dentro)))
            for precio, tienda, titulo in dentro:
                print("  $%10s  %-10s %s" % ("{:,.2f}".format(precio), tienda, titulo[:100]))
    fuera = [(c["producto"], c["tienda"], c["precio"], c["titulo"]) for c, sale, _ in filas if sale is None]
    print("\nDESCARTADO (%d)" % len(fuera))
    for prod, tienda, precio, titulo in sorted(fuera, key=lambda x: (x[0], x[1], x[2])):
        print("  %-5s %-10s $%10s  %s" % (prod, tienda, "{:,.2f}".format(precio), titulo[:90]))
    hallazgos = [c for c, _, _ in filas if "espera_hoy" in c]
    print("\nHallazgos (el programa da otra cosa que lo esperado): %d" % len(hallazgos))
    for c in hallazgos:
        print("  %s | %s | espera %s, hoy %s" % (c["tienda"], c["titulo"][:90], c["espera"], c["espera_hoy"]))
    try:
        n = correr(vigia)
        print("\ncasos_titulos OK: %d casos" % n)
    except AssertionError as ex:
        print("\nFALLA:", ex)
        sys.exit(1)
