# Lectores de lectores_nuevos.py contra respuestas reales grabadas (pruebas\respuestas\soriana_*.html, 25 nov 2026).
# Lo corre `vigia.py --selftest` (funcion correr). Sin red: v.pedir se sustituye por un servidor de respuestas.
# Si lectores_nuevos.py no esta junto a vigia.py, solo se prueba el enganche (que el vigia siga igual sin el).
import io
import os
import re
import sys
import types
import contextlib
import urllib.parse

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
RESP = os.path.join(AQUI, "respuestas")
S = "https://www.soriana.com"
FICHA_FOLD8 = S + "/samsung-galaxy-z-fold8-7.6-pulgadas-512gb-lavanda-desbloqueado/11944979.html"
FICHA_PS5 = S + "/consola-playstation-5-digital-slim-astro-bot-y-gran-turismo-7-825gb/11912222.html"
FICHA_UNIDAD = S + "/unidad-de-disco-para-consolas-ps5-digital/11864204.html"


def _leer(nombre):
    with open(os.path.join(RESP, nombre), encoding="utf-8") as f:
        return f.read()


def _busca(q):
    return S + "/buscar?q=" + urllib.parse.quote_plus(q)


class Servidor:
    """Sustituto de v.pedir: sirve respuestas grabadas; una URL no grabada es falla de la prueba."""

    def __init__(self, tabla):
        self.tabla, self.pedidas = tabla, []

    def __call__(self, url, *a, **k):
        self.pedidas.append(url)
        if url not in self.tabla:
            raise AssertionError("la prueba no tiene grabada %s" % url)
        return self.tabla[url]


NUEVAS = ("Soriana", "Cyberpuerta", "Chedraui")


def _probar_enganche_con_pruebas(v):
    """(f, H9) con el lectores_nuevos.py real: si sus pruebas fallan, las tres tiendas quedan dadas de baja en LECTORES,
    VENDEDOR_OFICIAL y FICHAS, los diccionarios como antes y el renglon dice por que; si pasan, quedan dadas de alta.
    (H6) sin lector cargado, tiendas_sin_lector() las nombra para que main() las pase por fuente() como falla."""
    dicts = [d for d in (v.LECTORES, v.VENDEDOR_OFICIAL, getattr(v, "FICHAS", None)) if isinstance(d, dict)]
    copias = [dict(d) for d in dicts]
    error = v.LECTORES_NUEVOS_ERROR
    prods = v.cargar_config(os.path.join(RAIZ, "productos.json"))
    try:
        for d in dicts:                          # el estado de antes del alta
            for t in NUEVAS:
                d.pop(t, None)
        sin_ellas = [dict(d) for d in dicts]
        assert v.tiendas_sin_lector(prods) == sorted(NUEVAS), v.tiendas_sin_lector(prods)

        def falla(m):
            raise AssertionError("prueba de lectores nuevos que falla a proposito")
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            v.enganchar_lectores_nuevos(prueba=falla)
        assert "lectores nuevos apagados: sus pruebas fallan: AssertionError" in salida.getvalue(), salida.getvalue()
        assert [dict(d) for d in dicts] == sin_ellas, "una prueba fallida dejo tiendas nuevas dadas de alta"
        assert v.tiendas_sin_lector(prods) == sorted(NUEVAS)
        assert "sus pruebas fallan" in v.LECTORES_NUEVOS_ERROR
        with contextlib.redirect_stdout(io.StringIO()):
            v.enganchar_lectores_nuevos(prueba=lambda m: None)
        assert all(t in v.LECTORES and t in v.VENDEDOR_OFICIAL for t in NUEVAS), "pruebas que pasan no dieron de alta"
        assert v.tiendas_sin_lector(prods) == [] and v.LECTORES_NUEVOS_ERROR == ""
    finally:
        for d, c in zip(dicts, copias):
            d.clear()
            d.update(c)
        v.LECTORES_NUEVOS_ERROR = error


