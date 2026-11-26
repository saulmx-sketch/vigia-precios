# Recoge los titulos reales que las tiendas dan hoy y lo que el vigia resuelve con cada uno.
# Uso (lee la red una vez; no toca visto.json ni precios.json):  py -X utf8 pruebas\recoger_titulos.py
# Salida: pruebas\titulos_vistos_<fecha>.json. Insumo para etiquetar a mano pruebas\casos_titulos.json.
import datetime as dt
import html
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import vigia  # noqa: E402

prods = vigia.cargar_config(os.path.join(RAIZ, "productos.json"))
orig_oferta, orig_variante, orig_marca, orig_pedir = vigia.oferta, vigia.variante, vigia.marca_ok, vigia.pedir
actual = {"tienda": None, "dentro": False}
por_oferta, previos, paginas = [], [], []


def razon(prod, tienda, titulo, precio, vendedor, importado):
    if not orig_variante(prod, titulo):
        return "descartado: titulo"
    if precio is None or precio < prod["piso"]:
        return "descartado: precio bajo el piso"
    if prod["requisitos"].get("solo_vendedor_oficial") and not vigia.vendedor_oficial(prod, tienda, vendedor):
        return "descartado: vendedor"
    if importado and not prod["requisitos"].get("permite_importados"):
        return "descartado: importado"
    return "descartado: otro"


def oferta(prod, tienda, titulo, precio, url, vendedor="", nota="", importado=False, var=None):
    actual["dentro"] = True
    try:
        o = orig_oferta(prod, tienda, titulo, precio, url, vendedor, nota, importado, var)
    finally:
        actual["dentro"] = False
    por_oferta.append({"tienda": tienda, "producto": prod["id"], "titulo": titulo, "precio": precio,
                       "vendedor": vendedor, "importado": bool(importado), "var_dada": var["id"] if var else None,
                       "resolvio": ("variante: " + o["var"]) if o else razon(prod, tienda, titulo, precio, vendedor, importado)})
    return o


def variante(prod, titulo):
    v = orig_variante(prod, titulo)
    if not actual["dentro"]:
        previos.append({"tienda": actual["tienda"], "producto": prod["id"], "titulo": titulo,
                        "resolvio_variante": v["id"] if v else None})
    return v


def marca_ok(prod, titulo):
    r = orig_marca(prod, titulo)
    if not r:
        previos.append({"tienda": actual["tienda"], "producto": prod["id"], "titulo": titulo, "sin_marca": True,
                        "resolvio_variante": None})
    return r


def pedir(url, *a, **k):
    e, t = orig_pedir(url, *a, **k)
    if "amazon.com.mx/s?" in url or "gameplanet.com/catalogo" in url:
        paginas.append((url, t))
    return e, t


def carrito(*a, **k):
    raise RuntimeError("carrito no probado en la recogida de titulos")


vigia.oferta, vigia.variante, vigia.marca_ok, vigia.pedir, vigia.samsung_carrito = oferta, variante, marca_ok, pedir, carrito

lecturas, estado = {}, {}
for tienda, lector in vigia.LECTORES.items():
    for p in prods:
        if tienda not in p["tiendas"]:
            continue
        actual["tienda"] = tienda
        try:
            r = lector(p, p["tiendas"][tienda], estado)
            lecturas["%s|%s" % (tienda, p["id"])] = "ok, %d ofertas" % len(r)
        except Exception as ex:
            lecturas["%s|%s" % (tienda, p["id"])] = "FALLA: %s" % str(ex)[:200]
        print(tienda, p["id"], lecturas["%s|%s" % (tienda, p["id"])])

# precio de los titulos que se descartaron antes de llegar a oferta() (Amazon y Gameplanet filtran primero)
precio_de = {}
for url, t in paginas:
    if "amazon" in url:
        marcas = list(re.finditer(r'data-asin="([A-Z0-9]{10})"[^>]{0,400}data-component-type="s-search-result"', t))
        for i, m in enumerate(marcas):
            bloque = t[m.end():marcas[i + 1].start() if i + 1 < len(marcas) else m.end() + 20000]
            tit = re.search(r"<h2[^>]*>(.*?)</h2>", bloque, re.S)
            pr = re.search(r'class="a-offscreen">\$([\d,]+\.\d\d)<', bloque)
            if tit and pr:
                titulo = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tit.group(1)))).strip()
                precio_de.setdefault(("Amazon", titulo), float(pr.group(1).replace(",", "")))
    else:
        lista = t[t.find('data-testid="products-list"'):] if 'data-testid="products-list"' in t else ""
        for href, cuerpo in re.findall(r'data-testid="product-card" href="(/products/[^"]+)">(.*?)</a></li>', lista, re.S):
            tit = re.search(r"<h3[^>]*>(.*?)</h3>", cuerpo, re.S)
            pr = re.search(r'data-testid="product-price" data-value="([\d.]+)"', cuerpo)
            if tit and pr:
                titulo = html.unescape(re.sub(r"<[^>]+>", " ", tit.group(1))).strip()
                precio_de.setdefault(("Gameplanet", titulo), float(pr.group(1)))

hechos = {(x["tienda"], x["producto"], x["titulo"]) for x in por_oferta}
titulos, ya = list(por_oferta), set()
for x in previos:
    k = (x["tienda"], x["producto"], x["titulo"])
    if k in hechos or k in ya:
        continue
    ya.add(k)
    pr = precio_de.get((x["tienda"], x["titulo"]))
    piso = next(p["piso"] for p in prods if p["id"] == x["producto"])
    titulos.append({"tienda": x["tienda"], "producto": x["producto"], "titulo": x["titulo"], "precio": pr,
                    "vendedor": None, "importado": False, "var_dada": None,
                    "resolvio": "descartado: no nombra la marca" if x.get("sin_marca") else
                    "descartado: titulo" if not x["resolvio_variante"] else
                    "descartado antes de la ficha: precio bajo el piso o sin precio" if pr is None or pr < piso else
                    "variante %s, pero la ficha no trae oferta nueva a la venta (agotado)" % x["resolvio_variante"]})

hoy = dt.date.today().isoformat()
salida = os.path.join(RAIZ, "pruebas", "titulos_vistos_%s.json" % hoy)
with open(salida, "w", encoding="utf-8") as f:
    json.dump({"fecha": hoy, "como": "pruebas/recoger_titulos.py: cada lector de LECTORES una vez con productos.json; "
               "carrito de Samsung sin probar (precio de pagina)", "lecturas": lecturas, "titulos": titulos},
              f, ensure_ascii=False, indent=1)
print("titulos:", len(titulos), "->", salida)
