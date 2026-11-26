"""Un producto que no es Fold ni PS5 se vigila sin tocar vigia.py (Saul, 25 nov 2026: "El proyecto debe quedar
suficientemente flexible como una skill para aplicarse a cualquier producto en cualquier momento").

Producto de muestra: pruebas\\producto_muestra.json (iPad Air 11, 128 GB, Wi-Fi). Respuestas grabadas reales del
25 nov 2026 en pruebas\\respuestas\\ipad_* (LEEME.md dice cual se altero). Ademas, una guarda: el codigo de vigia.py,
fuera de las zonas de las lineas A y B, del selftest y de comentarios y cadenas de documentacion, no nombra productos.
Uso:  py -X utf8 pruebas\\casos_producto_muestra.py      (o vigia.py --selftest, via correr(v))
"""
import ast
import contextlib
import io
import json
import os
import re
import sys
import tokenize

AQUI = os.path.dirname(os.path.abspath(__file__))
RESP = os.path.join(AQUI, "respuestas")
MUESTRA = os.path.join(AQUI, "producto_muestra.json")

# Palabras de producto que no deben aparecer en el codigo de vigia.py
PROHIBIDAS = ("ps5", "playstation", "fold8", "galaxy", "ipad")
# Donde si pueden aparecer, y por que (lo que quede atado hoy; un nombre nuevo fuera de aqui hace fallar la prueba)
PERMITIDAS = {
    "selftest": "pruebas del nucleo con los titulos reales de los productos vigilados",
    "NO_ES_CUPON": "linea B: palabras que no son codigo de cupon en los banners de Samsung",
    "PAGINAS_CUPON_SAMSUNG": "linea B: paginas de cupones de Samsung",
    "BUSQUEDAS_CUPON_FORO": "linea B: busquedas de cupones en el foro",
    "topes": "linea A", "nivel": "linea A", "bancos": "linea B", "samsung_carrito": "linea B",
    "codigos_de": "linea B", "cupones_en_texto": "linea B", "cosechar_cupones": "linea B", "cupones_candidatos": "linea B",
}


def lee(n):
    with open(os.path.join(RESP, n), encoding="utf-8") as f:
        return f.read()


class Red:
    v = None

    def __init__(self, mapa):
        self.mapa, self.pedidas = mapa, []
        if Red.v is not None:
            Red.v.LIVERPOOL_LEIDAS.clear()

    def __call__(self, url, datos=None, metodo=None, abridor=None, json_api=False, pausa=0.0, reintento=2.0, **_otros):
        self.pedidas.append(url)
        for patron, r in self.mapa:
            if re.search(patron, url):
                return r if isinstance(r, tuple) else (200, lee(r))
        return 404, ""


def nombres_de_producto(ruta):
    """[(renglon, zona, texto)] de cada mencion prohibida en el codigo (sin comentarios ni docstrings)."""
    with open(ruta, encoding="utf-8") as f:
        fuente = f.read()
    arbol = ast.parse(fuente)
    zonas, docs = [], set()
    for nodo in arbol.body:
        nombre = getattr(nodo, "name", None)
        if nombre is None and isinstance(nodo, ast.Assign) and isinstance(nodo.targets[0], ast.Name):
            nombre = nodo.targets[0].id
        zonas.append((nodo.lineno, nodo.end_lineno, nombre))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and nodo.body:
            d = nodo.body[0]
            if isinstance(d, ast.Expr) and isinstance(getattr(d, "value", None), ast.Constant) and isinstance(d.value.value, str):
                docs.update(range(d.lineno, d.end_lineno + 1))
    zona_de = lambda n: next((z for a, b, z in zonas if a <= n <= b), None)
    hallados = []
    for tok in tokenize.generate_tokens(io.StringIO(fuente).readline):
        if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            continue
        if tok.type == tokenize.STRING and tok.start[0] in docs:
            continue
        txt = tok.string.lower()
        for p in PROHIBIDAS:
            if p in txt:
                hallados.append((tok.start[0], zona_de(tok.start[0]), tok.string[:60]))
    return hallados


