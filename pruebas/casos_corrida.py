"""Corrida completa simulada: main() de punta a punta, muchas veces seguidas, sin red y con reloj falso.

Cada escena es una secuencia de corridas de vigia.main() sobre el mismo estado, dentro de una carpeta temporal
(tempfile, se borra al final). El servidor falso es un diccionario {regex de direccion: respuesta} armado con las
respuestas grabadas de pruebas\\respuestas (y variantes alteradas en memoria); lo que no conoce responde 404 y cada
peticion queda anotada. abrir_issue anota asunto y cuerpo; time.sleep no duerme; el "ahora" de main sale de un
datetime falso que el arnes adelanta entre corridas (15 min, o lo que pida la escena).

Lo que el arnes sustituye y restaura siempre (try/finally): vigia.pedir, vigia.abrir_issue, vigia.cargar_config (para
leer la copia de productos.json: su valor por defecto quedo fijado al importar), vigia.ESTADO, vigia.PRECIOS,
vigia.HISTORIAL, vigia.dt, time.sleep, urllib.request.build_opener (lo arma el carrito de Samsung; cuesta 30 ms por
los certificados y aqui no se usa), sys.argv y las variables de entorno PRUEBA y SIN_AVISOS_PRECIO.

Fallas del programa halladas: van en PENDIENTES (se imprimen; no tumban el selftest).
Uso:  py -X utf8 pruebas\\casos_corrida.py   (o vigia.py --selftest, via correr(v))
"""
import contextlib
import copy
import datetime as _dt
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import types
import urllib.error

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
RESP = os.path.join(AQUI, "respuestas")
INICIO = _dt.datetime(2026, 11, 27, 13, 5, tzinfo=_dt.timezone.utc)
K_COS = "Costco|fold8|normal-512"
K_ELE = "Elektra|ps5|digital"
PENDIENTES = []   # fallas del programa: (escena, que se esperaba, que paso)


