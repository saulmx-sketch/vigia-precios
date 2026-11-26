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
    _probar_enganche(v)
    if not os.path.exists(os.path.join(RAIZ, "lectores_nuevos.py")):
        return 1
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
    # "Unidad de Disco para Consolas PS5 Digital" pasa la regla de titulos (dice "Consolas" y "Digital"): la ficha
    # se abre y la deja fuera solo el piso ($5,190 < $6,500). Hallazgo del 25 nov, anotado en H7_lectores.md.
    abre = {"playstation 5 digital": [FICHA_PS5, FICHA_UNIDAD], "consola playstation 5": [FICHA_PS5]}
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
        tabla[FICHA_FOLD8] = (200, re.sub(r'"seller":\{[^}]*\}', '"seller":{}', f_fold))
        assert leer(fold, {"consulta": "galaxy z fold8"}, {}) == [], "sin vendedor leido se publico"
        # (c) agotada: no hay oferta
        tabla[FICHA_FOLD8] = (200, f_fold.replace("schema.org/InStock", "schema.org/OutOfStock"))
        assert leer(fold, {"consulta": "galaxy z fold8"}, {}) == [], "una ficha agotada produjo oferta"
        tabla[FICHA_FOLD8] = (200, f_fold)
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
            try:
                leer(fold, {"consulta": "galaxy z fold8"}, {})
            except RuntimeError:
                pass
            else:
                raise AssertionError("ficha %s no lanzo RuntimeError" % (resp,))
        tabla[FICHA_FOLD8] = (200, f_fold)
        # (e) si el listado no trae el nombre, se abre la ficha y el filtro de oferta() deja fuera accesorios
        lista_sin_nombre = ('<script type="application/ld+json">{"@type":"ItemList","itemListElement":'
                            '[{"@type":"ListItem","position":1,"url":"%s"}]}</script>' % FICHA_UNIDAD)
        tabla[_busca("unidad ps5")] = (200, lista_sin_nombre)
        assert leer(ps5, {"consulta": "unidad ps5"}, {}) == [], "la unidad de disco paso como consola"
        # (e) con la PS5 con lector (titulo supuesto: Soriana no la lista hoy) tampoco hay oferta
        tabla[FICHA_UNIDAD] = (200, f_ps5.replace("Consola PlayStation 5 Digital Slim + Astro Bot y Gran Turismo 7 825GB",
                                                  "Consola PlayStation 5 Slim Standard con Lector de Discos 1TB"))
        assert leer(ps5, {"consulta": "unidad ps5"}, {}) == [], "la PS5 con lector paso como digital"
    finally:
        v.pedir = guardado
    # (e) titulos de Soriana fuera: accesorios y la PS5 con lector
    # (a precio de consola, para que el titulo sea lo que decide; la unidad de disco va a su precio real: a $11,990
    # pasaria, ver el hallazgo de arriba)
    for titulo, precio in (("Unidad de Disco para Consolas PS5 Digital", 5190),
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
    return 1