def correr(v):
    guardado = (v.pedir, v.LIVERPOOL_SEGUNDOS)
    Red.v = v
    try:
        _correr(v)
    finally:
        v.pedir, v.LIVERPOOL_SEGUNDOS = guardado
        v.LIVERPOOL_LEIDAS.clear()
    print("casos_producto_muestra OK")


def _correr(v):
    prods = v.cargar_config(MUESTRA)
    assert [p["id"] for p in prods] == ["ipad-air"], prods
    ipad = prods[0]
    m4 = v.var_por_id(ipad, "m4-128-wifi")
    # para confirmar hace falta un precio con nivel de aviso: topes de demostracion (los provisionales no alcanzan)
    demo = json.loads(json.dumps(ipad))
    demo["topes_fijos"] = {"m4-128-wifi": [19000, 19500, 20000]}

    # Amazon: entra el iPad Air 11 M4 128 GB Wi-Fi que vende Amazon Mexico, a precio de ficha; fuera la de 13 pulgadas,
    # la M3 Wi-Fi + Cellular (dice "5G"), funda, teclado y renovado
    v.pedir = Red([(r"amazon\.com\.mx/s\?k=", "ipad_amz_busca.html"), (r"/dp/B0GQVP7LKW", "ipad_amz_ficha_B0GQVP7LKW.html")])
    out = v._leer_amazon(ipad, {"consultas": ["ipad+air+m3+128gb"]}, {}, [])
    assert [(o["var"], o["precio"], o["vendedor"]) for o in out] == [("m4-128-wifi", 16793.61, "Amazon México")], out
    assert sum("/dp/" in u for u in v.pedir.pedidas) == 1, v.pedir.pedidas      # solo se abrio la ficha del que casa
    for titulo in ("Apple 2026 iPad Air de 13 Pulgadas (Wi-Fi, 128 GB,) - Gris Espacial (M4)",
                   "Apple iPad Air de 11 Pulgadas con Chip M3: Apple Intelligence, 128 GB, Wi-Fi 6E y Red 5G",
                   "JETech Funda para iPad Air 11\" M4/M3/M2 & Air 10.9\" 5/4, Negro",
                   "Bettdow Ligero Aleación de Aluminio Español (Incluye Ñ) Magic Keyboard para iPad Air 11“ M3",
                   "Amazon Renewed Apple Ipad Air, 2025 con Chip M3 (13-pulgadas, Wi-fi, 128GB)",
                   "iPad Air M4 11 pulgadas 256 GB 12 GB RAM Gris Espacial"):
        assert v.oferta(ipad, "Amazon", titulo, 18000, "u", "Amazon México") is None, titulo
    # vendedor tercero: la misma ficha con otro vendedor no entra (ni por la busqueda ni al confirmar)
    v.pedir = Red([(r"amazon\.com\.mx/s\?k=", "ipad_amz_busca.html"), (r"/dp/", "ipad_amz_ficha_B0GQVP7LKW_tercero.html")])
    assert v._leer_amazon(ipad, {"consultas": ["ipad+air+m3+128gb"]}, {}, []) == []
    assert v.oferta(ipad, "Amazon", out[0]["titulo"], 16793.61, "u", "Apple") is not None    # Apple si cuenta
    r, _, razon = v.confirmar(dict(out[0]), ipad, {})
    assert r == "no_coincide" and "iShop Revendedor MX" in razon, (r, razon)
    v.pedir = Red([(r"/dp/B0GQVP7LKW", "ipad_amz_ficha_B0GQVP7LKW.html")])
    r, final, _ = v.confirmar(dict(out[0]), demo, {})
    assert r == "confirmada" and final["precio"] == 16793.61 and final["vendedor"] == "Amazon México", (r, final)

    # Liverpool: entra el M4 de 128 GB que vende Liverpool (titulo de la ficha tal cual: el producto no tiene reglas de
    # atributos); fuera 256 GB, 13 pulgadas, funda y teclado; confirmar con la ficha grabada da "confirmada"
    v.pedir = Red([(r"tienda\?s=", "ipad_liv_busca.html"), (r"pdp/p/1195646976", "ipad_liv_pdp_1195646976.html")])
    with contextlib.redirect_stdout(io.StringIO()):
        out = v.leer_liverpool(ipad, {"consulta": "ipad+air+11"}, {})
    assert [(o["var"], o["precio"], o["vendedor"]) for o in out] == [("m4-128-wifi", 18799.0, "Liverpool")], out
    assert out[0]["titulo"] == "iPad Air M4 11 pulgadas 128 GB 12 GB RAM Gris Espacial", out[0]["titulo"]
    pedidas = [u for u in v.pedir.pedidas if "pdp/p/" in u]
    # candidatos a ficha: el de 128 GB y el de 256 GB (dicen "ipad air" y pasan el piso); 13 pulgadas, funda y teclado no
    assert pedidas == ["https://www.liverpool.com.mx/tienda/pdp/p/1195646976",
                       "https://www.liverpool.com.mx/tienda/pdp/p/1195644558"], pedidas
    v.pedir = Red([(r"pdp/p/1195646976", "ipad_liv_pdp_1195646976.html")])
    r, final, _ = v.confirmar(dict(out[0], precio=18500.0), demo, {})
    assert r == "confirmada" and final["precio"] == 18799.0 and final["vendedor"] == "Liverpool", (r, final)
    assert v.titulo_con_atributos(ipad, "Liverpool", "iPad Air M4", {"Procesador": "Apple M4"}) == "iPad Air M4"

    # Costco: entra el de 128 GB (vende Costco); fuera 13 pulgadas y 256 GB
    v.pedir = Red([(r"costco", "ipad_cos_busca.json")])
    out = v.leer_costco(ipad, {"consulta": "ipad air"}, {})
    assert [(o["var"], o["precio"], o["vendedor"]) for o in out] == [("m4-128-wifi", 14799.0, "Costco (pide membresia)")], out

    # nivel con fracciones provisionales: el programa calcula topes para cualquier lista
    assert v.topes(ipad, m4) == (13200, 14700, 16000), v.topes(ipad, m4)

    # --cobertura del archivo de muestra: nombra el producto, sus variantes y sus tiendas
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        assert v.cobertura(prods) == 0
    texto = salida.getvalue()
    for x in ("iPad Air 11", "iPad Air 11 M4 128 GB Wi-Fi", "iPad Air 11 M3 128 GB Wi-Fi"):
        assert x in texto, (x, texto[:600])
    # cada tienda configurada sale, con lector (directo) o como "aun no tienen lector" (segun este lectores_nuevos.py)
    renglones = [r for r in texto.splitlines() if r.startswith(("Tiendas que se leen directo:", "Tiendas configuradas que aun"))]
    for t in ipad["tiendas"]:
        assert any(t in r for r in renglones), (t, renglones)

    # con otro archivo de productos (VIGIA_CONFIG) la corrida normal se niega: codigo 3, sin leer ni escribir nada
    guardado = (v.CONFIG, list(sys.argv), v.pedir)
    try:
        v.CONFIG, sys.argv = MUESTRA, ["vigia.py"]
        v.pedir = lambda *a, **k: (_ for _ in ()).throw(AssertionError("la corrida negada pidio red"))
        with contextlib.redirect_stdout(io.StringIO()) as s:
            assert v.main() == 3, "con VIGIA_CONFIG y sin --seco main debio negarse"
        assert "solo se corre --seco o --cobertura" in s.getvalue(), s.getvalue()
        assert [p["id"] for p in v.cargar_config()] != ["ipad-air"]   # sin ruta, siempre productos.json
    finally:
        v.CONFIG, sys.argv, v.pedir = guardado[0], guardado[1], guardado[2]

    # la guarda: ningun nombre de producto en el codigo fuera de las zonas permitidas
    hallados = [h for h in nombres_de_producto(v.__file__) if h[1] not in PERMITIDAS]
    assert not hallados, "vigia.py nombra productos fuera de las zonas permitidas: %s" % hallados


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(AQUI))
    import vigia
    correr(vigia)