def _probar_enganche(v):
    """(f) registrar() que truena deja LECTORES y VENDEDOR_OFICIAL como estaban y lo dice; archivo ausente = nada."""
    antes_l, antes_v = dict(v.LECTORES), dict(v.VENDEDOR_OFICIAL)
    guardado = sys.modules.get("lectores_nuevos")
    roto = types.ModuleType("lectores_nuevos")

    def registrar(m):
        m.LECTORES["Rota"] = lambda *a: []
        m.VENDEDOR_OFICIAL["Rota"] = "x"
        raise ValueError("falla de prueba")
    roto.registrar = registrar
    try:
        sys.modules["lectores_nuevos"] = roto
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            v.enganchar_lectores_nuevos()
        assert "lectores nuevos apagados: ValueError: falla de prueba" in salida.getvalue(), salida.getvalue()
        assert v.LECTORES == antes_l and v.VENDEDOR_OFICIAL == antes_v, "un registrar roto dejo cambios a medias"
        sys.modules["lectores_nuevos"] = None        # como si el archivo no existiera
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            v.enganchar_lectores_nuevos()
        assert salida.getvalue() == "" and v.LECTORES == antes_l, "sin lectores_nuevos el vigia cambio: %r" % salida.getvalue()
    finally:
        if guardado is None:
            sys.modules.pop("lectores_nuevos", None)
        else:
            sys.modules["lectores_nuevos"] = guardado
    for t in ("Samsung", "Amazon", "Costco", "Elektra", "Liverpool", "Gameplanet"):
        assert t in v.LECTORES, "falta el lector de siempre " + t


def correr(v):
    # al arrancar, vigia.py corre estas pruebas desde su propio enganche: el candado contra la recursion se suelta
    # aqui (estas pruebas nunca llaman al enganche sin `prueba` y con el modulo real) y se repone al final
    candado = list(v._ENGANCHANDO)
    del v._ENGANCHANDO[:]
    try:
        _probar_enganche(v)
        if not os.path.exists(os.path.join(RAIZ, "lectores_nuevos.py")):
            return 1
        _probar_enganche_con_pruebas(v)
    finally:
        v._ENGANCHANDO[:] = candado
    return _correr_tiendas(v)