def lee(n):
    with open(os.path.join(RESP, n), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ respuestas (grabadas y alteradas en memoria)
def costco_producto(base=44999.0, descuento=7000.0, nombre=None, externo=False, crudo_base=None):
    p = json.loads(lee("cos_api_648324.json"))
    p["basePrice"]["value"] = base if crudo_base is None else crudo_base
    p["couponDiscount"]["discountValue"] = descuento
    if nombre:
        p["name"] = nombre
    if externo:
        p["isExternalVendorProduct"] = True
    return p


def costco_busqueda(*productos):
    return 200, json.dumps({"products": list(productos)})


def costco_ficha(**kw):
    return 200, json.dumps(costco_producto(**kw))


ELE_PS5_ID = "1302999999"


def elektra_ps5(precio=7400.0, vendedor="Elektra"):
    """Renglon de Elektra armado con la forma de ele_api_1302051913.json (no hay grabada una PS5 de Elektra)."""
    d = json.loads(lee("ele_api_1302051913.json"))
    p = d[0]
    p["productId"] = ELE_PS5_ID
    p["productName"] = p["items"][0]["name"] = p["items"][0]["nameComplete"] = "Consola PlayStation 5 Digital 825 GB"
    p["link"] = "https://www.elektra.mx/consola-playstation-5-digital-%s/p" % ELE_PS5_ID
    s = p["items"][0]["sellers"]
    s[0]["sellerName"], s[0]["commertialOffer"]["Price"] = vendedor, precio
    del s[1:]
    return 200, json.dumps(d)


GP_CATALOGO = ('<ul data-testid="products-list"><li><a data-testid="product-card" '
               'href="/products/consola-playstation-5-digital-fortnite-flowering-playstation-5">'
               '<h3>Consola PlayStation 5 Digital Fortnite Flowering</h3>'
               '<span data-testid="product-price" data-value="10999.99"></span></a></li></ul>')
FORO_RSS = ('<?xml version="1.0"?><rss><channel><item><title>Licuadora de 10 velocidades</title>'
            '<link>https://www.promodescuentos.com/ofertas/licuadora-1</link><guid>corrida-g1</guid>'
            '<pubDate>Fri, 27 Nov 2026 06:00:00 -0600</pubDate><description>nada que vigilar</description></item>'
            '</channel></rss>')
COSTCO_SEARCH_FOLD = r"costco\.com\.mx/rest/v2/mexico/products/search\?query=galaxy"
COSTCO_FICHA = r"costco\.com\.mx/rest/v2/mexico/products/648324\?"
ELEKTRA_PS5 = r"elektra\.mx/api/catalog_system/pub/products/search\?ft=consola"
ELEKTRA_FICHA = r"elektra\.mx/api/catalog_system/pub/products/search\?fq=productId:" + ELE_PS5_ID


def mundo_base():
    """Un dia normal: todas las fuentes responden y nada baja de su tope (orden = prioridad de casamiento)."""
    m = {}
    m[r"promodescuentos\.com/rss/"] = (200, FORO_RSS)
    m[r"promodescuentos\.com/search\?"] = (200, "<html>sin hilos</html>")
    m[r"searchapi\.samsung\.com/"] = "sam_catalogo.json"          # catalogo; el carrito (shop.samsung.com) da 404
    m[r"www\.samsung\.com/mx/"] = (200, "<html>sin cupones a la vista</html>")
    m[r"amazon\.com\.mx/s\?k="] = "amz_busca_ps5.html"           # Fold8: la busqueda no trae ninguno (marca)
    m[r"amazon\.com\.mx/dp/B0FTP523RH"] = "amz_ficha_B0FTP523RH.html"
    m[COSTCO_SEARCH_FOLD] = costco_busqueda(costco_producto())   # $44,999 - $7,000 = $37,999
    m[r"costco\.com\.mx/rest/v2/mexico/products/search\?query=playstation"] = costco_busqueda()
    m[COSTCO_FICHA] = costco_ficha()
    m[r"elektra\.mx/api/catalog_system/pub/products/search\?ft=galaxy"] = "ele_api_1302051913.json"  # Systore: fuera
    m[ELEKTRA_PS5] = (200, "[]")
    m[ELEKTRA_FICHA] = elektra_ps5()
    m[r"liverpool\.com\.mx/tienda\?s=galaxy"] = "liv_busca_fold8.html"
    m[r"liverpool\.com\.mx/tienda\?s=playstation"] = "liv_busca_ps5.html"
    for pid, arch in (("1203150349", "liv_pdp_1203150349.html"), ("1156059630", "liv_pdp_1156059630_6999.html"),
                      ("1207352230", "liv_pdp_1207352230_alterada.html"), ("1186785807", "liv_pdp_1186785807.html"),
                      ("1186785793", "liv_pdp_1186785793.html")):
        m[r"liverpool\.com\.mx/tienda/pdp/p/" + pid] = arch
    m[r"gameplanet\.com/catalogo\?q="] = (200, GP_CATALOGO)
    m[r"gameplanet\.com/products/"] = "gp_pdp_fortnite.html"
    # lectores_nuevos.py (las mismas grabadas y direcciones que pruebas\casos_lectores_nuevos.py)
    m[r"soriana\.com/buscar\?q=galaxy\+z\+fold8"] = "soriana_busca_galaxy_z_fold8.html"
    m[r"soriana\.com/buscar\?q=playstation\+5\+digital"] = "soriana_busca_playstation_5_digital.html"
    m[r"soriana\.com/buscar\?q="] = "soriana_busca_vacia.html"
    m[r"soriana\.com/.*/11944979\.html"] = "soriana_ficha_fold8.html"
    m[r"soriana\.com/.*/11912222\.html"] = "soriana_ficha_ps5.html"
    m[r"soriana\.com/.*/11864204\.html"] = "soriana_ficha_unidad_disco.html"
    m[r"cyberpuerta\.mx/index\.php\?cl=search&searchparam=galaxy"] = "cyberpuerta_busca_galaxy_z_fold8.html"
    m[r"cyberpuerta\.mx/index\.php\?cl=search&searchparam=playstation"] = "cyberpuerta_busca_playstation_5_digital.html"
    m[r"cyberpuerta\.mx/.*Samsung-Galaxy-Z-FOLD8-5G-7-6-Dual-SIM-512GB-12GB-RAM-Morado\.html"] = "cyberpuerta_ficha_fold8_morado.html"
    m[r"chedraui\.com\.mx/api/catalog_system/pub/products/search\?fq=productId:3879487"] = (200, chedraui("chedraui_ficha_3879487.json"))
    m[r"chedraui\.com\.mx/api/catalog_system/pub/products/search\?ft="] = (
        200, chedraui("chedraui_busca_consola_playstation_5_digital.json"))
    return m


# Tiendas a las que el servidor falso sabe responder. Una tienda de productos.json que no este aqui (o sin lector
# cargado) se quita de la copia de productos.json de cada escena: asi una tienda nueva no rompe estas escenas.
SERVIDAS = ("Samsung", "Amazon", "Costco", "Elektra", "Liverpool", "Gameplanet", "Soriana", "Cyberpuerta", "Chedraui")
NUEVAS = ("Soriana", "Cyberpuerta", "Chedraui")   # las de lectores_nuevos.py (escena 12)


def chedraui(n):
    return json.dumps(json.loads(lee(n))["respuesta"])


def con(base, **cambios):
    m = dict(base)
    for k, r in cambios.items():
        m[{"cos": COSTCO_SEARCH_FOLD, "cos_ficha": COSTCO_FICHA, "ele": ELEKTRA_PS5, "ele_ficha": ELEKTRA_FICHA}[k]] = r
    return m


def costco_a(precio, **ficha):
    """Mundo base con Costco (listado y ficha) al precio dado; ficha=... altera solo la ficha."""
    base, desc = precio + 7000.0, 7000.0
    m = con(mundo_base(), cos=costco_busqueda(costco_producto(base, desc)))
    m[COSTCO_FICHA] = ficha["respuesta"] if "respuesta" in ficha else costco_ficha(base=base, descuento=desc, **ficha)
    return m


class Servidor:
    """Diccionario direccion (regex) -> respuesta: (estado, texto), nombre de archivo grabado o funcion(url)."""

    def __init__(self, mapa):
        self.mapa, self.pedidas = mapa, []

    def __call__(self, url, datos=None, metodo=None, abridor=None, json_api=False, pausa=0.0, reintento=2.0, intentos=2):
        self.pedidas.append(url)
        for patron, r in self.mapa.items():
            if re.search(patron, url):
                if callable(r):
                    r = r(url)
                return r if isinstance(r, tuple) else (200, lee(r))
        return 404, ""

    def cuantas(self, patron):
        return sum(1 for u in self.pedidas if re.search(patron, u))


# ------------------------------------------------------------------ banco de pruebas (una carpeta por escena)
class Banco:
    def __init__(self, v, visto=None):
        self.v = v
        self.dir = tempfile.mkdtemp(prefix="vigia_corrida_")
        self.config = os.path.join(self.dir, "productos.json")
        # siempre productos.json de la raiz, aunque VIGIA_CONFIG apunte a otro archivo (ver correr: v.CONFIG)
        with open(os.path.join(RAIZ, "productos.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.quitadas = set()
        for p in cfg["productos"]:   # solo tiendas con respuesta en el servidor falso y con lector cargado
            for t in list(p["tiendas"]):
                if t not in SERVIDAS or t not in v.LECTORES:
                    self.quitadas.add(t)
                    del p["tiendas"][t]
        self.escribe(self.config, json.dumps(cfg, ensure_ascii=False))
        self.tiendas = sorted({t for p in cfg["productos"] for t in p["tiendas"]})
        self.estado = os.path.join(self.dir, "visto.json")
        self.precios = os.path.join(self.dir, "precios.json")
        self.historial = os.path.join(self.dir, "historial_precios.csv")
        self.escribe(self.estado, json.dumps(visto if visto is not None else {}))
        self.escribe(self.precios, "{}")
        self.escribe(self.historial, "fecha,tienda,producto,precio,vendedor,como,liga\n")
        self.reloj = INICIO
        self.issues, self.fallar = [], 0
        self.ultima = None

    @staticmethod
    def escribe(ruta, texto):
        with open(ruta, "w", encoding="utf-8", newline="") as f:
            f.write(texto)

    def borrar(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def json(self, ruta=None):
        with open(ruta or self.estado, encoding="utf-8") as f:
            return json.load(f)

    def renglones(self):
        with open(self.historial, encoding="utf-8") as f:
            return f.read().splitlines()

    def _abrir(self, asunto, cuerpo):
        if self.fallar:
            self.fallar -= 1
            raise RuntimeError("GitHub no responde (simulado)")
        self.issues.append((asunto, cuerpo))
        return True

    def correr(self, mapa, args=(), env=None, avanza=_dt.timedelta(minutes=15), lento=0, lento_si=None):
        """Una corrida de main(). Devuelve (codigo, avisos de esta corrida, servidor, salida impresa).
        lento=N: se usa el pedir() de verdad sobre un transporte falso; cada peticion (cuya direccion case con
        lento_si, o todas) adelanta N segundos el reloj falso de vigia.reloj(). srv.inicios = [(segundo, url)]."""
        v, banco = self.v, self
        self.mono = getattr(self, "mono", 1000.0)

        class Ahora(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return banco.reloj.astimezone(tz) if tz else banco.reloj.replace(tzinfo=None)
        dt_falso = types.ModuleType("dt_falso")
        dt_falso.__dict__.update({k: getattr(_dt, k) for k in dir(_dt) if not k.startswith("__")})
        dt_falso.datetime = Ahora

        srv = Servidor(mapa)
        srv.inicios, mono0 = [], self.mono

        def transporte(req, timeout=None):
            url = req.full_url
            srv.inicios.append((banco.mono - mono0, url, timeout))
            if not lento_si or re.search(lento_si, url):
                banco.mono += lento
            estado, texto = srv(url)
            cuerpo = texto.encode("utf-8")
            if estado >= 400:
                raise urllib.error.HTTPError(url, estado, "falso", {}, io.BytesIO(cuerpo))
            return Respuesta(estado, cuerpo)
        # junta-2: SIN_PROMOS=1 por defecto. promos.mejorar (linea B) relee la ficha de Costco cada 60 min de reloj
        # falso (descuadra las cuentas de peticiones de las escenas) y duerme con el time.sleep real que ató al
        # importarse; sus casos viven en promos.py --selftest. Una escena que quiera promos pasa env={"SIN_PROMOS": None}.
        env = dict({"PRUEBA": None, "SIN_AVISOS_PRECIO": None, "VIGIA_CONFIG": None, "SIN_PROMOS": "1"}, **(env or {}))
        # VIGIA_CONFIG se lee al importar (v.CONFIG): si quedo puesta, main() se negaria a escribir estado y avisar.
        # Durante cada corrida v.CONFIG es la copia de la escena (que cargar_config lee de todos modos).
        guardado = (v.pedir, v.abrir_issue, v.cargar_config, v.ESTADO, v.PRECIOS, v.HISTORIAL, v.dt, time.sleep,
                    list(sys.argv), {k: os.environ.get(k) for k in env}, v.urllib.request.build_opener,
                    getattr(v, "CONFIG", None), getattr(v, "CONFIG_PRINCIPAL", None), getattr(v, "reloj", None),
                    v.urllib.request.urlopen)
        original_config = v.cargar_config
        n0 = len(self.issues)
        salida = io.StringIO()
        try:
            v.pedir, v.abrir_issue = srv, self._abrir
            v.cargar_config = lambda ruta=None: original_config(self.config)
            v.ESTADO, v.PRECIOS, v.HISTORIAL, v.dt = self.estado, self.precios, self.historial, dt_falso
            v.CONFIG = self.config
            if hasattr(v, "CONFIG_PRINCIPAL"):
                v.CONFIG_PRINCIPAL = self.config
            time.sleep = lambda *a, **k: None
            # el carrito de Samsung arma un abridor con cookies (carga los certificados de Windows: 30 ms cada vez);
            # el servidor falso no lo usa, asi que basta un objeto vacio
            v.urllib.request.build_opener = lambda *a, **k: types.SimpleNamespace(open=transporte if lento else None)
            if guardado[13] is not None:
                v.reloj = lambda: banco.mono      # reloj falso de los topes de tiempo (solo avanza con lento)
            if lento:
                v.pedir = pedir_de_verdad(v)
                v.urllib.request.urlopen = transporte
            sys.argv = ["vigia.py"] + list(args)
            for k, x in env.items():
                if x is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = x
            with contextlib.redirect_stdout(salida):
                codigo = v.main()
        finally:
            (v.pedir, v.abrir_issue, v.cargar_config, v.ESTADO, v.PRECIOS, v.HISTORIAL, v.dt, time.sleep,
             sys.argv) = guardado[:9]
            v.urllib.request.build_opener = guardado[10]
            v.CONFIG = guardado[11]
            if guardado[12] is not None:
                v.CONFIG_PRINCIPAL = guardado[12]
            if guardado[13] is not None:
                v.reloj = guardado[13]
            v.urllib.request.urlopen = guardado[14]
            for k, x in guardado[9].items():
                if x is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = x
            self.reloj += avanza
        self.ultima = salida.getvalue()
        return codigo, self.issues[n0:], srv, self.ultima


class Respuesta:
    def __init__(self, status, cuerpo):
        self.status, self._b, self.headers = status, cuerpo, {"Content-Type": "text/html; charset=utf-8"}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def pedir_de_verdad(v):
    """El pedir() de vigia.py, recompilado de su fuente con los globales del modulo: durante --selftest v.pedir esta
    sustituido por una funcion que prohibe la red, y la escena 13 necesita el de verdad (con su tope duro)."""
    import ast
    with open(v.__file__, encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    nodo = next(n for n in arbol.body if isinstance(n, ast.FunctionDef) and n.name == "pedir")
    local = {}
    exec(compile(ast.Module(body=[nodo], type_ignores=[]), v.__file__, "exec"), v.__dict__, local)
    return local["pedir"]


def asuntos(avs):
    return [a for a, _ in avs]


def afirma(cond, msg, banco=None):
    if not cond:
        raise AssertionError("%s\n--- salida de la ultima corrida (cola) ---\n%s" % (
            msg, (banco.ultima or "")[-1500:] if banco else ""))


def pendiente(escena, cond, esperado, paso):
    """Falla del programa ya reportada: no tumba el selftest, se anota y se imprime."""
    if not cond:
        PENDIENTES.append((escena, esperado, paso))


# ------------------------------------------------------------------ escenas
def e1_dia_normal(v):
    b = Banco(v)
    try:
        c, avs, srv, _ = b.correr(mundo_base())
        afirma(c == 0 and avs == [], ("1a: avisos en un dia normal", asuntos(avs)), b)
        est = b.json()
        nombres = {"Foro", "Cupones"} | set(b.tiendas)
        afirma(set(est["fallos_fuente"]) == nombres and not any(est["fallos_fuente"].values()),
               ("1a: alguna fuente fallo (el mundo falso esta incompleto)", est["fallos_fuente"], est.get("ultimo_error")), b)
        tabla = b.json(b.precios)
        for k in ("Samsung|fold8|normal-512", "Amazon|ps5|digital", K_COS, "Liverpool|ps5|digital", "Gameplanet|ps5|digital"):
            afirma(k in tabla, ("1a: falta en precios.json", k, sorted(tabla)), b)
        afirma(tabla[K_COS]["precio"] == 37999.0, tabla[K_COS], b)
        n = len(b.renglones())
        afirma(n == 1 + len(tabla), ("1a: historial", n, len(tabla)), b)
        c, avs, srv2, _ = b.correr(mundo_base())
        afirma(c == 0 and avs == [] and len(b.renglones()) == n, ("1b: segunda corrida", asuntos(avs), len(b.renglones())), b)
        return "2 corridas, 0 avisos, %d precios en la tabla, historial %d renglones sin crecer, %d peticiones por corrida" % (
            len(tabla), n, len(srv2.pedidas))
    finally:
        b.borrar()


def e2_baja_confirmada(v):
    b = Banco(v)
    try:
        b.correr(mundo_base())
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(len(avs) == 1, ("2b: se esperaba un aviso", asuntos(avs)), b)
        a, cu = avs[0]
        afirma(a == "GANGA $19,000 · Fold8 512 GB · Costco", a, b)
        for x in ("Confirmado: se releyó la ficha", "https://www.costco.com.mx/Electronicos/", "/p/648324",
                  "lo vende Costco (pide membresia)", "**$19,000**"):
            afirma(x in cu, ("2b: falta en el cuerpo", x, cu[:600]), b)
        afirma(srv.cuantas(COSTCO_FICHA) == 1, ("2b: fichas releidas", srv.cuantas(COSTCO_FICHA)), b)
        est = b.json()
        afirma(est["avisado"][K_COS] == 19000.0 and est["avisado_nivel"][K_COS] == ["GANGA", 19000.0], est["avisado"], b)
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(avs == [] and srv.cuantas(COSTCO_FICHA) == 0, ("2c: repitio", asuntos(avs)), b)
        c, avs, srv, _ = b.correr(costco_a(19000.0 - v.BAJA_MINIMA - 100))
        afirma(asuntos(avs) == ["GANGA $18,600 · Fold8 512 GB · Costco"], ("2d", asuntos(avs)), b)
        afirma("Confirmado" in avs[0][1], avs[0][1][:400], b)
        return "4 corridas: 0, 1 GANGA confirmado ($19,000), 0, 1 mas tras bajar $400 ($18,600)"
    finally:
        b.borrar()


def e3_listado_miente(v):
    b = Banco(v)
    try:
        b.correr(mundo_base())
        c, avs, srv, _ = b.correr(costco_a(19000.0, externo=True))
        afirma(asuntos(avs) == ["SIN CONFIRMAR $19,000 · Fold8 512 GB · Costco · abre la ficha antes de creerlo"],
               ("3b", asuntos(avs)), b)
        afirma("lo vende vendedor externo en Costco, no la tienda ni la marca" in avs[0][1], avs[0][1][:500], b)
        afirma(b.json()["avisado_nivel"][K_COS] == ["SIN CONFIRMAR", 19000.0], b.json()["avisado_nivel"], b)
        c, avs, srv, _ = b.correr(costco_a(19000.0, externo=True))
        afirma(avs == [], ("3c: repitio el SIN CONFIRMAR", asuntos(avs)), b)
        releida = srv.cuantas(COSTCO_FICHA)
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(asuntos(avs) == ["GANGA $19,000 · Fold8 512 GB · Costco"] and "Confirmado" in avs[0][1], ("3d", asuntos(avs)), b)
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(avs == [], ("3e: repitio el confirmado", asuntos(avs)), b)
        # 3f: la ficha dice otro articulo (banco nuevo)
        b2 = Banco(v)
        try:
            c, avs, srv, _ = b2.correr(costco_a(19000.0, nombre="Samsung Galaxy Tab S10 Ultra 512GB Grafito"))
            afirma(len(avs) == 1 and avs[0][0].startswith("SIN CONFIRMAR $19,000") and
                   'es otro artículo: la ficha lo titula "Samsung Galaxy Tab S10' in avs[0][1], ("3f", avs), b2)
        finally:
            b2.borrar()
        return ("5 corridas (tercero): SIN CONFIRMAR con la razon, 0 (la ficha se relee igual: %d peticion), "
                "GANGA confirmado una vez, 0; y otro articulo: SIN CONFIRMAR con su razon" % releida)
    finally:
        b.borrar()


def e4_ficha_caida(v):
    b = Banco(v)
    try:
        b.correr(mundo_base())
        c, avs, srv, _ = b.correr(costco_a(19000.0, respuesta=(503, "Service Unavailable")))
        afirma(asuntos(avs) == ["GANGA $19,000 · Fold8 512 GB · Costco · ficha sin releer"], ("4b", asuntos(avs)), b)
        afirma("No se pudo releer la ficha (la tienda no la entregó: Costco respondio 503 en la ficha 648324)" in avs[0][1],
               avs[0][1][:700], b)
        afirma(b.json()["avisado_nivel"][K_COS] == ["GANGA", 19000.0, "releer"], b.json()["avisado_nivel"], b)
        afirma(b.json()["avisado"][K_COS] == 19000.0, b.json()["avisado"], b)       # lo que lee la version vieja: numero
        # 4c: la ficha sigue caida cinco corridas: se relee cada vez (queda pendiente) y no se repite ningun correo
        for i in range(5):
            c, avs, srv, _ = b.correr(costco_a(19000.0, respuesta=(503, "Service Unavailable")))
            afirma(avs == [] and srv.cuantas(COSTCO_FICHA) == 1, ("4c%d" % i, asuntos(avs), srv.cuantas(COSTCO_FICHA)), b)
        # 4d: la ficha se deja leer y coincide: un solo correo mas, "Confirmado", y ya no se relee
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(asuntos(avs) == ["GANGA $19,000 · Fold8 512 GB · Costco"], ("4d", asuntos(avs)), b)
        afirma(avs[0][1].startswith("Confirmación del aviso anterior") and "Confirmado: se releyó la ficha" in avs[0][1],
               avs[0][1][:400], b)
        afirma(b.json()["avisado_nivel"][K_COS] == ["GANGA", 19000.0, "hecho"], b.json()["avisado_nivel"], b)
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(avs == [] and srv.cuantas(COSTCO_FICHA) == 0, ("4e: repitio o releyo", asuntos(avs)), b)
        # 4f (banco nuevo): sin releer y luego la ficha dice tercero: un correo de correccion, SIN CONFIRMAR, y ya no mas
        b2 = Banco(v)
        try:
            b2.correr(mundo_base())
            b2.correr(costco_a(19000.0, respuesta=(503, "Service Unavailable")))
            c, avs, srv, _ = b2.correr(costco_a(19000.0, externo=True))
            afirma(asuntos(avs) == ["SIN CONFIRMAR $19,000 · Fold8 512 GB · Costco · abre la ficha antes de creerlo"],
                   ("4f", asuntos(avs)), b2)
            afirma(avs[0][1].startswith("Corrección del aviso anterior") and "vendedor externo en Costco" in avs[0][1],
                   avs[0][1][:500], b2)
            afirma(b2.json()["avisado_nivel"][K_COS] == ["SIN CONFIRMAR", 19000.0], b2.json()["avisado_nivel"], b2)
            # 4g (R4-H1): la correccion no cierra la clave: se sigue releyendo como todo SIN CONFIRMAR, sin repetir correo
            for i in range(2):
                c, avs, srv, _ = b2.correr(costco_a(19000.0, externo=True))
                afirma(avs == [] and srv.cuantas(COSTCO_FICHA) == 1, ("4g%d: repitio la correccion" % i, asuntos(avs)), b2)
            # 4h: la ficha ya confirma: un correo confirmado mas, y ahi termina (sin bucle)
            c, avs, srv, _ = b2.correr(costco_a(19000.0))
            afirma(asuntos(avs) == ["GANGA $19,000 · Fold8 512 GB · Costco"] and "Confirmado" in avs[0][1], ("4h", asuntos(avs)), b2)
            for i in range(2):
                c, avs, srv, _ = b2.correr(costco_a(19000.0))
                afirma(avs == [] and srv.cuantas(COSTCO_FICHA) == 0, ("4i%d: bucle tras confirmar" % i, asuntos(avs)), b2)
        finally:
            b2.borrar()
        return ("4 corridas: 1 GANGA 'ficha sin releer'; cinco corridas con la ficha caida: 0 correos (se relee, queda "
                "pendiente); al leerse: 1 'Confirmacion del aviso anterior' y ya no se relee; y con tercero: 1 correccion "
                "SIN CONFIRMAR, se sigue releyendo sin repetir, y al confirmar 1 correo mas y nada despues")
    finally:
        b.borrar()


def e5_tienda_caida(v):
    b = Banco(v)
    try:
        b.correr(mundo_base(), avanza=_dt.timedelta(hours=1))
        caida = con(mundo_base(), cos=(403, "<html>Access Denied</html>"))
        todos = []
        for i in range(4):
            c, avs, srv, _ = b.correr(caida, avanza=_dt.timedelta(hours=1))
            todos.append(asuntos(avs))
            tabla = b.json(b.precios)
            afirma(tabla.get(K_COS, {}).get("precio") == 37999.0, ("5: la tabla perdio a Costco", i, tabla.get(K_COS)), b)
            afirma(not any(a.startswith("Agotado") for a in asuntos(avs)), ("5: agotado con la tienda caida", avs), b)
        afirma(todos == [[], [], ["El vigia no puede leer Costco desde hace 2 horas"], []], ("5: avisos", todos), b)
        est = b.json()
        afirma(K_COS not in est.get("ausente", {}) and K_COS not in est.get("agotados", {}), est.get("ausente"), b)
        c, avs, srv, _ = b.correr(mundo_base())
        afirma(asuntos(avs) == ["El vigia volvio a leer Costco"], ("5f", asuntos(avs)), b)
        return ("6 corridas, reloj +1 h por corrida: 403 cuatro veces sin 'Agotado' y con $37,999 en la tabla; "
                "'no puede leer Costco' una vez (a las 2 h); al volver, 'volvio a leer'")
    finally:
        b.borrar()


def e6_agotado_regreso(v):
    b = Banco(v)
    try:
        b.correr(mundo_base())
        vacia = con(mundo_base(), cos=costco_busqueda())
        vistos = []
        for _ in range(v.AUSENCIAS_AGOTADO):
            c, avs, srv, _ = b.correr(vacia)
            vistos.append(asuntos(avs))
        afirma(vistos == [[], [], [], ["Agotado: Fold8 512 GB ya no esta a la venta en Costco"]], ("6a", vistos), b)
        c, avs, srv, _ = b.correr(vacia)
        afirma(avs == [], ("6b: repitio el agotado", asuntos(avs)), b)
        c, avs, srv, _ = b.correr(costco_a(21000.0))
        a = asuntos(avs)
        afirma(a == ["COMPRAR $21,000 · Fold8 512 GB · Costco",
                     "Volvio a la venta: Fold8 512 GB en Costco a $21,000 · mira el aviso de precio de esta corrida"],
               ("6c", a), b)
        afirma("Confirmado" in avs[0][1] and not re.search(r"GANGA|COMPRAR|SIN CONFIRMAR", a[1]) and
               "**$21,000**" in avs[1][1], ("6c: se contradicen", avs), b)
        c, avs, srv, _ = b.correr(costco_a(21000.0))
        afirma(avs == [], ("6d", asuntos(avs)), b)
        return ("8 corridas: 'Agotado' solo a la 4a ausencia y una vez; regreso a $21,000: COMPRAR confirmado + "
                "'Volvio a la venta ... mira el aviso de precio' (mismo precio, sin nivel propio); luego 0")
    finally:
        b.borrar()


def e7_pruebas_fallidas(v):
    b = Banco(v)
    try:
        b.correr(mundo_base())
        vacia = con(mundo_base(), cos=costco_busqueda())
        for _ in range(v.AUSENCIAS_AGOTADO):
            b.correr(vacia)
        antes = b.json()
        afirma(K_COS in antes["agotados"], antes.get("agotados"), b)
        c, avs, srv, sal = b.correr(costco_a(19000.0), env={"SIN_AVISOS_PRECIO": "1"})
        despues = b.json()
        afirma(c == 0 and avs == [], ("7a: salieron avisos con SIN_AVISOS_PRECIO=1", asuntos(avs)), b)
        afirma(K_COS not in despues["avisado"] and K_COS not in despues.get("avisado_nivel", {}), despues["avisado"], b)
        for k in ("conocidos", "agotados", "ausente"):
            afirma(despues.get(k) == antes.get(k), ("7a: se toco %s" % k, antes.get(k), despues.get(k)), b)
        afirma("se habria avisado" in sal, "7a: no dijo lo que habria avisado", b)
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(asuntos(avs) == ["GANGA $19,000 · Fold8 512 GB · Costco",
                                "Volvio a la venta: Fold8 512 GB en Costco a $19,000 · mira el aviso de precio de esta corrida"],
               ("7b", asuntos(avs)), b)
        return ("con SIN_AVISOS_PRECIO=1 y una baja a $19,000 de un agotado: 0 avisos, avisado y "
                "conocidos/agotados/ausente intactos; sin la variable: GANGA + 'Volvio a la venta'")
    finally:
        b.borrar()


def e8_aviso_que_falla(v):
    b = Banco(v)
    try:
        b.correr(mundo_base())
        b.fallar = 1
        c, avs, srv, sal = b.correr(costco_a(19000.0))
        afirma(c == 0 and avs == [] and b.fallar == 0 and "no se pudo abrir el aviso" in sal, ("8a", avs), b)
        est = b.json()
        afirma(K_COS not in est["avisado"] and b.json(b.precios)[K_COS]["precio"] == 19000.0, est["avisado"], b)
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(asuntos(avs) == ["GANGA $19,000 · Fold8 512 GB · Costco"], ("8b", asuntos(avs)), b)
        return "abrir_issue falla una vez: la corrida termina (codigo 0), guarda estado y precios; el aviso sale en la siguiente"
    finally:
        b.borrar()


def e9_ficha_basura(v):
    casos = [("JSON roto", {"respuesta": (200, '{"name": "Samsung Galaxy Z Fold 8 512GB", "price": {')}),
             ("precio como texto raro", {"crudo_base": "veintiseis mil (aprox)"}),
             ("HTML de mantenimiento con 200", {"respuesta": (200, "<html><h1>Sitio en mantenimiento</h1></html>")})]
    hechos = []
    for nombre, ficha in casos:
        b = Banco(v)
        try:
            m = costco_a(19000.0, **ficha)
            m[ELEKTRA_PS5] = elektra_ps5()
            c, avs, srv, _ = b.correr(m)
            a = sorted(asuntos(avs))
            afirma(c == 0 and a == ["GANGA $19,000 · Fold8 512 GB · Costco · ficha sin releer",
                                    "GANGA $7,400 · PS5 Digital (sin lector) · Elektra"], ("9 %s" % nombre, a), b)
            afirma(any("Confirmado" in cu for x, cu in avs if "Elektra" in x), ("9 %s: Elektra sin confirmar" % nombre), b)
            hechos.append(nombre)
        finally:
            b.borrar()
    # y si la basura llega en el listado (no en la ficha): Costco cae como fuente, Elektra avisa igual
    b = Banco(v)
    try:
        m = con(mundo_base(), cos=(200, "<html>Sitio en mantenimiento</html>"))
        m[ELEKTRA_PS5] = elektra_ps5()
        c, avs, srv, _ = b.correr(m)
        afirma(c == 0 and asuntos(avs) == ["GANGA $7,400 · PS5 Digital (sin lector) · Elektra"], ("9 listado", asuntos(avs)), b)
        afirma(b.json()["fallos_fuente"]["Costco"] == 1, b.json()["fallos_fuente"], b)
    finally:
        b.borrar()
    return "ficha de Costco con %s: la corrida sigue, Costco sale 'ficha sin releer' y Elektra confirmado; listado basura: Costco cae, Elektra avisa" % (
        ", ".join(hechos))


def e10_estado_viejo(v):
    # copia congelada del visto.json de la version d295f51 (nunca el vivo del repositorio: la nube lo reescribe cada
    # 15 min y sus contadores de ausencia hacian salir un "Agotado" que esta escena no espera)
    with open(os.path.join(RESP, "visto_viejo_2026-11-25.json"), encoding="utf-8-sig") as f:
        viejo = json.load(f)
    b = Banco(v, visto=viejo)
    try:
        c, avs, srv, _ = b.correr(mundo_base())
        agot = set(viejo.get("agotados", {}))
        a = asuntos(avs)
        afirma(c == 0, c, b)
        afirma(all(x.startswith("Volvio a la venta") for x in a), ("10a: avisos que no son regreso", a), b)
        claves = sorted(k for k in agot if k in b.json(b.precios))
        afirma(len(a) == len(claves), ("10a: regresos", a, claves), b)
        est = b.json()
        afirma(all(isinstance(x, (int, float)) for x in est["avisado"].values()), est["avisado"], b)
        c, avs, srv, _ = b.correr(mundo_base())
        afirma(avs == [], ("10b: repitio", asuntos(avs)), b)
        primera = a
    finally:
        b.borrar()
    # 10c: la misma copia con un avisado escrito por la primera version de confirmar ({"precio", "nivel"})
    viejo2 = copy.deepcopy(viejo)
    viejo2["avisado"] = {K_COS: {"precio": 19000.0, "nivel": "GANGA"}}
    b = Banco(v, visto=viejo2)
    try:
        c, avs, srv, _ = b.correr(costco_a(19000.0))
        afirma(not any("Costco" in x and not x.startswith("Volvio") for x in asuntos(avs)), ("10c: reaviso", asuntos(avs)), b)
        est = b.json()
        afirma(est["avisado"] == {K_COS: 19000.0} and est["avisado_nivel"] == {K_COS: ["GANGA", 19000.0]},
               (est["avisado"], est.get("avisado_nivel")), b)
    finally:
        b.borrar()
    return ("visto.json real (copia): termina, solo %d aviso(s) de regreso legitimos (%s), 0 en la siguiente; "
            "avisado en numeros; avisado viejo {precio, nivel}: no reavisa y queda 19000.0" % (len(primera), "; ".join(primera)))


def e11_seco(v):
    b = Banco(v)
    try:
        b.correr(mundo_base())
        foto = {}
        for n in sorted(os.listdir(b.dir)):
            with open(os.path.join(b.dir, n), "rb") as f:
                foto[n] = f.read()
        c, avs, srv, sal = b.correr(costco_a(19000.0), args=["--seco"])
        despues = {}
        for n in sorted(os.listdir(b.dir)):
            with open(os.path.join(b.dir, n), "rb") as f:
                despues[n] = f.read()
        afirma(c == 0 and avs == [] and despues == foto, ("11: --seco escribio o aviso", asuntos(avs),
                                                           [n for n in despues if despues[n] != foto.get(n)]), b)
        afirma(" * AVISO GANGA $19,000" in sal and len(srv.pedidas) > 20, "11: --seco no leyo o no imprimio el aviso", b)
        return "con una GANGA a la vista: imprime el aviso, %d peticiones, 0 issues, ningun archivo cambia" % len(srv.pedidas)
    finally:
        b.borrar()


def e12_lectores_apagados(v):
    """Lectores nuevos apagados al arrancar porque su prueba falla (enganchar_lectores_nuevos con una prueba que
    falla, como en casos_lectores_nuevos.py): sus tiendas salen como "lector no cargado", sin "Agotado" de sus
    articulos, la tabla conserva su ultimo precio y la corrida termina."""
    nuevas = [t for t in NUEVAS if t in v.LECTORES]
    if not nuevas:
        return "sin lectores_nuevos.py cargado: nada que apagar (escena saltada)"
    b = Banco(v)   # se arma con los lectores encendidos: la copia de productos.json conserva las tres tiendas
    dicts = [d for d in (v.LECTORES, v.VENDEDOR_OFICIAL, getattr(v, "FICHAS", None)) if isinstance(d, dict)]
    copias, error, candado = [dict(d) for d in dicts], v.LECTORES_NUEVOS_ERROR, list(v._ENGANCHANDO)
    try:
        b.correr(mundo_base())
        tabla0 = b.json(b.precios)
        suyas = sorted(k for k in b.json()["conocidos"] if k.split("|")[0] in nuevas)
        afirma(suyas, ("12: los lectores nuevos no dieron ningun articulo en la corrida de partida", tabla0), b)
        for d in dicts:
            for t in NUEVAS:
                d.pop(t, None)
        del v._ENGANCHANDO[:]

        def falla(m):
            raise AssertionError("prueba de lectores nuevos que falla a proposito (escena 12)")
        with contextlib.redirect_stdout(io.StringIO()) as sal:
            v.enganchar_lectores_nuevos(prueba=falla)
        afirma("lectores nuevos apagados" in sal.getvalue() and not any(t in v.LECTORES for t in NUEVAS),
               ("12: el enganche no apago", sal.getvalue()), b)
        for i in range(v.AUSENCIAS_AGOTADO + 1):
            c, avs, srv, salida = b.correr(mundo_base())
            afirma(c == 0 and avs == [], ("12: avisos con los lectores apagados", i, asuntos(avs)), b)
            est, tabla = b.json(), b.json(b.precios)
            for t in nuevas:
                afirma(est["ultimo_error"].get(t, "").startswith("lector no cargado: sus pruebas fallan"),
                       ("12: %s no salio como lector no cargado" % t, est["ultimo_error"].get(t)), b)
                afirma(est["fallos_fuente"][t] == i + 1, (t, est["fallos_fuente"]), b)
            for k in suyas:
                afirma(k not in est.get("agotados", {}) and k not in est.get("ausente", {}), ("12: ausencia", k), b)
                afirma(tabla.get(k) == tabla0.get(k), ("12: la tabla perdio", k), b)
            afirma(not any(re.search(r"soriana|cyberpuerta|chedraui", u) for u in srv.pedidas), "12: pidio a una apagada", b)
        return ("lectores nuevos apagados (prueba que falla): %d corridas sin avisos, %s como 'lector no cargado', "
                "sin 'Agotado' ni ausencias de sus %d articulos y con su precio en la tabla" % (
                    v.AUSENCIAS_AGOTADO + 1, ", ".join(nuevas), len(suyas)))
    finally:
        for d, cp in zip(dicts, copias):
            d.clear()
            d.update(cp)
        v.LECTORES_NUEVOS_ERROR = error
        v._ENGANCHANDO[:] = candado
        b.borrar()


def e13_servidor_lento(v):
    """Cada peticion tarda 40 s (reloj falso): la corrida termina y guarda; ninguna peticion empieza pasado el segundo
    SEGUNDOS_DUROS; lo no alcanzado no suma ausencias ni da "Agotado"; la siguiente, normal, lee todo.
    13a: todo lento (el foro y los cupones se comen el tiempo). 13b: solo las tiendas lentas (se leen algunas)."""
    if not hasattr(v, "SEGUNDOS_DUROS"):
        return "sin tope duro en vigia.py (escena saltada)"
    hechos = []
    for nombre, lento_si in (("13a todo lento", None), ("13b tiendas lentas", r"samsung|amazon|costco|elektra|liverpool|"
                                                        r"gameplanet|soriana|cyberpuerta|chedraui")):
        b = Banco(v)
        try:
            b.correr(mundo_base())
            antes = b.json()
            con_avisos = len(b.issues)
            with open(b.precios, encoding="utf-8") as f:
                tabla0 = json.load(f)
            m0 = os.stat(b.estado).st_mtime_ns
            c, avs, srv, sal = b.correr(mundo_base(), lento=40, lento_si=lento_si)
            est = b.json()
            tarde = [(round(t), u) for t, u, _ in srv.inicios if t >= v.SEGUNDOS_DUROS]
            afirma(c == 0 and not tarde, ("%s: peticiones empezadas pasado el tope" % nombre, tarde[:3]), b)
            afirma(os.stat(b.estado).st_mtime_ns != m0 and est != antes, ("%s: no guardo visto.json" % nombre), b)
            afirma(os.path.exists(b.precios), nombre, b)
            afirma(not any(a.startswith("Agotado") for a in asuntos(avs)), ("%s: Agotado" % nombre, asuntos(avs)), b)
            nuevas = {k: n for k, n in est.get("ausente", {}).items() if n > antes.get("ausente", {}).get(k, 0)}
            afirma(not nuevas, ("%s: ausencias por tiendas no alcanzadas" % nombre, nuevas), b)
            afirma(min(t for _, _, t in srv.inicios) >= 5, ("%s: timeout menor a 5 s" % nombre), b)
            tabla = b.json(b.precios)
            perdidas = sorted(set(tabla0) - set(tabla))
            c, avs2, srv2, _ = b.correr(mundo_base())
            est2 = b.json()
            afirma(c == 0 and not any(est2["fallos_fuente"].get(t) for t in ["Foro", "Cupones"] + b.tiendas),
                   ("%s: la corrida siguiente no leyo todo" % nombre, est2["fallos_fuente"]), b)
            afirma(set(b.json(b.precios)) == set(tabla0), ("%s: la tabla no volvio" % nombre), b)
            hechos.append("%s: %d peticiones, la ultima empezo en el s %d, 0 despues del %d; claves perdidas de la tabla: %s" % (
                nombre, len(srv.inicios), max(t for t, _, _ in srv.inicios), v.SEGUNDOS_DUROS, perdidas or "ninguna"))
        finally:
            b.borrar()
    return "; ".join(hechos) + "; despues, corrida normal: todo leido"


ESCENAS = [("1 dia normal", e1_dia_normal), ("2 baja real confirmada", e2_baja_confirmada),
           ("3 el listado miente", e3_listado_miente), ("4 ficha caida", e4_ficha_caida),
           ("5 tienda caida", e5_tienda_caida), ("6 agotado y regreso", e6_agotado_regreso),
           ("7 pruebas fallidas", e7_pruebas_fallidas), ("8 aviso que falla", e8_aviso_que_falla),
           ("9 ficha basura", e9_ficha_basura), ("10 estado viejo", e10_estado_viejo), ("11 modo seco", e11_seco),
           ("12 lectores apagados", e12_lectores_apagados), ("13 servidor lento", e13_servidor_lento)]


def foto_repo():
    salida = {}
    for n in ("visto.json", "precios.json", "historial_precios.csv", "historial.csv", "productos.json"):
        r = os.path.join(RAIZ, n)
        salida[n] = os.stat(r).st_mtime_ns if os.path.exists(r) else None
    return salida


def correr(v):
    del PENDIENTES[:]
    antes = foto_repo()
    t0 = time.perf_counter()
    # topes por fracciones de productos.json, como en selftest(): prospectiva.json (linea A) cambia con el historial
    # y vence con la fecha; se vacia mientras corren las escenas y se repone al salir
    prosp = getattr(v, "PROSPECTIVA", None)
    prosp_antes = dict(prosp) if isinstance(prosp, dict) else None
    try:
        if prosp_antes is not None:
            prosp.clear()
        for nombre, fn in ESCENAS:
            detalle = fn(v)
            print("casos_corrida escena %-24s OK: %s" % (nombre, detalle))
    finally:
        if prosp_antes is not None:
            prosp.clear()
            prosp.update(prosp_antes)
    afirma(foto_repo() == antes, ("se escribio en la carpeta del repositorio", antes, foto_repo()))
    for esc, esperado, paso in PENDIENTES:
        print("casos_corrida PENDIENTE (falla del programa) escena %s: se esperaba %s; paso %s" % (esc, esperado, paso))
    print("casos_corrida OK: %d escenas en %.2f s, %d pendientes" % (len(ESCENAS), time.perf_counter() - t0, len(PENDIENTES)))


if __name__ == "__main__":
    sys.path.insert(0, RAIZ)
    import vigia
    correr(vigia)