def _correr_tiendas(v):
    assert "Soriana" in v.LECTORES, "lectores_nuevos.py existe pero Soriana no quedo registrada"
    prods = {p["id"]: p for p in v.cargar_config(os.path.join(RAIZ, "productos.json"))}
    fold, ps5 = prods["fold8"], prods["ps5"]
    leer = v.LECTORES["Soriana"]
    f_fold, f_ps5 = _leer("soriana_ficha_fold8.html"), _leer("soriana_ficha_ps5.html")
    tabla = {
        _busca("galaxy z fold8"): (200, _leer("soriana_busca_galaxy_z_fold8.html")),
        _busca("playstation 5 digital"): (200, _leer("soriana_busca_playstation_5_digital.html")),
        _busca("consola playstation 5"): (200, _leer("soriana_busca_consola_playstation_5.html")),
        _busca("samsung galaxy z fold"): (200, _leer("soriana_busca_vacia.html")),
        FICHA_FOLD8: (200, f_fold),
        FICHA_PS5: (200, f_ps5),
        FICHA_UNIDAD: (200, _leer("soriana_ficha_unidad_disco.html")),
    }
    # "Unidad de Disco para Consolas PS5 Digital": con la regla de excluir de linea-C ya no pasa el titulo, asi que su
    # ficha no se abre (antes la dejaba fuera solo el piso; hallazgo del 25 nov en H7_lectores.md).
    abre = {"playstation 5 digital": [FICHA_PS5], "consola playstation 5": [FICHA_PS5]}
    guardado = v.pedir
    try:
        v.pedir = srv = Servidor(tabla)
        # (a) Fold8 512 y PS5 Digital, con precio, vendedor, liga y variante
        r = leer(fold, {"consulta": "galaxy z fold8"}, {})
        assert [(o["var"], o["precio"], o["vendedor"], o["url"], o["tienda"]) for o in r] == \
            [("normal-512", 36990.0, "Soriana", FICHA_FOLD8, "Soriana")], r
        assert "Fold8 7.6 Pulgadas 512GB Lavanda" in r[0]["titulo"], r
        for q in ("playstation 5 digital", "consola playstation 5"):
            srv.pedidas = []
            r = leer(ps5, {"consulta": q}, {})
            assert [(o["var"], o["precio"], o["vendedor"], o["url"]) for o in r] == \
                [("digital", 11990.0, "Soriana", FICHA_PS5)], (q, r)
            # (e) el listado ya deja fuera juegos, audifonos y control: no se abren sus fichas
            assert srv.pedidas == [_busca(q)] + abre[q], (q, srv.pedidas)
        # busqueda real sin resultados = lista vacia (no es falla)
        assert leer(fold, {"consulta": "samsung galaxy z fold"}, {}) == []
        # (b) vendedor distinto de Soriana, o sin vendedor: no hay oferta
        assert f_fold.count('"name":"Soriana"') == 1 and re.search(r'"seller":\{[^}]*\}', f_fold), "recorte cambiado"
        tabla[FICHA_FOLD8] = (200, f_fold.replace('"name":"Soriana"', '"name":"Electronica Tercera SA"'))
        assert leer(fold, {"consulta": "galaxy z fold8"}, {}) == [], "un tercero en Soriana paso"
        # (H1) sin vendedor o sin existencia legible y sin ningun agotado: "formato cambiado", nunca lista vacia
        for alterada in (re.sub(r'"seller":\{[^}]*\}', '"seller":{}', f_fold),
                         f_fold.replace('"availability":"http://schema.org/InStock",', ""),
                         f_fold.replace("schema.org/InStock", "schema.org/EnCamino")):
            assert alterada != f_fold, "recorte cambiado"
            tabla[FICHA_FOLD8] = (200, alterada)
            _espera_error(lambda: leer(fold, {"consulta": "galaxy z fold8"}, {}), "ficha Soriana sin dato")
        # (c) agotada: no hay oferta, y es lista vacia legitima (agotado explicito)
        tabla[FICHA_FOLD8] = (200, f_fold.replace("schema.org/InStock", "schema.org/OutOfStock"))
        assert leer(fold, {"consulta": "galaxy z fold8"}, {}) == [], "una ficha agotada produjo oferta"
        tabla[FICHA_FOLD8] = (200, f_fold)
        # (H2) una ficha que falla se salta si otra se leyo; si fallan todas las abiertas, RuntimeError
        otra_ps5 = S + "/consola-playstation-5-digital-otra/11999999.html"
        dos = ('<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"url":"%s"},{"url":"%s"}]}'
               '</script><a class="link plp-link x" href="/consola-playstation-5-digital-otra/11999999.html">Consola '
               'PlayStation 5 Digital Slim</a><a class="link plp-link x" href="%s">Consola PlayStation 5 Digital Slim + '
               'Astro Bot y Gran Turismo 7 825GB</a>' % (otra_ps5, FICHA_PS5, urllib.parse.urlsplit(FICHA_PS5).path))
        tabla[_busca("dos fichas")] = (200, dos)
        tabla[otra_ps5] = (404, "<html>no encontrada</html>")
        with contextlib.redirect_stdout(io.StringIO()):
            r = leer(ps5, {"consulta": "dos fichas"}, {})
        assert [(o["var"], o["precio"]) for o in r] == [("digital", 11990.0)], r
        tabla[FICHA_PS5] = (404, "<html>no encontrada</html>")
        with contextlib.redirect_stdout(io.StringIO()):
            _espera_error(lambda: leer(ps5, {"consulta": "dos fichas"}, {}), "Soriana con todas las fichas en 404")
        tabla[FICHA_PS5] = (200, f_ps5)
        # (d) 403 o pagina sin ld+json: RuntimeError, nunca "no hay ofertas"
        for resp in ((403, "<html>Access Denied</html>"), (200, "<html><body>sin datos</body></html>"),
                     (503, "")):
            tabla[_busca("galaxy z fold8")] = resp
            try:
                leer(fold, {"consulta": "galaxy z fold8"}, {})
            except RuntimeError:
                pass
            else:
                raise AssertionError("busqueda %s no lanzo RuntimeError" % (resp,))
        tabla[_busca("galaxy z fold8")] = (200, _leer("soriana_busca_galaxy_z_fold8.html"))
        for resp in ((403, "<html>Access Denied</html>"), (200, "<html>ficha sin datos</html>")):
            tabla[FICHA_FOLD8] = resp
            with contextlib.redirect_stdout(io.StringIO()):
                _espera_error(lambda: leer(fold, {"consulta": "galaxy z fold8"}, {}), "ficha Soriana %s" % (resp,))
        tabla[FICHA_FOLD8] = (200, f_fold)
        # (H7) listado con ligas y ningun nombre: RuntimeError, no se abren 6 fichas a ciegas
        lista_sin_nombre = ('<script type="application/ld+json">{"@type":"ItemList","itemListElement":'
                            '[{"@type":"ListItem","position":1,"url":"%s"}]}</script>' % FICHA_UNIDAD)
        tabla[_busca("unidad ps5")] = (200, lista_sin_nombre)
        srv.pedidas = []
        _espera_error(lambda: leer(ps5, {"consulta": "unidad ps5"}, {}), "listado de Soriana sin nombres")
        assert srv.pedidas == [_busca("unidad ps5")], srv.pedidas
        # (e) una liga sin nombre en un listado que si trae nombres: se abre la ficha y oferta() deja fuera la unidad
        # de disco; con la PS5 con lector (titulo supuesto: Soriana no la lista hoy) tampoco hay oferta
        lista_mixta = ('<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"url":"%s"}]}'
                       '</script><a class="link plp-link x" href="/otra/1.html">Spider Man 2 para PS5</a>' % FICHA_UNIDAD)
        tabla[_busca("unidad ps5")] = (200, lista_mixta)
        assert leer(ps5, {"consulta": "unidad ps5"}, {}) == [], "la unidad de disco paso como consola"
        tabla[FICHA_UNIDAD] = (200, f_ps5.replace("Consola PlayStation 5 Digital Slim + Astro Bot y Gran Turismo 7 825GB",
                                                  "Consola PlayStation 5 Slim Standard con Lector de Discos 1TB"))
        assert leer(ps5, {"consulta": "unidad ps5"}, {}) == [], "la PS5 con lector paso como digital"
    finally:
        v.pedir = guardado
    # (e) titulos de Soriana fuera: accesorios y la PS5 con lector
    # (a precio de consola, para que el titulo sea lo que decide)
    for titulo, precio in (("Unidad de Disco para Consolas PS5 Digital", 11990),
                           ("Auriculares Inalámbricos Pulse Explore para PS5", 11990),
                           ("Control DualSense PlayStation 5 Gray Camo", 11990),
                           ("Spider Man 2 Standard Edition para PS5", 11990),
                           ("Consola PlayStation 5 Slim Standard con Lector de Discos 1TB", 11990)):
        assert v.oferta(ps5, "Soriana", titulo, precio, "u", "Soriana") is None, titulo
    # la regla de vendedor de Soriana: solo Soriana, sin importar mayusculas ni espacios
    assert v.vendedor_oficial(ps5, "Soriana", "  SORIANA ") and not v.vendedor_oficial(ps5, "Soriana", "Soriana Marketplace")
    assert not v.vendedor_oficial(fold, "Soriana", ""), "sin vendedor no debe contar como Soriana"
    if isinstance(getattr(v, "FICHAS", None), dict):
        assert "Soriana" in v.FICHAS, "FICHAS existe pero Soriana no quedo registrada"
        _probar_fichas_soriana(v, f_fold)
    _probar_cyberpuerta(v, fold, ps5)
    _probar_chedraui(v, ps5)
    return 1


CH_API = "https://www.chedraui.com.mx/api/catalog_system/pub/products/search"
CH_BUSCA = CH_API + "?ft=consola%20playstation%205%20digital&_from=0&_to=49"
CH_FICHA = ("https://www.chedraui.com.mx/bundle-consola-playstation-5-edicion-digital-de-825-gb-astro-bot-y-gran-turismo-7"
            "-3879487/p")


def _ch(nombre):
    """La respuesta grabada de Chedraui (lista de productos), como la da la API."""
    import json
    with open(os.path.join(RESP, nombre), encoding="utf-8") as f:
        return json.load(f)["respuesta"]


def _probar_chedraui(v, ps5):
    import copy
    import json
    assert "Chedraui" in v.LECTORES, "Chedraui no quedo registrada"
    leer = v.LECTORES["Chedraui"]
    busca, ficha = _ch("chedraui_busca_consola_playstation_5_digital.json"), _ch("chedraui_ficha_3879487.json")
    otra = _ch("chedraui_busca_playstation_5_digital.json")

    def con_existencia(lista, vendedor=None, ids=None):
        """Copia con AvailableQuantity 10 (y, si se pide, otro vendedor) en los productos indicados (todos si None)."""
        lista = copy.deepcopy(lista)
        for p in lista:
            if ids is None or p["productId"] in ids:
                for it in p["items"]:
                    for s in it["sellers"]:
                        s["commertialOffer"]["AvailableQuantity"] = 10
                        if vendedor is not None:
                            s["sellerName"] = vendedor
        return json.dumps(lista)
    tabla = {CH_BUSCA: (200, json.dumps(busca)), CH_API + "?fq=productId:3879487": (200, json.dumps(ficha))}
    guardado = v.pedir
    try:
        v.pedir = Servidor(tabla)
        cfg = {"consulta": "consola playstation 5 digital"}
        # hoy todo agotado (AvailableQuantity 0): lista vacia, sin error
        assert leer(ps5, cfg, {}) == []
        # (a) la misma respuesta alterada a "en existencia" (LEEME): entra solo la Edicion Digital, $12,499, Chedraui;
        # (e) "Bundle ... Astro Bot y Gran Turismo 7" ($11,490, no dice digital) y el "Pack ... Slim 1TB Standard"
        # ($8,990, de disco) no entran aunque pasan el piso
        tabla[CH_BUSCA] = (200, con_existencia(busca))
        r = leer(ps5, cfg, {})
        assert [(o["var"], o["precio"], o["vendedor"], o["url"], o["tienda"]) for o in r] == \
            [("digital", 12499.0, "Chedraui", CH_FICHA, "Chedraui")], r
        # (b) la Digital vendida por "Doto" (tercero real del marketplace de Chedraui): no hay oferta
        tabla[CH_BUSCA] = (200, con_existencia(busca, vendedor="Doto", ids={"3879487"}))
        assert leer(ps5, cfg, {}) == [], "un tercero (Doto) en Chedraui paso"
        # (H1) sin vendedor, o sin AvailableQuantity, en la Digital: "formato cambiado", nunca lista vacia
        tabla[CH_BUSCA] = (200, con_existencia(busca, vendedor="", ids={"3879487"}))
        _espera_error(lambda: leer(ps5, cfg, {}), "Chedraui sin vendedor")
        sin_q = copy.deepcopy(busca)
        for p in sin_q:
            for it in p["items"]:
                for s in it["sellers"]:
                    s["commertialOffer"].pop("AvailableQuantity")
        tabla[CH_BUSCA] = (200, json.dumps(sin_q))
        _espera_error(lambda: leer(ps5, cfg, {}), "Chedraui sin AvailableQuantity")
        # (H2) AvailableQuantity y Price como texto se leen (no TypeError)
        texto = copy.deepcopy(busca)
        for p in texto:
            for it in p["items"]:
                for s in it["sellers"]:
                    s["commertialOffer"]["AvailableQuantity"] = "10"
                    s["commertialOffer"]["Price"] = "{:,.2f}".format(s["commertialOffer"]["Price"])
        tabla[CH_BUSCA] = (200, json.dumps(texto))
        assert [(o["var"], o["precio"]) for o in leer(ps5, cfg, {})] == [("digital", 12499.0)]
        # (H8) estructura real de dos vendedores sobre la consola: Doto por defecto con 10 piezas a $9,000 y Chedraui
        # no por defecto con Price 0 y 0 piezas -> nada entra; con Chedraui en existencia, sale vendida por "Chedraui"
        dos = copy.deepcopy(busca)
        consola = [p for p in dos if p["productId"] == "3879487"][0]
        ch = consola["items"][0]["sellers"][0]
        doto = {"sellerId": "doto", "sellerName": "Doto", "sellerDefault": True,
                "commertialOffer": {"Price": 9000.0, "ListPrice": 9000.0, "AvailableQuantity": 10, "IsAvailable": True}}
        ch.update({"sellerDefault": False})
        ch["commertialOffer"].update({"Price": 0.0, "AvailableQuantity": 0})
        consola["items"][0]["sellers"] = [doto, ch]
        tabla[CH_BUSCA] = (200, json.dumps(dos))
        assert leer(ps5, cfg, {}) == [], "la consola de Doto paso"
        ch["commertialOffer"].update({"Price": 12499.0, "AvailableQuantity": 10})
        tabla[CH_BUSCA] = (200, json.dumps(dos))
        assert [(o["precio"], o["vendedor"]) for o in leer(ps5, cfg, {})] == [(12499.0, "Chedraui")]
        # respuesta real con terceros (Doto) en existencia: juegos y control, nada entra
        tabla[CH_API + "?ft=playstation%205%20digital&_from=0&_to=49"] = (200, json.dumps(otra))
        assert leer(ps5, {"consulta": "playstation 5 digital"}, {}) == []
        # (d) 403, 503, HTML en vez de JSON, objeto en vez de lista: RuntimeError
        for resp in ((403, "<html>Access Denied</html>"), (503, ""), (200, "<html>sin datos</html>"), (200, '{"error":1}')):
            tabla[CH_BUSCA] = resp
            _espera_error(lambda: leer(ps5, cfg, {}), "busqueda Chedraui %s" % (resp,))
        # FICHAS["Chedraui"]: hoy agotada -> existencia False; alterada a 10 piezas -> True
        if isinstance(getattr(v, "FICHAS", None), dict):
            f = v.FICHAS["Chedraui"](CH_FICHA)
            assert (f["precio"], f["vendedor"], f["existencia"]) == (12499.0, "Chedraui", False), f
            tabla[CH_API + "?fq=productId:3879487"] = (200, con_existencia(ficha))
            f = v.FICHAS["Chedraui"](CH_FICHA)
            assert (f["precio"], f["vendedor"], f["existencia"]) == (12499.0, "Chedraui", True), f
            assert v.variante(ps5, f["titulo"])["id"] == "digital", f
            for resp in ((403, "x"), (200, "[]")):
                tabla[CH_API + "?fq=productId:3879487"] = resp
                _espera_error(lambda: v.FICHAS["Chedraui"](CH_FICHA), "ficha Chedraui %s" % (resp,))
    finally:
        v.pedir = guardado
    assert v.vendedor_oficial(ps5, "Chedraui", "Chedraui ") and not v.vendedor_oficial(ps5, "Chedraui", "Doto")


def _espera_error(funcion, que):
    try:
        funcion()
    except RuntimeError:
        return
    raise AssertionError("%s no lanzo RuntimeError" % que)


def _probar_fichas_soriana(v, f_fold):
    """FICHAS["Soriana"]: existencia True/False/None (contrato de FICHAS), mismo titulo, precio y vendedor."""
    guardado = v.pedir
    try:
        tabla = {FICHA_FOLD8: (200, f_fold)}
        v.pedir = Servidor(tabla)
        f = v.FICHAS["Soriana"](FICHA_FOLD8)
        assert (f["titulo"], f["precio"], f["vendedor"], f["existencia"], f["importado"]) == (
            "Samsung Galaxy Z Fold8 7.6 Pulgadas 512GB Lavanda Desbloqueado", 36990.0, "Soriana", True, False), f
        tabla[FICHA_FOLD8] = (200, f_fold.replace("schema.org/InStock", "schema.org/OutOfStock"))
        assert v.FICHAS["Soriana"](FICHA_FOLD8)["existencia"] is False
        tabla[FICHA_FOLD8] = (200, f_fold.replace("schema.org/InStock", "schema.org/Raro"))
        assert v.FICHAS["Soriana"](FICHA_FOLD8)["existencia"] is None
        tabla[FICHA_FOLD8] = (403, "<html>Access Denied</html>")
        _espera_error(lambda: v.FICHAS["Soriana"](FICHA_FOLD8), "ficha Soriana 403")
    finally:
        v.pedir = guardado


def _cp_alterar(texto, funcion):
    """La busqueda grabada de Cyberpuerta con su ItemList (dentro de @graph) cambiado por `funcion`, en memoria."""
    import json
    patron = r'(<script[^>]*type="application/ld\+json"[^>]*>)(.*?)(</script>)'

    def cambia(m):
        d = json.loads(m.group(2))
        nodos = [n for n in d.get("@graph", []) if n.get("@type") == "ItemList"] if isinstance(d, dict) else []
        if not nodos:
            return m.group(0)
        funcion(nodos[0])
        return m.group(1) + json.dumps(d, ensure_ascii=False) + m.group(3)
    return re.sub(patron, cambia, texto, flags=re.S)


CP_BUSCA = "https://www.cyberpuerta.mx/index.php?cl=search&searchparam="
CP_FICHA = ("https://www.cyberpuerta.mx/Celulares-y-Telefonia/Celulares/Celulares/"
            "Samsung-Galaxy-Z-FOLD8-5G-7-6-Dual-SIM-512GB-12GB-RAM-Morado.html")


def _probar_cyberpuerta(v, fold, ps5):
    assert "Cyberpuerta" in v.LECTORES, "Cyberpuerta no quedo registrada"
    leer = v.LECTORES["Cyberpuerta"]
    b_fold, b_ps5 = _leer("cyberpuerta_busca_galaxy_z_fold8.html"), _leer("cyberpuerta_busca_playstation_5_digital.html")
    ficha = _leer("cyberpuerta_ficha_fold8_morado.html")
    qf, qp = CP_BUSCA + "galaxy+z+fold8", CP_BUSCA + "playstation+5+digital"
    tabla = {qf: (200, b_fold), qp: (200, b_ps5), CP_FICHA: (200, ficha)}
    guardado = v.pedir
    try:
        v.pedir = srv = Servidor(tabla)
        # (a) Fold8 512 y Ultra 512 en existencia, vendedor Cyberpuerta, liga a su ficha; sin abrir fichas.
        # (H3) los de clave de otra region quedan fuera y se dice: Gris SM-F971BZKMLEM $38,469, Morado F971BLVMGTO
        # $37,459, Blanco F971BZWMGTO $37,589. Entran los de clave sin region, con la nota de confirmar.
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            r = leer(fold, {"consulta": "galaxy z fold8"}, {})
        assert srv.pedidas == [qf], srv.pedidas
        # Ronda 5 (Q1): LEM ya no queda fuera: entra con la nota de revisar la garantia ("revisar" en productos.json)
        assert sorted((o["var"], o["precio"]) for o in r) == [("normal-512", 34319.0)] * 3 + [
            ("normal-512", 38469.0), ("ultra-512", 39999.0)], r
        assert {o["vendedor"] for o in r} == {"Cyberpuerta"} and {o["tienda"] for o in r} == {"Cyberpuerta"}, r
        lem = [o for o in r if o["precio"] == 38469.0]
        assert "clave de modelo LEM: confirma en la ficha que tenga garantía de Samsung México" in lem[0]["nota"], lem
        assert all("confirma en la ficha que sea versión México" in o["nota"] for o in r if o not in lem), r
        assert CP_FICHA in [o["url"] for o in r], r
        fuera = [l for l in salida.getvalue().splitlines() if "clave de otra region" in l]
        assert len(fuera) == 2 and all(any(s in l for l in fuera) for s in ("F971BLVMGTO", "F971BZWMGTO")), fuera   # GTO
        # (H3) la misma clave con region de Mexico (LTM) entra, sin la nota
        tabla[qf] = (200, b_fold.replace('"SM-F971BZKMLEM"', '"SM-F971BZKMLTM"'))
        with contextlib.redirect_stdout(io.StringIO()):
            r = leer(fold, {"consulta": "galaxy z fold8"}, {})
        gris = [o for o in r if o["precio"] == 38469.0]
        assert len(gris) == 1 and "confirma en la ficha" not in gris[0]["nota"], r          # LTM: limpio
        tabla[qf] = (200, b_fold)
        # (H3) la regla vive en productos.json (requisitos.clave_region): el mismo producto sin ella no se filtra por
        # clave (entran los 7 en existencia, sin nota de region)
        import copy
        sin_regla = copy.deepcopy(fold)
        sin_regla["requisitos"].pop("clave_region", None)
        r = leer(sin_regla, {"consulta": "galaxy z fold8"}, {})
        assert len(r) == 7 and not any("región" in o["nota"] for o in r), r
        # (c) agotada: el Ultra 512 Crema ($40,179, OutOfStock) y el Fold8 256 no entran
        assert not [o for o in r if o["precio"] in (40179.0, 33229.0)], r
        # PS5 hoy: las digitales estan agotadas (agotado explicito) y lo demas es de disco o juegos -> lista vacia
        assert leer(ps5, {"consulta": "playstation 5 digital"}, {}) == []
        # la misma respuesta alterada a "en existencia" (LEEME): entran solo las dos Slim Digital (a la PS5 no se le
        # aplica la regla de region: sus claves son de Sony, "1000049662", "20484601008-825ABGT7")
        tabla[qp] = (200, b_ps5.replace("OutOfStock", "InStock"))
        r = leer(ps5, {"consulta": "playstation 5 digital"}, {})
        assert sorted((o["var"], o["precio"]) for o in r) == [("digital", 9479.0), ("digital", 13139.0)], r
        assert not any("región" in o["nota"] for o in r), r
        tabla[qp] = (200, b_ps5)
        # (b) vendedor distinto de Cyberpuerta: no hay oferta (lista vacia: el vendedor si se leyo)
        tabla[qf] = (200, b_fold.replace('"name":"Cyberpuerta"', '"name":"Otra Tienda SA"'))
        with contextlib.redirect_stdout(io.StringIO()):
            assert leer(fold, {"consulta": "galaxy z fold8"}, {}) == [], "un tercero en Cyberpuerta paso"
        # (H1) sin vendedor, sin existencia o articulos sin "item": "formato cambiado", nunca lista vacia
        def sin_campo(campo):
            def f(lista):
                for it in lista["itemListElement"]:
                    it["item"]["offers"].pop(campo, None)
            return f

        def solo_url(lista):
            lista["itemListElement"] = [{"@type": "ListItem", "position": i + 1, "url": it["item"].get("url")}
                                        for i, it in enumerate(lista["itemListElement"])]
        for alterada in (_cp_alterar(b_fold, sin_campo("seller")), _cp_alterar(b_fold, sin_campo("availability")),
                         _cp_alterar(b_fold, solo_url)):
            assert alterada != b_fold, "recorte cambiado"
            tabla[qf] = (200, alterada)
            with contextlib.redirect_stdout(io.StringIO()):
                _espera_error(lambda: leer(fold, {"consulta": "galaxy z fold8"}, {}), "Cyberpuerta sin dato")
        # (d) 403, 503 o pagina sin ld+json: RuntimeError; (H1) ItemList vacio tambien
        for resp in ((403, "<html>Access Denied</html>"), (503, ""), (200, "<html><body>sin datos</body></html>"),
                     (200, '<script type="application/ld+json">{"@context":"https://schema.org","@graph":'
                           '[{"@type":"ItemList","itemListElement":[]}]}</script>')):
            tabla[qf] = resp
            _espera_error(lambda: leer(fold, {"consulta": "galaxy z fold8"}, {}), "busqueda Cyberpuerta %s" % (resp[0],))
        # (H2) precio ilegible en un juego no tumba la tienda; en un Fold8, si
        tabla[qp] = (200, b_ps5.replace('"price":1579', '"price":"Desde 1,579"'))
        assert '"Desde 1,579"' in tabla[qp][1], "recorte cambiado"
        assert leer(ps5, {"consulta": "playstation 5 digital"}, {}) == []
        tabla[qf] = (200, b_fold.replace('"price":34319', '"price":"Desde 34,319"', 1))
        assert '"Desde 34,319"' in tabla[qf][1], "recorte cambiado"
        _espera_error(lambda: leer(fold, {"consulta": "galaxy z fold8"}, {}), "precio ilegible en un Fold8")
        tabla[qf], tabla[qp] = (200, b_fold), (200, b_ps5)
        # FICHAS["Cyberpuerta"]: titulo del <h1> (el name del ld+json es el modelo), precio, vendedor, existencia
        f = v.FICHAS["Cyberpuerta"](CP_FICHA) if isinstance(getattr(v, "FICHAS", None), dict) else None
        if f is not None:
            assert (f["titulo"], f["precio"], f["vendedor"], f["existencia"]) == (
                'Samsung Galaxy Z FOLD8 5G 7.6" Dual SIM, 512GB, 12GB RAM, Morado', 34319.0, "Cyberpuerta", True), f
            assert v.variante(fold, f["titulo"])["id"] == "normal-512"
            tabla[CP_FICHA] = (200, ficha.replace('"availability":"InStock"', '"availability":"OutOfStock"'))
            assert v.FICHAS["Cyberpuerta"](CP_FICHA)["existencia"] is False
            tabla[CP_FICHA] = (200, ficha.replace('"availability":"InStock"', '"availability":"PreOrder"'))
            assert v.FICHAS["Cyberpuerta"](CP_FICHA)["existencia"] is None
            # (H3) clave sin region = no importado; clave de otra region = importado
            assert f["importado"] is False and f["sku"] == "SM-F971B/512/VA", f
            tabla[CP_FICHA] = (200, ficha.replace('"sku":"SM-F971B/512/VA"', '"sku":"F971BLVMGTO"'))
            assert '"F971BLVMGTO"' in tabla[CP_FICHA][1], "recorte cambiado"
            assert v.FICHAS["Cyberpuerta"](CP_FICHA)["importado"] is True
            tabla[CP_FICHA] = (200, ficha.replace('"sku":"SM-F971B/512/VA"', '"sku":"SM-F971BZKMLEM"'))
            assert v.FICHAS["Cyberpuerta"](CP_FICHA)["importado"] is False      # LEM: a revisar, no importado
            for resp in ((403, "x"), (200, "<html>ficha sin datos</html>")):
                tabla[CP_FICHA] = resp
                _espera_error(lambda: v.FICHAS["Cyberpuerta"](CP_FICHA), "ficha Cyberpuerta %s" % (resp,))
    finally:
        v.pedir = guardado
    # (e) de disco, juegos y una garantia (supuesta, a precio de consola) quedan fuera
    for titulo in ("Sony PlayStation 5 Slim 1TB, Wi-Fi, Lector de Discos, Negro/Blanco",
                   "Playstation Astro Bot y Gran Turismo 7 1TB, Wi-Fi, Lector de Discos, Negro/Blanco",
                   "EA SPORTS FC 26, PlayStation 5",
                   "Garantía extendida 2 años para PlayStation 5 Digital"):
        assert v.oferta(ps5, "Cyberpuerta", titulo, 11990, "u", "Cyberpuerta") is None, titulo
    assert v.vendedor_oficial(ps5, "Cyberpuerta", " CYBERPUERTA ") and not v.vendedor_oficial(ps5, "Cyberpuerta", "Cyberpuerta Marketplace")
