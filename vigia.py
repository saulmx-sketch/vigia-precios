"""Vigia de precios de primera fuente ("un Keepa propio, de varias tiendas").

Que hace, cada 15 minutos, sin tokens y sin la computadora de Saul:
  1. Lee directamente las tiendas configuradas por producto en productos.json (Samsung con prueba de cupones
     en un carrito anonimo, Amazon, Costco, Elektra, Liverpool, Gameplanet).
  2. Aplica los requisitos del producto (quien vende, importados si o no, cargo de importacion estimado).
  3. Compara contra los topes de cada variante y abre un aviso (issue de GitHub = correo) si baja de su tope.
  4. Guarda lo leido en precios.json e historial.csv (el historial de precios propio).
  5. De respaldo, lee los feeds del foro Promodescuentos para las tiendas que rechazan lectores automaticos.

Solo biblioteca estandar.  Prueba local:  python vigia.py --seco      Autoprueba:  python vigia.py --selftest
"""
import csv
import datetime as dt
import email.utils
import gzip
import html
import http.client
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:   # cupon de ficha de Amazon, bancos y tiendas cerradas de Samsung; si falta o falla, todo sigue como antes
    import promos
except Exception:
    promos = None

# 25 nov 2026: apagado. El cupon de ficha de Amazon (linea B, promos.amazon_precio) solo se restaba cuando la ficha se
# releia; con la memoria vendedor_amazon el precio volvia al de sin cupon, oscilaba y el mismo GANGA se repetia
# (revision independiente R7, hallazgo H2). En False el precio de Amazon es el de la ficha a secas (lectura y relectura)
# y el cupon queda solo como nota. Se vuelve a prender cuando la memoria guarde el precio ya con cupon.
RESTAR_CUPON_FICHA_AMAZON = False
NOTA_CUPON_SIN_RESTAR = "con cupon en la ficha, sin restar"


def nota_cupon_amazon(nota, ficha=""):
    """Nota del cupon de Amazon cuando no se resta: la busqueda o la ficha lo anuncian -> NOTA_CUPON_SIN_RESTAR."""
    if nota or 'class="couponLabelText"' in (ficha or ""):
        return NOTA_CUPON_SIN_RESTAR
    return nota

AQUI = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(AQUI, "visto.json")
PRECIOS = os.path.join(AQUI, "precios.json")
HISTORIAL = os.path.join(AQUI, "historial_precios.csv")
# Otro archivo de productos con el mismo esquema: VIGIA_CONFIG=<ruta>. Con ella solo corren --seco y --cobertura
# (main se niega a lo demas: un archivo de muestra no escribe estado ni avisa).
CONFIG_PRINCIPAL = os.path.join(AQUI, "productos.json")
CONFIG = os.environ.get("VIGIA_CONFIG") or CONFIG_PRINCIPAL


def es_principal(ruta):
    """Si `ruta` es productos.json, escrita como sea (relativa, otras mayusculas en Windows; revisor R4-H4)."""
    return os.path.normcase(os.path.abspath(ruta)) == os.path.normcase(os.path.abspath(CONFIG_PRINCIPAL))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
MX = dt.timezone(dt.timedelta(hours=-6))

BAJA_MINIMA = 300        # una tienda ya avisada vuelve a avisar solo si baja al menos esto
DIAS_FRESCO = 3          # ofertas del foro mas viejas que esto no se avisan
# Horas sin una sola lectura buena antes de avisar que una fuente esta caida. Amazon rechaza a ratos las
# peticiones que salen de la nube de GitHub (medido el 20 nov 2026: 3 de cada 4 corridas, con tramos de hasta
# 3 horas); por eso a Amazon se le da mas margen y un rechazo suelto no abre aviso.
HORAS_SIN_LEER = {"Amazon": 6, "Gameplanet": 2}
HORAS_SIN_LEER_DEFECTO = 2
# Nunca interesan: accesorios, usados, importados declarados en el titulo.
EXCLUIR = (r"\b(funda|mica|protector|case|carcasa|cargador|reacondicionad[oa]|seminuevo|usado|refurbished|open\s*box|renewed|"
           r"importad[oa]|versi[oó]n\s+(internacional|global|china|usa)|international|global\s+version|"
           # equipo no nuevo o que no es el equipo (revisor R3-H4 y H5, 25 nov 2026)
           r"caja\s+abierta|exhibici[oó]n|empaque\s+da[nñ]ado|preventa|garant[ií]a\s+extendida)\b")
# Quien cuenta como vendedor oficial en cada tienda cuando el producto pide "solo_vendedor_oficial".
# {marca} se cambia por la marca del producto. Tienda que no aparece aqui = NO oficial (desde el 25 nov 2026, revisor
# R1-F3: una tienda nueva sin su regla dejaba pasar a cualquier revendedor); toda tienda que vende ella misma va aqui.
VENDEDOR_OFICIAL = {
    "Samsung": r"^\s*samsung\s*$",                                        # leer_samsung escribe "Samsung" fijo
    "Amazon": r"^\s*(amazon(\s+m[eé]xico)?|{marca}(\s+m[eé]xico)?)\s*$",   # no terceros ni "Remates de Almacen"
    "Elektra": r"^\s*elektra\s*$",                                        # no vendedores del marketplace
    "Gameplanet": r"^\s*game\s*planet\s*$",                               # vendedor leido de la ficha (ld+json)
    # Liverpool: la ficha no escribe "Liverpool" como vendedor; lo que dice es si el articulo es de su marketplace
    # (isMarketPlace, hasHybridSeller y las ofertas de terceros con su sellerName). ficha_liverpool pone "Liverpool"
    # solo cuando la ficha dice que no es de marketplace ni tiene ofertas de terceros (medido el 25 nov 2026).
    "Liverpool": r"^\s*liverpool\s*$",
    "Costco": r"^\s*costco\b",                                            # su API marca isExternalVendorProduct
}
SENAL_IMPORTADO = r"importaci[oó]n|desde Estados Unidos|Amazon Estados Unidos|Amazon Global|Env[ií]o internacional"
# En el foro solo se atienden tiendas establecidas en Mexico (no importadores ni revendedores).
TIENDAS_FORO = (r"samsung|walmart|bodega|sam'?s|amazon|mercado\s*libre|liverpool|suburbia|coppel|costco|elektra|sears|"
                r"sanborns|palacio|telcel|at&t|office\s*depot|best\s*buy|claro\s*shop|chedraui|soriana|heb|doto|huawei|"
                r"lenovo|apple|xiaomi|motorola|sony|lg\b|hp\b|dell")
BANCOS_PROPIOS = {"BBVA": r"bbva|bancomer", "Revolut": r"revolut", "PayPal": r"paypal"}
BANCOS_AJENOS = {"Banorte": r"banorte", "Santander": r"santander", "Banamex": r"banamex|citibanamex",
                 "HSBC": r"hsbc", "Scotiabank": r"scotiabank", "Amex": r"\bamex\b|american\s+express",
                 "Inbursa": r"inbursa", "Afirme": r"afirme", "BanCoppel": r"bancoppel",
                 "Mercado Pago": r"mercado\s*pago", "Nu": r"\bnu\b", "Hey Banco": r"hey\s*banco"}
SAMSUNG_API = "https://p1-smb-api-cdn.shop.samsung.com/tokocommercewebservices/v2/mx"
# Cupones: el vigia los sale a buscar en cada corrida (Saul, 25 nov 2026: "que tenga proactividad en buscar cupones";
# hasta ese dia la cosecha automatica habia juntado cero codigos, ver INCIDENTES.md). Fuentes medidas ese dia:
# las paginas de Samsung publican "cupon BF1000" / "20% cupon ECO_20" en sus banners, y el foro guarda en los datos de
# cada hilo el campo voucherCode con la tienda (linkHost) y la fecha (publishedAt).
PAGINAS_CUPON_SAMSUNG = ["https://www.samsung.com/mx/offer/", "https://www.samsung.com/mx/offer/black-friday/",
                         "https://www.samsung.com/mx/smartphones/all-smartphones/",
                         "https://www.samsung.com/mx/apps/samsung-shop-app/",
                         # medidas el 25 nov 2026: dan BF1000 y BFLIVESEM (este, hasta ese dia, solo llegaba por el foro)
                         "https://www.samsung.com/mx/offer/deals/", "https://www.samsung.com/mx/offer/samsung-live/",
                         # medida el 25 nov 2026 con vigia.pedir: 200, 678 KB, da BF1000, ECO_25, ECO_20, PRO-15
                         "https://www.samsung.com/mx/offer/cyber"]
BUSQUEDAS_CUPON_FORO = ["samsung", "samsung cupon", "samsung codigo"]   # + "cupones_foro" de cada producto
DIAS_CUPON = 21          # codigo publicado o visto hace mas de esto: se olvida
HORAS_REPROBADO = 3      # codigo que no bajo ningun precio del producto: se vuelve a probar pasadas estas horas
HORAS_REPROBADO_CUPONERA = 12   # el que solo publica una cuponera y no bajo nada descansa mas: son muchos (41 el 25 nov
#                                 2026) y casi todos de otros productos; probarlos cada 3 h duplicaba el tiempo del carrito
TOPE_CUPONES = 30        # codigos probados por corrida y producto (los recien hallados primero). Era 40; con 40 la
#                          corrida de ensayo del 25 nov 2026 tardo 243 s (101 s con 24). Lo que no cabe entra en la corrida
#                          siguiente, porque lo ya probado sin exito descansa.
SEGUNDOS_COSECHA = 45    # linea B (E11): tope de las paginas de Samsung y busquedas del foro en cosechar_cupones
MARGEN_COSECHA = 15      # una peticion de cosecha solo arranca antes de los 30 s: con su timeout de 30 s, peor caso 60 s
SEGUNDOS_CARRITO = 90   # tope de tiempo de prueba de cupones por modelo (el trabajo de GitHub dura maximo 12 min)
# Codigos de tienda cerrada (empleados o estudiantes: llevan "EPP"). En el carrito normal se rechazaron 24 de 24 el
# 25 nov 2026 (8 codigos x 3 modelos). Se siguen cosechando (quedan en visto.json), pero no se prueban en el carrito.
# Expresion acotada: "EPP" como pieza del codigo (CART5_EPP, EPP10_NOV, EPPLIVESEM), no dentro de una palabra (PEPPER10).
MARCA_TIENDA_CERRADA = re.compile(r"(^|_)EPP|EPP($|_|\d)")
NO_ES_CUPON = re.compile(r"^(SAMSUNG|GALAXY|ULTRA|FOLD\d?|FLIP\d?|MSI|IVA|MXN|BBVA|PAYPAL|SANTANDER|BANORTE|"
                         r"CUPON|CODIGO|ASESOR|EPROMOTER|DESCUENTO|GRATIS|ENVIO)$")


# ------------------------------------------------------------------------------- utilidades
# Tope duro de la corrida: pasado esto desde el arranque de main() ya no empieza ninguna peticion ni reintento, para
# que siempre alcance a guardar visto.json y precios.json antes de los 12 min del trabajo de GitHub (una tienda o el
# foro colgados la hacian morir sin guardar). Fuera de main() (_ARRANQUE sin fijar) nada cambia.
SEGUNDOS_DUROS = 600
_ARRANQUE = [None]


def reloj():
    """Reloj de los topes de tiempo (sustituible en pruebas)."""
    return time.monotonic()


def tiempo_de_peticion(tope=30):
    """Timeout de la siguiente peticion: `tope`, recortado a lo que quede del tope duro (minimo 5 s); pasado el tope
    duro, RuntimeError. Fuera de main(), `tope` sin mas."""
    if _ARRANQUE[0] is None:
        return tope
    queda = SEGUNDOS_DUROS - (reloj() - _ARRANQUE[0])
    if queda <= 0:
        raise RuntimeError("se acabo el tiempo de la corrida")
    return max(5, min(tope, queda))


def pedir(url, datos=None, metodo=None, abridor=None, json_api=False, pausa=0.0, reintento=2.0, intentos=2):
    """(estado, texto). Un corte de red o un tope de tiempo se reintenta una vez tras `reintento` segundos; si vuelve
    a fallar, RuntimeError("sin respuesta: <host>: <causa>"). Una respuesta HTTP con error no lanza: da su codigo."""
    import socket
    cab = {"User-Agent": UA, "Accept-Language": "es-MX,es;q=0.9,en;q=0.6", "Accept-Encoding": "gzip",
           "Accept": "application/json, text/plain, */*" if json_api else
                     "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
    cuerpo = None
    if json_api:
        cab["Origin"] = "https://shop.samsung.com"
        cab["Referer"] = "https://shop.samsung.com/"
    if datos is not None:
        cuerpo = json.dumps(datos).encode("utf-8")
        cab["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=cuerpo, headers=cab, method=metodo)
    abre = abridor.open if abridor else urllib.request.urlopen
    if pausa:
        tiempo_de_peticion()   # pasado el tope duro no se espera la pausa: se lanza ya
        time.sleep(pausa)
    ultimo = 1 if intentos == 1 else 2   # linea B: intentos=1 abre una sola vez (C4: un cuelgue costaba 60 s, no 30)
    for intento in range(1, ultimo + 1):
        espera = tiempo_de_peticion(30)   # pasado el tope duro: no se empieza (ni el reintento)
        try:
            with abre(req, timeout=espera) as r:
                b = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    b = gzip.decompress(b)
                return r.status, decodificar(b, r.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as e:   # va antes: HTTPError tambien es URLError
            b = e.read() or b""
            try:
                b = gzip.decompress(b)
            except Exception:
                pass
            return e.code, decodificar(b, e.headers.get("Content-Type", "") if e.headers else "")
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if intento == ultimo:
                causa = getattr(e, "reason", None) or e
                raise RuntimeError("sin respuesta: %s: %s" % (urllib.parse.urlsplit(url).hostname, causa))
            if reintento:
                tiempo_de_peticion()
                time.sleep(reintento)


def decodificar(b, tipo=""):
    """Texto de una respuesta. Samsung sirve sus paginas sin decir la codificacion y no en UTF-8: leidas como UTF-8,
    'cupon' con acento salia roto y la busqueda de codigos nunca casaba (medido el 25 nov 2026)."""
    m = re.search(r"charset=([\w-]+)", tipo or "", re.I)
    if m:
        try:
            return b.decode(m.group(1), "replace")
        except LookupError:
            pass
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", "replace")


def cargar_config(ruta=None):
    """Productos de `ruta`; sin ruta, siempre productos.json (lo que esperan las pruebas aunque VIGIA_CONFIG este
    puesta). main() y cobertura() piden CONFIG, que si sigue a VIGIA_CONFIG."""
    ruta = ruta or CONFIG_PRINCIPAL
    nombre = os.path.basename(ruta)
    with open(ruta, encoding="utf-8") as f:
        prods = json.load(f)["productos"]
    for p in prods:  # validacion: un producto mal escrito aborta con mensaje claro, no vigila a medias
        for campo in ("id", "nombre", "patron", "piso", "fracciones", "variantes", "tiendas", "requisitos"):
            if campo not in p:
                raise SystemExit("%s: al producto %s le falta '%s'" % (nombre, p.get("id", "?"), campo))
        for v in p["variantes"]:
            if "id" not in v or "nombre" not in v or "lista" not in v:
                raise SystemExit("%s: variante incompleta en %s: %s" % (nombre, p["id"], v))
        try:
            for rx in [p["patron"], p.get("excluir") or ""] + [x for v in p["variantes"] for x in v.get("todas", []) + v.get("ninguna", [])]:
                re.compile(rx)
            fa = p.get("ficha_atributos")
            if fa is not None and not isinstance(fa, dict):
                raise SystemExit("%s: en %s, ficha_atributos debe ser un objeto {tienda: [reglas]}" % (nombre, p["id"]))
            for tienda, reglas in (fa or {}).items():
                if not isinstance(reglas, list):
                    raise SystemExit("%s: en %s, ficha_atributos.%s debe ser una lista de reglas" % (nombre, p["id"], tienda))
                for r in reglas:
                    # una regla sin "si" casaria con todo titulo de la tienda (revisor R4-H3); sin "agrega" no hace nada
                    if not isinstance(r, dict) or not isinstance(r.get("si"), dict) or not r["si"] or not r.get("agrega"):
                        raise SystemExit("%s: en %s, cada regla de ficha_atributos.%s necesita \"si\" (atributos que deben "
                                         "casar, no vacio) y \"agrega\": %s" % (nombre, p["id"], tienda, r))
                    for rx in list((r.get("si") or {}).values()) + [r.get("no_si") or "", r.get("no_si_atributos") or ""]:
                        re.compile(rx)
        except re.error as ex:
            raise SystemExit("%s: regla mal escrita en %s: %s" % (nombre, p["id"], ex))
    return prods


def variante(prod, titulo):
    """La variante del producto que casa con el titulo, o None si no interesa."""
    if not re.search(prod["patron"], titulo, re.I) or re.search(EXCLUIR, titulo, re.I):
        return None
    if prod.get("excluir") and re.search(prod["excluir"], titulo, re.I):
        return None
    for v in prod["variantes"]:
        if all(re.search(x, titulo, re.I) for x in v.get("todas", [])) and \
           not any(re.search(x, titulo, re.I) for x in v.get("ninguna", [])):
            return v
    return None


def var_por_id(prod, vid):
    return next(v for v in prod["variantes"] if v["id"] == vid)


def cargar_prospectiva():
    """Topes y cifras de referencia que prospectiva.py calcula con el historial de precios (prospectiva.json).
    Devuelve {} si el modulo o el archivo faltan, el archivo vencio o no pasa su validacion: entonces topes()
    usa las fracciones de productos.json, como siempre. Nada de aqui puede tumbar al vigia."""
    try:
        sys.dont_write_bytecode = True   # sin __pycache__: el paso que guarda el estado en la nube hace "git add -A"
        import prospectiva
        return prospectiva.cargar_json() or {}
    except Exception as ex:
        print("   prospectiva no disponible ({}): topes por fracciones".format(ex), file=sys.stderr)
        return {}


PROSPECTIVA = cargar_prospectiva()   # clave "producto|variante"; vacio = topes por fracciones


def prospectiva_de(prod, var):
    d = PROSPECTIVA.get("%s|%s" % (prod["id"], var["id"]))
    return d if isinstance(d, dict) else {}


def topes(prod, var):
    fijos = prod.get("topes_fijos", {}).get(var["id"])
    if fijos:                                    # una cifra que Saul dio a mano gana siempre
        return tuple(fijos)
    calculados = prospectiva_de(prod, var).get("topes")
    if calculados:                               # ya validados por prospectiva.cargar_json()
        return tuple(calculados)
    return tuple(int(round(var["lista"] * f / 100.0)) * 100 for f in prod["fracciones"])


def nivel(precio, prod, var):
    g, c, t = topes(prod, var)
    bajo = prospectiva_de(prod, var).get("sin_confirmar_bajo")
    # muy por debajo de todo lo visto con garantia: se avisa, pero no como GANGA (el umbral nunca alcanza a la ganga)
    if type(bajo) in (int, float) and 0 < bajo < g and precio < bajo:
        return "SIN CONFIRMAR"
    return "GANGA" if precio <= g else "COMPRAR" if precio <= c else "MIRAR" if precio <= t else None


def razon(precio, prod, var, hoy=None):
    """Una o dos frases en lego de por que ese precio es bueno o no (minimo visto con garantia, lo normal del
    mes, lo que se espera en la siguiente temporada). Cadena vacia si no hay datos; nunca lanza excepcion."""
    try:
        import prospectiva
        return prospectiva.razon(precio, prospectiva_de(prod, var), hoy) or ""
    except Exception:
        return ""


def marca_ok(prod, titulo):
    """En Amazon el titulo debe nombrar la marca (deja fuera accesorios de otras marcas). Un producto cuyo titulo
    no suele traer la marca (PS5: "Consola PlayStation 5" sin "Sony") la da en marca_titulo."""
    regla = prod.get("marca_titulo", prod.get("marca"))
    return not regla or bool(re.search(regla, titulo, re.I))


def vendedor_oficial(prod, tienda, vendedor):
    # el producto puede fijar su propia regla por tienda (PS5: en Amazon solo Amazon Mexico; Sony no vende directo)
    regla = prod["requisitos"].get("vendedor_oficial", {}).get(tienda) or VENDEDOR_OFICIAL.get(tienda)
    if not regla:
        return False   # tienda sin regla: no se sabe quien cuenta como oficial, asi que nadie
    return bool(re.search(regla.replace("{marca}", re.escape(prod.get("marca", "zzzz"))), vendedor or "", re.I))


def oferta(prod, tienda, titulo, precio, url, vendedor="", nota="", importado=False, var=None):
    """Arma la oferta ya filtrada por los requisitos del producto; None si no cuenta."""
    var = var or variante(prod, titulo)
    req = prod["requisitos"]
    if not var or precio is None or precio < prod["piso"]:
        return None
    oficial = vendedor_oficial(prod, tienda, vendedor)
    if req.get("solo_vendedor_oficial") and not oficial:
        return None
    if importado and not req.get("permite_importados"):
        return None
    paga = float(precio)
    if importado:  # el cargo real solo se ve en el carrito o al pagar: aqui va estimado y se dice
        est = float(req.get("importacion_estimada", 0.2))
        paga = round(paga * (1 + est), 2)
        nota = "; ".join(x for x in [nota, "importado: ficha ${:,.0f} + cargo de importacion estimado {:.0f} % (sin leer)".format(precio, est * 100)] if x)
    elif not oficial:
        nota = "; ".join(x for x in [nota, "vendedor tercero"] if x)
    return {"prod": prod["id"], "var": var["id"], "nombre": var["nombre"], "tienda": tienda, "precio": paga,
            "url": url, "vendedor": vendedor, "nota": nota, "titulo": titulo.strip()[:140]}


# ------------------------------------------------------------------------------- tiendas
def codigos_de(texto):
    """Codigos sueltos de un campo de cupones ('BFLIVESEM + CRP26TV + CARRITO5', 'A | B o C', 'Cupon del Asesor')."""
    salida = []
    for pieza in re.split(r"[+|/,;]|\s+(?:o|y|u|or)\s+|\s+-\s+|\s{2,}", texto or ""):
        pieza = pieza.strip(" .:'\"“”()")
        if (re.fullmatch(r"[A-Z0-9][A-Z0-9_\-]{3,21}", pieza) and re.search(r"[A-Z]", pieza)
                and not NO_ES_CUPON.match(pieza) and pieza not in salida):
            salida.append(pieza)
    return salida


def cupones_en_texto(plano):
    """Codigos anunciados en una pagina: 'con el cupon BF1000', '20% cupon ECO_20', 'usa el codigo X'."""
    salida = []
    for m in re.finditer(r"(?i:cup[oó]n(?:es)?|c[oó]digo)\s*(?:(?i:de\s+descuento)\s*)?[:\"“]?\s*"
                         r"([A-Z0-9][A-Z0-9_\-]{3,21})\b", plano):
        for c in codigos_de(m.group(1)):
            if c not in salida:
                salida.append(c)
    # /offer/cyber/ los escribe de otra forma: "Cupon 20%: PRO-20", "10% con HS26_ECO" (medido el 25 nov 2026);
    # la segunda exige guion o guion bajo para no tomar "10% con BBVA"
    for m in re.finditer(r"(?i:cup[oó]n)\s*\d{1,2}\s*%\s*:\s*([A-Z0-9][A-Z0-9_\-]{3,21})\b|"
                         r"\d{1,2}\s*%\s+con\s+([A-Z][A-Z0-9]*[_\-][A-Z0-9_\-]{1,18})\b", plano):
        for c in codigos_de(m.group(1) or m.group(2)):
            if c not in salida:
                salida.append(c)
    return salida


HILO_FORO = re.compile(r'"publishedAt":(\d+)[^{}]{0,400}?"voucherCode":"((?:[^"\\]|\\.)*)","linkHost":"([^"]*)"')


def cosechar_cupones(prods, estado, ahora):
    """Sale a buscar codigos de Samsung en cada corrida: paginas de ofertas y de compra de Samsung, y los datos
    del foro. Lo hallado queda en estado['cupones_cosechados'] = {codigo: dia en que se vio por ultima vez}."""
    cosecha = estado.get("cupones_cosechados")
    if not isinstance(cosecha, dict):          # formato anterior: lista
        cosecha = {c: ahora.astimezone(MX).strftime("%Y-%m-%d") for c in (cosecha or [])}
    paginas, busquedas = list(PAGINAS_CUPON_SAMSUNG), list(BUSQUEDAS_CUPON_FORO)
    for p in prods:
        sam = p["tiendas"].get("Samsung")
        if not sam:
            continue
        for info in (sam.get("skus") or {}).values():   # 26 nov (pendiente 7): Samsung sin skus no tumba la cosecha
            if len(info) > 2 and info[2] not in paginas:
                paginas.append(info[2])
        for b in sam.get("cupones_foro", [p["nombre"]]):
            if b not in busquedas:
                busquedas.append(b)
    hallados, leidas = {}, 0
    # linea B (E11, simulacro C4): con samsung.com o el foro colgados la cosecha tardaba 930 s y dejaba sin leer todas
    # las tiendas. Ahora: un solo intento por peticion, tope total, y tras el primer cuelgue de un host no se le pide mas.
    t0, caidos = time.monotonic(), set()

    def pide(u):
        host = urllib.parse.urlsplit(u).hostname
        if host in caidos:
            raise RuntimeError("host caido en esta corrida: %s" % host)
        if time.monotonic() - t0 + MARGEN_COSECHA >= SEGUNDOS_COSECHA:
            raise RuntimeError("sin tiempo de cosecha")
        try:
            return pedir(u, reintento=0, intentos=1)
        except Exception:
            caidos.add(host)
            raise

    for u in paginas:
        try:
            e, t = pide(u)
        except Exception:
            continue
        if e != 200:
            continue
        leidas += 1
        plano = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))
        for c in cupones_en_texto(plano) + cupones_en_texto(html.unescape(t)):
            hallados.setdefault(c, "Samsung")
    for b in busquedas:
        try:
            e, t = pide("https://www.promodescuentos.com/search?q=" + urllib.parse.quote(b) + "&sortBy=new")
        except Exception:
            continue
        if e != 200:
            continue
        leidas += 1
        for pub, crudo, host in HILO_FORO.findall(t):
            if "samsung" not in host.lower() or ahora.timestamp() - int(pub) > DIAS_CUPON * 86400:
                continue
            try:
                crudo = json.loads('"%s"' % crudo)
            except Exception:
                pass
            for c in codigos_de(crudo):
                hallados.setdefault(c, "foro")
    if promos:
        try:   # cuponeras medidas el 25 nov 2026 (promos.FUENTES_EXTRA); si fallan enteras, la cosecha queda como antes
            # cada 2 h; entre lecturas no pide nada. dormir busca time.sleep al llamar (junta-2): el default de promos lo
            # ato al importarse y la corrida simulada de C dormia de verdad 200 s
            extra = promos.cosechar_extra_si_toca(pedir, estado, ahora, dormir=lambda s: time.sleep(s))
            for crudo in extra.get("Samsung") or {}:
                for c in codigos_de(crudo):
                    hallados.setdefault(c, "cuponera")
            leidas += promos.ULTIMA_COSECHA.get("leidas", 0)
            promos.guardar_codigos(estado, extra, ahora)   # Amazon, Liverpool, Costco: solo como dato
            print("   cuponeras: %s" % promos.ULTIMA_COSECHA.get("resumen", ""))
        except Exception as ex:
            print("   cuponeras sin leer (%s)" % str(ex)[:100])
    if not leidas:
        raise RuntimeError("ninguna fuente de cupones respondio")
    hoy = ahora.astimezone(MX).strftime("%Y-%m-%d")
    for c in hallados:
        cosecha[c] = hoy
    limite = (ahora.astimezone(MX) - dt.timedelta(days=DIAS_CUPON)).strftime("%Y-%m-%d")
    estado["cupones_cosechados"] = {c: f for c, f in cosecha.items() if f >= limite}
    # origen duradero de cada codigo (Samsung, foro o cuponera): lo de Samsung o del foro no se rebaja a cuponera
    origen = estado.get("cupones_origen") if isinstance(estado.get("cupones_origen"), dict) else {}
    for c, o in hallados.items():
        if not (o == "cuponera" and origen.get(c) in ("Samsung", "foro")):
            origen[c] = o
    estado["cupones_origen"] = {c: o for c, o in origen.items() if c in estado["cupones_cosechados"]}
    estado["cupones_ultima_busqueda"] = {"cuando": ahora.astimezone(MX).strftime("%Y-%m-%d %H:%M"),
                                         "fuentes_leidas": leidas, "hallados": hallados}
    return sorted(hallados)


def cupones_candidatos(prod, estado):
    """Primero la lista fija, luego lo recien hallado en Samsung y en el foro y al final lo que solo publica una
    cuponera (origen duradero en estado['cupones_origen']; la ultima busqueda, de respaldo); fuera los reprobados de las
    ultimas horas. Asi el tope nunca deja fuera a la lista fija."""
    lista = []
    try:
        with open(os.path.join(AQUI, prod["tiendas"]["Samsung"].get("cupones", "cupones_samsung.txt")), encoding="utf-8") as f:
            lista = [l.split("#")[0].strip() for l in f if l.split("#")[0].strip()]
    except Exception:
        pass
    cosecha = estado.get("cupones_cosechados") or {}
    if not isinstance(cosecha, dict):
        cosecha = {c: "" for c in cosecha}
    reprobados = estado.get("cupones_reprobados", {})
    ultima = (estado.get("cupones_ultima_busqueda") or {}).get("hallados") or {}
    ultima = ultima if isinstance(ultima, dict) else {}
    duradero = estado.get("cupones_origen") if isinstance(estado.get("cupones_origen"), dict) else {}
    frescos = sorted(cosecha, key=lambda k: cosecha[k], reverse=True)
    de_cuponera = [c for c in frescos if (duradero.get(c) or ultima.get(c)) == "cuponera" and c not in lista]
    salida = []
    # orden: lista fija primero, luego lo fresco de Samsung y del foro, al final lo de cuponera
    for c in lista + [c for c in frescos if c not in de_cuponera] + de_cuponera:
        horas = HORAS_REPROBADO_CUPONERA if c in de_cuponera else HORAS_REPROBADO
        if c in salida or MARCA_TIENDA_CERRADA.search(c.upper()) or time.time() - reprobados.get("%s|%s" % (prod["id"], c), 0) < horas * 3600:
            continue
        salida.append(c)
    return salida[:TOPE_CUPONES]


def samsung_carrito(sku, cupones):
    """Arma un carrito anonimo con el SKU, prueba cada cupon y se queda con los que bajan el total."""
    jar = http.cookiejar.CookieJar()
    ab = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    e, t = pedir("https://shop.samsung.com/mx/ng/p4v1/addCart?productCode=%s&quantity=1" % sku, abridor=ab, json_api=True)
    if e != 200 or "SUCCESS" not in t:
        raise RuntimeError("no se pudo agregar al carrito: %s %s" % (e, t[:80]))
    campos = "totalPriceWithTax(value),appliedVouchers(code,voucherCode),appliedProductPromotions(promotion(code,endDate))"
    url_cart = SAMSUNG_API + "/users/current/carts/current?fields=" + campos + "&lang=es_MX&curr=MXN"
    url_vou = SAMSUNG_API + "/users/current/carts/current/vouchers"

    def total():
        e2, t2 = pedir(url_cart, abridor=ab, json_api=True, pausa=0.25)
        d = json.loads(t2)
        puestos = [v.get("voucherCode") or v.get("code") for v in d.get("appliedVouchers", []) or []]
        return float(d["totalPriceWithTax"]["value"]), puestos, d

    def poner(cup):
        e2, t2 = pedir(url_vou + "?lang=es_MX&curr=MXN", {"voucherId": cup, "voucherPopup": False}, abridor=ab,
                       json_api=True, pausa=0.25)
        return e2 == 200 and ('"errors" : [ ]' in t2 or '"errors":[]' in t2)

    def quitar(cup):   # la orden real de la pagina (capturada el 2026-11-19); DELETE .../vouchers/<codigo> da 404
        pedir(SAMSUNG_API + "/users/current/carts/current/removeVoucher?lang=es_MX&curr=MXN", {"voucherId": cup},
              abridor=ab, json_api=True, pausa=0.25)

    def dejar_solo(conjunto):
        """Deja en el carrito exactamente ese conjunto de cupones (un cupon puede desplazar a otro al entrar)."""
        _, puestos, _ = total()
        for c in puestos:
            if c not in conjunto:
                quitar(c)
        _, puestos, _ = total()
        for c in conjunto:
            if c not in puestos:
                poner(c)
        return total()

    base, _, d = total()
    # Pasada 1: cada cupon solo. Asi el resultado no depende del orden de la lista. Un codigo muerto cuesta una
    # sola peticion (el carrito lo rechaza al ponerlo); hay tope de tiempo para no pasar el limite del trabajo.
    solos, probados, inicio = {}, [], time.time()
    for cup in cupones:
        if time.time() - inicio > SEGUNDOS_CARRITO:
            break
        probados.append(cup)
        if not poner(cup):
            continue
        t1, puestos, d = total()
        if cup in puestos and t1 < base - 1:
            solos[cup] = t1
        for c in puestos:
            quitar(c)
    # Pasada 2: se parte del mejor y se le suman los demas solo si bajan el total.
    mejor_total, mejor, fin_d = base, [], d
    for cup in sorted(solos, key=solos.get):
        if time.time() - inicio > SEGUNDOS_CARRITO * 2:
            break
        prueba = mejor + [cup]
        t2, puestos, d2 = dejar_solo(prueba)
        if all(c in puestos for c in prueba) and t2 < mejor_total - 1:
            mejor_total, mejor, fin_d = t2, prueba, d2
    final, puestos, d = dejar_solo(mejor)
    if abs(final - mejor_total) > 1:   # el carrito no quedo como se midio: se reporta lo que de verdad hay
        mejor_total, mejor = final, puestos
    fin = ""
    for p in d.get("appliedProductPromotions", []) or []:
        fin = (p.get("promotion", {}).get("endDate") or "")[:10] or fin
    return base, mejor_total, mejor, fin, probados, list(solos)


def leer_samsung(prod, cfg, estado):
    skus = cfg["skus"]
    e, t = pedir("https://searchapi.samsung.com/v6/front/b2c/product/card/detail/newhybris?siteCode=mx&modelList="
                 + ",".join(skus) + "&commonCodeYN=N&saleSkuYN=N&onlyRequestSkuYN=Y&keySummaryYN=N&shopSiteCode=mx")
    if e != 200:
        raise RuntimeError("catalogo de Samsung respondio %s" % e)
    venta = {}
    for item in json.loads(t)["response"]["resultData"]["productList"]:
        for m in item.get("modelList", []):
            sku, precio = m.get("modelCode"), m.get("promotionPrice") or m.get("price")
            if sku in skus and precio and m.get("ctaType") in ("inStock", "preOrder", "lowStock"):
                vid, color, pagina = skus[sku]
                if vid not in venta or float(precio) < venta[vid][1]:
                    venta[vid] = (sku, float(precio), color, m.get("ctaType"), pagina)
    # Los codigos los sale a buscar cosechar_cupones (fuente "Cupones", antes de las tiendas, en cada corrida).
    salida, cupones = [], cupones_candidatos(prod, estado)
    probados, sirven = set(), set()
    for vid, (sku, precio, color, cta, pagina) in venta.items():
        nota = "preventa" if cta == "preOrder" else ""
        buenos, fin = [], ""   # si el carrito falla, la oferta no hereda los cupones del modelo anterior
        try:
            base, final, buenos, fin, prob, solos = samsung_carrito(sku, cupones)
            probados.update(prob)
            sirven.update(solos)
            nota = "; ".join(x for x in [
                ("con cupones " + " + ".join(buenos)) if buenos else
                "ninguno de %d cupones probados baja el precio" % len(prob),
                "sin cupon ${:,.0f}".format(base) if buenos else "",
                ("promocion vigente hasta " + fin) if fin else "", nota] if x)
            precio = final
        except Exception as ex:
            print("   Samsung: carrito de %s sin probar: %s" % (sku, str(ex)[:120]))
            nota = "precio de pagina, cupones sin probar"
        var = var_por_id(prod, vid)
        of = oferta(prod, "Samsung", "%s %s" % (var["nombre"], color), precio, pagina, "Samsung", nota, var=var)
        if of:
            of["cupones"], of["vigencia"] = list(buenos), fin or ""   # en el orden en que quedaron aplicados
            salida.append(of)
    # Lo que no bajo el precio de ningun modelo descansa HORAS_REPROBADO; lo que si sirvio se sigue probando siempre.
    reprobados = estado.setdefault("cupones_reprobados", {})
    for c in probados:
        clave = "%s|%s" % (prod["id"], c)
        if c in sirven:
            reprobados.pop(clave, None)
        else:
            reprobados[clave] = int(time.time())
    for clave in [k for k, v in reprobados.items() if time.time() - v > 7 * 86400]:
        del reprobados[clave]
    if not salida:
        raise RuntimeError("Samsung no listo ningun modelo a la venta")
    return salida


def amazon_rechazo(e, t, senal):
    """'' si la pagina trae datos (contiene la senal); si no, el tipo de rechazo, para diagnosticar.
    Medido el 20 nov 2026 desde GitHub: 503 de ~1.7 KB (pagina de robots) y 200 de ~2.3 KB (pagina corta sin datos);
    desde casa, con el mismo programa, la misma peticion da 200 con la pagina completa."""
    if e == 200 and senal in t:
        return ""
    return "%s de %d bytes%s" % (e, len(t), " con captcha" if "captcha" in t.lower() else "")


def ficha_amazon(t):
    """Con el texto de una ficha: (titulo, precio a pagar o None, vendedor, importado), como siempre.
    Con una liga o un ASIN: pide la ficha y devuelve el diccionario de ficha (ver confirmar); RuntimeError si Amazon
    la rechaza (desde la nube de GitHub pasa 3 de cada 4 veces)."""
    if re.match(r"(https?://\S+|[A-Z0-9]{10})$", t or ""):
        url = t if t.startswith("http") else "https://www.amazon.com.mx/dp/%s?th=1&psc=1" % t
        e, p = pedir(url, pausa=1.0)
        r = amazon_rechazo(e, p, 'id="productTitle"')
        if r:
            raise RuntimeError("Amazon respondio %s en la ficha" % r)
        titulo, precio, vend, imp = _ficha_amazon(p)
        if RESTAR_CUPON_FICHA_AMAZON and promos and precio is not None:   # linea B: el mismo cupon que resta _leer_amazon
            try:
                f2, _ = promos.amazon_precio(precio, promos.amazon_ficha(p))
                if f2 is not None and precio * 0.5 <= f2 <= precio:
                    precio = f2
            except Exception:
                pass
        agotado =re.search(r"No disponible por el momento|Actualmente no disponible", p)
        return {"titulo": titulo, "precio": precio, "vendedor": vend, "importado": imp, "url": url,
                "existencia": True if precio is not None else False if agotado else None}
    return _ficha_amazon(t)


def _ficha_amazon(t):
    """Titulo, precio a pagar (None si esta agotado o sin precio), vendedor e importado, leidos de la ficha."""
    tit = re.search(r'id="productTitle"[^>]*>(.*?)</span>', t, re.S)
    titulo = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tit.group(1)))).strip() if tit else ""
    pr = re.search(r'apex-pricetopay-value.{0,800}?a-price-whole">([\d,]+).{0,200}?a-price-fraction">(\d\d)', t, re.S)
    precio = float(pr.group(1).replace(",", "") + "." + pr.group(2)) if pr else None
    v = re.search(r"Vendido por:?\s*(?:<[^>]*>\s*)*([^<]{2,60})<", t) or \
        re.search(r'offer-display-feature-text-message">\s*([^<]{2,60})<', t)
    vend = html.unescape(v.group(1)).strip() if v else ""
    caja = re.sub(r"<[^>]+>", " ", t[t.find('id="desktop_buybox"'):][:60000]) if 'id="desktop_buybox"' in t else ""
    return titulo, precio, vend, bool(re.search(SENAL_IMPORTADO, caja, re.I))


def leer_amazon(prod, cfg, estado):
    """Busqueda de Amazon; si Amazon la rechaza, segunda via: las fichas ya conocidas del producto, una por una.
    Cada corrida deja en visto.json (diag_amazon) que pagina respondio y como, para medir el rechazo en el tiempo."""
    diag = []
    try:
        return _leer_amazon(prod, cfg, estado, diag)
    finally:
        d = estado.setdefault("diag_amazon", [])
        d.append("%s %s" % (dt.datetime.now(MX).strftime("%m-%d %H:%M"), "; ".join(diag)))
        del d[:-60]


# Lo recordado de una ficha de Amazon caduca (pendiente H8, 25 nov 2026): con la misma cifra de busqueda se reusaba
# el precio de ficha para siempre y una baja que solo se ve en la ficha nunca se avisaba. Pasados estos minutos se
# relee la ficha aunque la busqueda no cambie, a lo mas TOPE_RELEER_AMAZON por producto y corrida (sin rafagas).
# La hora va como sexto elemento de vendedor_amazon: la version publicada solo lee los cinco primeros.
MINUTOS_MEMO_AMAZON = 60
TOPE_RELEER_AMAZON = 3


def _leer_amazon(prod, cfg, estado, diag):
    memo = estado.setdefault("vendedor_amazon", {})
    ahora_ts = int(dt.datetime.now(dt.timezone.utc).timestamp())
    releidas = 0
    solo_oficial = prod["requisitos"].get("solo_vendedor_oficial")
    candidatas, visto, fallo = [], set(), ""
    for q in cfg["consultas"]:
        url = "https://www.amazon.com.mx/s?k=" + q
        e, t = pedir(url, pausa=1.0)
        r = amazon_rechazo(e, t, "s-search-result")
        if r:   # un reintento tras una pausa por si el rechazo es de un momento
            e, t = pedir(url, pausa=25.0)
            r2 = amazon_rechazo(e, t, "s-search-result")
            diag.append("busqueda %s, reintento %s" % (r, r2 or "ok"))
            r = r2
        else:
            diag.append("busqueda ok")
        if r:
            fallo = "Amazon respondio %s en la busqueda" % r
            break
        marcas = list(re.finditer(r'data-asin="([A-Z0-9]{10})"[^>]{0,400}data-component-type="s-search-result"', t))
        for i, m in enumerate(marcas):
            bloque = t[m.end():marcas[i + 1].start() if i + 1 < len(marcas) else m.end() + 20000]
            tit = re.search(r"<h2[^>]*>(.*?)</h2>", bloque, re.S)
            pr = re.search(r'class="a-offscreen">\$([\d,]+\.\d\d)<', bloque)
            if not tit or not pr or m.group(1) in visto:
                continue
            titulo = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tit.group(1)))).strip()
            precio = float(pr.group(1).replace(",", ""))
            if not marca_ok(prod, titulo):
                continue
            if variante(prod, titulo) and precio >= prod["piso"]:
                visto.add(m.group(1))
                nota = "con cupon en la ficha" if re.search(r"cup[oó]n", bloque, re.I) else ""
                promo_b = None
                if promos and nota:
                    try:   # cupon del bloque de busqueda (promos.py); se aplica abajo sobre el precio de la ficha
                        promo_b = promos.amazon_busqueda(bloque)
                    except Exception:
                        promo_b = None
                candidatas.append((m.group(1), titulo, precio, nota, promo_b))
    salida = []
    if not fallo:
        # Quien vende y si viene de fuera solo se ve en la ficha. Se recuerda por articulo y precio de busqueda (eso
        # alimenta la tabla, nunca basta para avisar: lo que va a avisar pasa por confirmar()).
        for asin, titulo, precio, nota, promo_b in candidatas:
            url = "https://www.amazon.com.mx/dp/%s?th=1&psc=1" % asin
            previo = memo.get(asin)
            paga, leida = precio, False
            # quinto elemento: el precio que dio la ficha (la version d6ab147 solo lee los cuatro primeros). Lo
            # recordado sin el (escrito antes del 25 nov) se trata como no recordado: se relee la ficha una vez.
            igual = bool(previo and previo[0] == precio and len(previo) > 4 and previo[4])
            fresco = igual and len(previo) > 5 and isinstance(previo[5], (int, float)) and \
                ahora_ts - previo[5] < MINUTOS_MEMO_AMAZON * 60
            if igual and (fresco or releidas >= TOPE_RELEER_AMAZON):
                vend, importado, paga = previo[1], bool(previo[2]), previo[4]
            else:
                if igual:
                    releidas += 1   # recordado vencido: se relee aunque la busqueda no cambio
                e, t = pedir(url, pausa=1.0)
                r = amazon_rechazo(e, t, 'id="productTitle"')
                diag.append("ficha %s %s" % (asin, r or "ok"))
                _, pf, vend, importado = _ficha_amazon(t)
                if not r:
                    if pf is None:
                        continue   # la ficha leida no tiene precio a pagar: agotado o sin oferta propia
                    # La busqueda y la ficha pueden mostrar ofertas distintas (otro vendedor, usado): manda la ficha,
                    # que es la del vendedor leido.
                    paga, leida = pf, True
                elif previo and previo[0] == precio:
                    # ficha rechazada (pasa en la nube) y lo recordado es viejo, sin precio de ficha: como antes, el
                    # vendedor recordado con el precio de busqueda, para no vaciar la tabla; no se reescribe la memoria
                    vend, importado = previo[1], bool(previo[2]) if len(previo) > 2 else False
                    if len(previo) > 4 and previo[4]:   # recordado vencido y ficha rechazada: queda el precio de ficha
                        paga = previo[4]               # recordado y se reintenta en la corrida siguiente
                    of = oferta(prod, "Amazon", titulo, paga, url, vend, nota, importado=importado)
                    if of:
                        salida.append(of)
                    continue
            if vend:
                if leida or not igual:
                    memo[asin] = [precio, vend, importado, prod["id"], paga, ahora_ts]   # precio sin cupon y su hora
            final = paga
            if not RESTAR_CUPON_FICHA_AMAZON:
                nota = nota_cupon_amazon(nota, t if leida else "")
            elif promos and (nota or leida):
                try:   # promos.py: cupon de ficha (de la ficha leida ahora o, si no se leyo, del bloque de busqueda)
                    promo = promos.amazon_ficha(t) if leida else promo_b
                    if promo:
                        f2, n = promos.amazon_precio(paga, promo)
                        if f2 is not None and paga * 0.5 <= f2 <= paga:
                            final, nota = f2, (n or nota)
                except Exception:
                    final = paga
            of = oferta(prod, "Amazon", titulo, final, url, vend or "sin confirmar", nota, importado=importado)
            if of and final < paga:
                of["cupones"] = []   # el cupon de ficha se recorta en la pagina: no es un codigo que se teclee
            if of and (vend or not solo_oficial):
                salida.append(of)
        return salida
    # Segunda via: el buscador rechazo; se lee directo la ficha de cada articulo ya conocido de este producto.
    # Todo o nada: si una ficha tambien se rechaza, la fuente falla y la tabla conserva el ultimo dato de Amazon.
    conocidas = [a for a, v in memo.items() if len(v) < 4 or v[3] == prod["id"]]
    if not conocidas:
        raise RuntimeError(fallo)
    for asin in conocidas:
        url = "https://www.amazon.com.mx/dp/%s?th=1&psc=1" % asin
        e, t = pedir(url, pausa=2.0)
        r = amazon_rechazo(e, t, 'id="productTitle"')
        diag.append("ficha %s %s" % (asin, r or "ok"))
        if r:
            raise RuntimeError("%s y %s en la ficha %s" % (fallo, r, asin))
        titulo, precio, vend, importado = _ficha_amazon(t)
        if precio is None or not marca_ok(prod, titulo):
            continue   # agotado o sin precio a la vista: hoy no se ofrece
        if vend:
            memo[asin] = [precio, vend, importado, prod["id"], precio, ahora_ts]
        nota, final = "", precio
        if not RESTAR_CUPON_FICHA_AMAZON:
            nota = nota_cupon_amazon("", t)
        elif promos:
            try:   # promos.py: cupon de ficha que no pide nada (Prime, banco, app); si falla, como antes
                final, nota = promos.amazon_precio(precio, promos.amazon_ficha(t))
                final = final if final is not None and precio * 0.5 <= final <= precio else precio
            except Exception:
                nota, final = "", precio
        of = oferta(prod, "Amazon", titulo, final, url, vend or "sin confirmar", nota, importado=importado)
        if of and final < precio:
            of["cupones"] = []   # el cupon de ficha se recorta en la pagina: no es un codigo que se teclee
        if of and (vend or not solo_oficial):
            salida.append(of)
    return salida


MESES = "ene feb mar abr may jun jul ago sep oct nov dic".split()


def hasta_mx(iso):
    """'2026-11-30T05:59:59.999Z' -> '29 nov 23:59' (hora de Mexico); '' si no se entiende."""
    try:
        d = dt.datetime.fromisoformat((iso or "").replace("Z", "+00:00")).astimezone(MX)
    except ValueError:
        return ""
    return "%d %s %s" % (d.day, MESES[d.month - 1], d.strftime("%H:%M"))


COSTCO_API = "https://www.costco.com.mx/rest/v2/mexico/products/"


def _costco(p):
    """Ficha de un producto de la API de Costco (busqueda o producto). Costco marca en cada producto si lo vende un
    tercero (isExternalVendorProduct); en las busquedas del 25 nov 2026 de Fold8 y PS5 (40 productos) ninguno lo era."""
    base = (p.get("basePrice") or p.get("price") or {}).get("value")
    cup = p.get("couponDiscount") or {}
    precio = base - (cup.get("discountValue") or 0) if base is not None else None
    externo = p.get("isExternalVendorProduct") or p.get("isExternalVendorVariantProduct")
    return {"titulo": p.get("name", ""), "precio": precio, "importado": False,
            "vendedor": "vendedor externo en Costco" if externo else "Costco (pide membresia)",
            "existencia": (p.get("stock") or {}).get("stockLevelStatus") != "outOfStock" if p.get("stock") else None,
            "url": "https://www.costco.com.mx" + p.get("url", ""),
            "nota": "descuento de tienda ${:,.0f} ya restado".format(cup["discountValue"]) if cup.get("discountValue") else "",
            "vigencia": hasta_mx(cup.get("discountEndDate")) if cup.get("discountValue") else ""}


def ficha_costco(url_o_id):
    m = re.search(r"(\d{4,})\D*$", str(url_o_id))
    if not m:
        raise RuntimeError("liga de Costco sin numero de articulo")
    e, t = pedir(COSTCO_API + m.group(1) + "?fields=FULL&lang=es_MX&curr=MXN", pausa=1.0)
    if e != 200:
        raise RuntimeError("Costco respondio %s en la ficha %s" % (e, m.group(1)))
    return _costco(json.loads(t))


def leer_costco(prod, cfg, estado):
    e, t = pedir(COSTCO_API + "search?query=%s&fields=FULL&lang=es_MX&curr=MXN" % urllib.parse.quote(cfg["consulta"]))
    if e != 200:
        raise RuntimeError("Costco respondio %s" % e)
    salida = []
    for p in json.loads(t).get("products", []):
        f = _costco(p)
        if f["precio"] is None or f["existencia"] is False:
            continue
        of = oferta(prod, "Costco", f["titulo"], f["precio"], f["url"], f["vendedor"], f["nota"])
        if of:
            if f["vigencia"]:
                of["vigencia"] = f["vigencia"]
            salida.append(of)
    return salida


ELEKTRA_API = "https://www.elektra.mx/api/catalog_system/pub/products/search"


def ficha_elektra(url_o_id):
    """La ficha de Elektra es su API por numero de articulo: cada vendedor con su precio y existencia (sellerName)."""
    m = re.search(r"(\d{6,})(?:/p)?/?$", str(url_o_id))
    if not m:
        raise RuntimeError("liga de Elektra sin numero de articulo")
    e, t = pedir(ELEKTRA_API + "?fq=productId:" + m.group(1), pausa=1.0)
    if e not in (200, 206):
        raise RuntimeError("Elektra respondio %s en la ficha %s" % (e, m.group(1)))
    d = json.loads(t)
    if not d:
        raise RuntimeError("Elektra no devolvio el articulo %s" % m.group(1))
    p, opciones = d[0], []
    for it in p.get("items", []):
        for s in it.get("sellers", []):
            co = s.get("commertialOffer", {})
            opciones.append({"titulo": it.get("nameComplete") or p.get("productName", ""), "precio": co.get("Price") or None,
                             "vendedor": s.get("sellerName", ""), "existencia": co.get("AvailableQuantity", 0) > 0,
                             "importado": False, "url": p.get("link", ""), "defecto": bool(s.get("sellerDefault"))})
    if not opciones:
        raise RuntimeError("la ficha %s de Elektra no trae vendedores" % m.group(1))
    top = next((o for o in opciones if o["existencia"] and o["defecto"]), None) or \
        next((o for o in opciones if o["existencia"]), opciones[0])
    return dict(top, opciones=opciones)


def leer_elektra(prod, cfg, estado):
    e, t = pedir(ELEKTRA_API + "?ft=%s&_from=0&_to=49" % urllib.parse.quote(cfg["consulta"]))
    if e not in (200, 206):
        raise RuntimeError("Elektra respondio %s" % e)
    salida = []
    for p in json.loads(t):
        for it in p.get("items", []):
            for s in it.get("sellers", []):
                co = s.get("commertialOffer", {})
                if co.get("AvailableQuantity", 0) > 0 and co.get("Price"):
                    of = oferta(prod, "Elektra", p.get("productName", ""), co["Price"], p.get("link", ""), s.get("sellerName", ""))
                    if of:
                        salida.append(of)
    return salida


LIVERPOOL = "https://www.liverpool.com.mx/tienda"
LIVERPOOL_FICHAS = 8      # fichas por producto y corrida, las mas baratas primero (1 s de pausa entre una y otra)


def next_plano(t):
    """Los datos que Next.js manda dentro de la pagina (self.__next_f.push), ya como texto: ahi viene la ficha."""
    salida = []
    for p in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', t):
        try:
            salida.append(json.loads('"%s"' % p))
        except ValueError:
            pass
    return "".join(salida)


def objetos_con(plano, clave, valor, intentos=400):
    """Objetos JSON del texto que traen clave=valor en su primer nivel (se busca hacia atras la llave que los abre)."""
    dec, salida, vistos = json.JSONDecoder(), [], set()
    for m in re.finditer(r'"%s":"%s"' % (re.escape(clave), re.escape(valor)), plano):
        j = m.start()
        for _ in range(intentos):
            j = plano.rfind("{", 0, j)
            if j < 0 or j in vistos:
                break
            try:
                o, fin = dec.raw_decode(plano, j)
            except ValueError:
                continue
            if fin > m.start() and isinstance(o, dict) and o.get(clave) == valor:
                vistos.add(j)
                salida.append(o)
                break
    return salida


def titulo_con_atributos(prod, tienda, titulo, atributos):
    """Titulo de la ficha completado con sus atributos, segun las reglas del producto en productos.json:
    "ficha_atributos": {tienda: [regla, ...]}, la primera que aplica gana. Regla:
      "si": {nombre de atributo: regex}   todos deben casar (sin mayusculas/minusculas; atributo que falta = no casa)
      "no_si": regex, "no_si_atributos": regex   la regla no aplica si "no_si" casa en el titulo o en el valor de algun
                                                 atributo cuyo nombre case "no_si_atributos"
      "agrega": texto                    se anade al titulo, separado por un espacio
    Producto sin reglas para esa tienda, o lector que no entrega atributos: el titulo queda como lo da la ficha.
    Sirve para tiendas que titulan sin decir la variante y la dicen en la ficha (medido con Liverpool, 25 nov 2026)."""
    reglas = (prod.get("ficha_atributos") or {}).get(tienda) or []
    if not reglas or not atributos:
        return titulo
    for r in reglas:
        if not all(re.search(rx, str(atributos.get(nombre) or ""), re.I) and atributos.get(nombre) not in (None, "")
                   for nombre, rx in (r.get("si") or {}).items()):
            continue
        if r.get("no_si"):
            mirar = [titulo] + [str(v) for k, v in atributos.items()
                                if r.get("no_si_atributos") and re.search(r["no_si_atributos"], k or "", re.I)]
            if re.search(r["no_si"], " ".join(mirar), re.I):
                continue
        return "%s %s" % (titulo, r.get("agrega", "")) if r.get("agrega") else titulo
    return titulo


def ficha_liverpool_texto(t, pid, sku=None):
    """Ficha de Liverpool desde su pagina. Medido el 25 nov 2026 (fichas 1156059630, 1203150349, 1186785807,
    1207352230, 999683571464): el articulo trae title, productAttributes, configurationFlags (isMarketPlace,
    hasHybridSeller, isInternational) y cada variante (skuId) su priceInfo, inventoryStatus, attributes (Size, Color) y
    promotions.offersInfo: {"offersCount": 0} si lo vende Liverpool, o las ofertas de terceros con sellerName y precio.
    La ficha nunca escribe "Liverpool" como vendedor: se pone solo cuando dice que no hay marketplace ni terceros."""
    plano = next_plano(t)
    objs = objetos_con(plano, "productId", pid)
    prin = next((o for o in objs if "variants" in o and o.get("title")), None)
    if not prin:
        raise RuntimeError("la ficha %s de Liverpool no trae los datos del articulo" % pid)
    skus = [o for o in objs if o.get("skuId")] or prin.get("variants") or []
    attrs = {d.get("name"): d.get("value") for g in prin.get("productAttributes") or [] for d in g.get("details") or []}
    opciones = []
    for s in skus:
        flags = s.get("configurationFlags") or prin.get("configurationFlags") or {}
        ofs = (s.get("promotions") or {}).get("offersInfo")
        pi = s.get("priceInfo") or prin.get("priceInfo") or {}
        precio = (pi.get("promoPrice") or {}).get("price") or pi.get("salePrice")
        if flags.get("isMarketPlace") or flags.get("hasHybridSeller") or (ofs or {}).get("offersCount"):
            # de marketplace (o mixto): cuenta la mejor oferta de tercero, con su nombre y su precio
            # Limite conocido (R1-P2): en una ficha mixta se pierde el precio propio de Liverpool (no sale aviso falso).
            best = (ofs or {}).get("bestOffer") or {}
            vend = (best.get("sellerName") or "").strip()
            bp = best.get("priceInfo") or {}
            precio = (bp.get("promoPrice") or {}).get("price") or bp.get("salePrice") or precio
        elif ofs is not None and "offersCount" in ofs:
            vend = "Liverpool"       # la ficha dice: ni marketplace ni ofertas de terceros
        else:
            vend = ""                # no se pudo leer quien vende: no se publica
        a = s.get("attributes") or {}
        color = a.get("Color") or {}
        extra = [(a.get("Size") or {}).get("name"), color.get("clothingBrandColor") or color.get("name")]
        titulo = " ".join([prin["title"]] + [x for x in extra if x and x.lower() not in prin["title"].lower()])
        inv = s.get("inventoryStatus", prin.get("inventoryStatus"))
        # los atributos de la ficha van aparte: quien conoce el producto (leer_liverpool, confirmar) completa el titulo
        # con titulo_con_atributos y las reglas de productos.json
        opciones.append({"titulo": titulo, "atributos": attrs, "precio": float(precio) if precio else None, "vendedor": vend,
                         "existencia": inv if isinstance(inv, bool) else None, "sku": s.get("skuId", ""),
                         "importado": bool(flags.get("isInternational") or flags.get("isCrossBorder")),
                         "url": "%s/pdp/p/%s" % (LIVERPOOL, pid) + ("?skuId=%s" % s["skuId"] if s.get("skuId") else "")})
    if not opciones:
        raise RuntimeError("la ficha %s de Liverpool no trae variantes" % pid)
    vivas = [o for o in opciones if o["existencia"] and o["precio"]]
    top = next((o for o in opciones if sku and o["sku"] == sku), None) or \
        (min(vivas, key=lambda o: o["precio"]) if vivas else opciones[0])
    return dict(top, opciones=opciones)


# Fichas de Liverpool ya leidas en esta corrida (memoria del proceso, no del estado): confirmar() no las vuelve a pedir.
# main() la vacia al arrancar; las pruebas tambien.
LIVERPOOL_LEIDAS = {}
LIVERPOOL_SEGUNDOS = 120  # tope de tiempo para abrir fichas en leer_liverpool (y se para tras 2 fallas seguidas)


def ficha_liverpool(url_o_id):
    m = re.search(r"(\d{6,})(?:\?skuId=(\d+))?\s*$", str(url_o_id))
    if not m:
        raise RuntimeError("liga de Liverpool sin numero de articulo")
    leida = LIVERPOOL_LEIDAS.get(m.group(1))
    if leida:
        ops, sku = leida["opciones"], m.group(2)
        top = next((o for o in ops if sku and o["sku"] == sku), None) or leida
        return dict(top, opciones=ops)
    e, t = pedir("%s/pdp/p/%s" % (LIVERPOOL, m.group(1)), pausa=1.0)
    if e != 200:
        raise RuntimeError("Liverpool respondio %s en la ficha %s" % (e, m.group(1)))
    return ficha_liverpool_texto(t, m.group(1), m.group(2))


def leer_liverpool(prod, cfg, estado):
    """Busqueda (titulo y precio) y, para lo que es el producto y pasa el piso, su ficha: titulo (completado con sus
    atributos segun las reglas "ficha_atributos" del producto), precio de cada variante, existencia y quien vende. Lo que
    no tiene ficha leida o no dice quien vende no se publica."""
    e, t = pedir(LIVERPOOL + "?s=" + cfg["consulta"])
    if e != 200 or "-price" not in t:
        raise RuntimeError("Liverpool respondio %s (%d bytes)" % (e, len(t)))
    cands = {}
    for m in re.finditer(r'<h3[^>]*>([^<]*)</h3><div[^>]*data-testid="(\d+)-price">(.{0,700}?)</div>', t, re.S):
        pr = re.search(r'data-testid="discounted".{0,200}?\$(?:<!-- -->)?([\d,]+)', m.group(3), re.S) or \
             re.search(r"\$(?:<!-- -->)?([\d,]+)", m.group(3))
        if not pr:
            continue
        titulo, pid, precio = html.unescape(m.group(1)), m.group(2), float(pr.group(1).replace(",", ""))
        # candidato a ficha: es el producto (patron), no es accesorio ni usado (EXCLUIR, excluir) y pasa el piso,
        # aunque el titulo del listado no diga la variante: la ficha puede decirla (ver titulo_con_atributos)
        if re.search(prod["patron"], titulo, re.I) and not re.search(EXCLUIR, titulo, re.I) and \
           not (prod.get("excluir") and re.search(prod["excluir"], titulo, re.I)) and precio >= prod["piso"] and \
           (pid not in cands or precio < cands[pid][1]):
            cands[pid] = (titulo, precio)
    elegidos = sorted(cands.items(), key=lambda kv: kv[1][1])[:LIVERPOOL_FICHAS]
    salida, leidas, seguidas, t0 = [], 0, 0, reloj()
    for i, (pid, (titulo, precio)) in enumerate(elegidos):
        if i and (seguidas >= 2 or reloj() - t0 > LIVERPOOL_SEGUNDOS):
            print("   Liverpool: se dejan de abrir fichas (%s); no se publican %d articulos" % (
                "2 fallas seguidas" if seguidas >= 2 else "pasaron %d s" % LIVERPOOL_SEGUNDOS, len(elegidos) - i))
            break
        try:
            fi = ficha_liverpool(pid)
        except Exception as ex:
            seguidas += 1
            print("   Liverpool: no se publica %s (%s, $%s): la ficha no se pudo leer: %s" % (pid, titulo[:40], precio, str(ex)[:90]))
            continue
        seguidas = 0
        LIVERPOOL_LEIDAS[pid] = fi
        leidas += 1
        for op in fi["opciones"]:
            if op["existencia"] is False or not op["precio"]:
                continue
            if not op["vendedor"]:
                print("   Liverpool: no se publica %s: la ficha no dice quien vende" % op["url"])
                continue
            tit = titulo_con_atributos(prod, "Liverpool", op["titulo"], op.get("atributos"))
            of = oferta(prod, "Liverpool", tit, op["precio"], op["url"], op["vendedor"], "", importado=op["importado"])
            if of:
                salida.append(of)
                print("   Liverpool: entra %s $%s (%s, lo vende %s)" % (of["titulo"][:90], "{:,.0f}".format(of["precio"]),
                                                                       of["nombre"], of["vendedor"]))
            elif variante(prod, tit) and not vendedor_oficial(prod, "Liverpool", op["vendedor"]):
                print("   Liverpool: no se publica %s: lo vende %s (tercero de su marketplace)" % (op["url"], op["vendedor"]))
    if elegidos and not leidas:
        raise RuntimeError("Liverpool: ninguna de %d fichas se pudo leer" % len(elegidos))
    return salida


GAMEPLANET = "https://www.gameplanet.com"
GP_EN_VENTA = {"InStock": "", "LimitedAvailability": "pocas piezas", "PreOrder": "preventa"}


def ficha_gameplanet(t):
    """Con el texto de la ficha: (nombre, [(precio, existencia, condicion, vendedor)]), como siempre.
    Con una liga: pide la ficha y devuelve el diccionario de ficha (solo ofertas nuevas; ver confirmar)."""
    if re.match(r"https?://\S+$", t or ""):
        e, p = pedir(t, pausa=1.0)
        if e != 200:
            raise RuntimeError("Gameplanet respondio %s en la ficha" % e)
        nombre, ofs = _ficha_gameplanet(p)
        if not nombre and not ofs:
            raise RuntimeError("la ficha de Gameplanet no trae sus datos")
        opciones = [{"titulo": nombre, "precio": precio, "vendedor": vend, "existencia": existe in GP_EN_VENTA,
                     "importado": False, "url": t} for precio, existe, cond, vend in ofs if cond == "NewCondition"]
        if not opciones:
            return {"titulo": nombre, "precio": None, "vendedor": "", "existencia": False, "importado": False, "url": t}
        return dict(next((o for o in opciones if o["existencia"]), opciones[0]), opciones=opciones)
    return _ficha_gameplanet(t)


def _ficha_gameplanet(t):
    """Nombre y ofertas [(precio, existencia, condicion, vendedor)] del ld+json de una ficha de Gameplanet
    (medido el 24 nov 2026: el catalogo no trae existencia; la ficha si, con el vendedor de cada oferta)."""
    m = re.search(r'<script type="application/ld\+json" data-testid="product-jsonld">(.*?)</script>', t, re.S)
    if not m:
        return "", []
    d = json.loads(m.group(1))
    ofs = d.get("offers") or []
    ofs = ofs if isinstance(ofs, list) else [ofs]
    fin = lambda x: (x or "").rsplit("/", 1)[-1]
    return d.get("name", ""), [(float(o["price"]) if o.get("price") else None, fin(o.get("availability")),
                                fin(o.get("itemCondition")), (o.get("seller") or {}).get("name", "")) for o in ofs]


def leer_gameplanet(prod, cfg, estado):
    """Catalogo de busqueda (titulo y precio) y, para lo que casa con el producto, su ficha (existencia, condicion,
    vendedor). Solo ofertas nuevas y a la venta; el vendedor se lee de la ficha, nunca se supone."""
    e, t = pedir(GAMEPLANET + "/catalogo?q=" + urllib.parse.quote(cfg["consulta"]))
    if e != 200 or ('data-testid="product-card"' not in t and 'data-testid="no-products"' not in t):
        raise RuntimeError("Gameplanet respondio %s (%d bytes)" % (e, len(t)))
    lista = t[t.find('data-testid="products-list"'):] if 'data-testid="products-list"' in t else ""
    salida, vistos = [], set()
    for href, cuerpo in re.findall(r'data-testid="product-card" href="(/products/[^"]+)">(.*?)</a></li>', lista, re.S):
        tit = re.search(r"<h3[^>]*>(.*?)</h3>", cuerpo, re.S)
        pr = re.search(r'data-testid="product-price" data-value="([\d.]+)"', cuerpo)
        titulo = html.unescape(re.sub(r"<[^>]+>", " ", tit.group(1))).strip() if tit else ""
        if href in vistos or not pr or not variante(prod, titulo) or float(pr.group(1)) < prod["piso"]:
            continue
        vistos.add(href)
        e, f = pedir(GAMEPLANET + href, pausa=1.0)
        if e != 200:
            raise RuntimeError("Gameplanet respondio %s en la ficha %s" % (e, href))
        nombre, ofs = ficha_gameplanet(f)
        for precio, existe, cond, vend in ofs:
            if existe in GP_EN_VENTA and cond == "NewCondition":
                of = oferta(prod, "Gameplanet", nombre or titulo, precio, GAMEPLANET + href, vend, GP_EN_VENTA[existe])
                if of:
                    salida.append(of)
    return salida


# Para sumar una tienda: correr sondeo.py, escribir leer_<tienda>(prod, cfg, estado) y registrarla aqui.
LECTORES = {"Samsung": leer_samsung, "Amazon": leer_amazon, "Costco": leer_costco,
            "Elektra": leer_elektra, "Liverpool": leer_liverpool, "Gameplanet": leer_gameplanet}


# ------------------------------------------------------------------------------- releer la ficha antes de avisar
SAMSUNG_CATALOGO = ("https://searchapi.samsung.com/v6/front/b2c/product/card/detail/newhybris?siteCode=mx&modelList=%s"
                    "&commonCodeYN=N&saleSkuYN=N&onlyRequestSkuYN=Y&keySummaryYN=N&shopSiteCode=mx")


def ficha_samsung(skus):
    """La "ficha" de Samsung es su catalogo: precio de pagina y ctaType de cada modelo (uno o varios, separados por coma).
    El total con cupones ya salio de un carrito real en esta corrida; aqui no se vuelve a armar."""
    lista = [s for s in re.split(r"[,\s]+", str(skus)) if s]
    if not lista:
        raise RuntimeError("sin modelo de Samsung que releer")
    e, t = pedir(SAMSUNG_CATALOGO % ",".join(lista), pausa=1.0)
    if e != 200:
        raise RuntimeError("catalogo de Samsung respondio %s" % e)
    opciones = []
    for item in json.loads(t)["response"]["resultData"]["productList"]:
        for m in item.get("modelList", []):
            if m.get("modelCode") in lista:
                precio = m.get("promotionPrice") or m.get("price")
                opciones.append({"titulo": re.sub(r"<[^>]+>", " ", m.get("displayName") or m.get("modelName") or m["modelCode"]).strip(),
                                 "precio": float(precio) if precio else None, "vendedor": "Samsung", "importado": False,
                                 "existencia": m.get("ctaType") in ("inStock", "preOrder", "lowStock"), "sku": m["modelCode"]})
    if not opciones:
        raise RuntimeError("el catalogo de Samsung no devolvio %s" % ",".join(lista))
    return dict(next((o for o in opciones if o["existencia"]), opciones[0]), opciones=opciones)


FICHAS_PROPIAS = {"Amazon": ficha_amazon, "Liverpool": ficha_liverpool, "Costco": ficha_costco, "Elektra": ficha_elektra,
                  "Gameplanet": ficha_gameplanet, "Samsung": ficha_samsung}
# Contrato para lectores que se dan de alta desde fuera (lectores_nuevos.py, registrar(v)): tienda -> funcion que recibe
# la url de la oferta y devuelve {"titulo", "precio", "vendedor", "existencia" (True/False/None), "importado"} o lanza
# RuntimeError. confirmar() la usa para toda tienda sin trato propio, con las mismas tres salidas.
FICHAS = {}
CAMPOS_FICHA = ("titulo", "precio", "vendedor", "existencia", "importado")
TOPE_CONFIRMAR = 6                                   # fichas releidas por corrida (las de menor precio primero)
SEGUNDOS_CONFIRMAR = 540                             # pasado esto desde el arranque, ya no se relee (el aviso sale igual)
NIVELES_CONFIRMAR = ("GANGA", "COMPRAR", "SIN CONFIRMAR")


def ref_ficha(o, prod):
    """Con que se pide la ficha de una oferta: su liga; en Samsung, los modelos de esa variante y color."""
    if o["tienda"] == "Samsung":
        skus = prod["tiendas"].get("Samsung", {}).get("skus", {})
        return ",".join(s for s, (vid, color, _) in skus.items() if vid == o["var"] and color.lower() in o["titulo"].lower())
    return o["url"]


def pesos(x):
    return "sin precio" if x is None else "${:,.0f}".format(x)


def confirmar(o, prod, estado):
    """Relee la ficha de una oferta antes de avisarla. Devuelve (resultado, oferta_final, razon):
    - "confirmada": la ficha sostiene la oferta (misma variante, con existencia, vendedor que cumple, precio con nivel de
      aviso); oferta_final trae el precio, el titulo y el vendedor de la ficha.
    - "no_coincide": la ficha se leyo y no la sostiene; razon lo dice con cifras.
    - "sin_releer": no se pudo leer (rechazo, tope de tiempo, tienda sin ficha); la oferta queda como estaba.
    Las tres salidas avisan: releer nunca se traga un aviso. Cuenta las relecturas de la corrida en estado["_confirmaciones"]."""
    lector = FICHAS_PROPIAS.get(o.get("tienda")) or FICHAS.get(o.get("tienda"))
    if o.get("foro") or not lector:
        return "sin_releer", o, "esta tienda no deja releer la ficha"
    if estado.get("_confirmaciones", 0) >= TOPE_CONFIRMAR:
        return "sin_releer", o, "sin tiempo"
    if estado.get("_arranque") is not None and reloj() - estado["_arranque"] > SEGUNDOS_CONFIRMAR:
        return "sin_releer", o, "sin tiempo"
    try:
        return _confirmar(o, prod, estado, lector)
    except Exception as ex:   # una ficha rara nunca tumba los demas avisos (revisor R1-P1)
        return "sin_releer", o, "error al revisar la ficha: %s" % str(ex)[:120]


def numero(x):
    """Precio de una ficha como numero: 8499, "8,499.00", "$8,499" -> 8499.0; lo que no se entienda -> None."""
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(x))
    return float(m.group(0).replace(",", "")) if m else None


def _confirmar(o, prod, estado, lector):
    ref = ref_ficha(o, prod)
    if not ref:
        return "sin_releer", o, "no se sabe cuál es su ficha"
    estado["_confirmaciones"] = estado.get("_confirmaciones", 0) + 1
    try:
        fi = lector(ref)
    except Exception as ex:
        return "sin_releer", o, "la tienda no la entregó: %s" % str(ex)[:120]
    if not isinstance(fi, dict) or any(k not in fi for k in CAMPOS_FICHA):
        return "sin_releer", o, "la ficha llegó incompleta"
    var = var_por_id(prod, o["var"])
    buenas, motivos, sin_existencia = [], [], []
    for op in fi.get("opciones") or [fi]:
        precio, titulo = numero(op.get("precio")), str(op.get("titulo") or o["titulo"])
        titulo = titulo_con_atributos(prod, o["tienda"], titulo, op.get("atributos"))
        if o["tienda"] == "Samsung":
            # el catalogo no dice color ni capacidad (el modelo los fija) y da el precio sin cupones: el total del
            # carrito de esta corrida manda
            titulo = "%s (%s)" % (o["titulo"], titulo) if titulo != o["titulo"] else titulo
            if precio is not None and precio >= o["precio"]:
                precio = o["precio"]
        if op.get("existencia") is False:
            motivos.append("agotado")
            continue
        if not op.get("vendedor"):
            motivos.append("la ficha no dice quién vende")
            continue
        of = oferta(prod, o["tienda"], titulo, precio, op.get("url") or o["url"], op["vendedor"], o.get("nota", ""),
                    importado=bool(op.get("importado")), var=var if o["tienda"] == "Samsung" else None)
        if not of:
            if o["tienda"] != "Samsung" and not variante(prod, titulo):
                motivos.append('es otro artículo: la ficha lo titula "%s"' % titulo[:90])
            elif precio is None or precio < prod["piso"]:
                motivos.append("precio %s fuera de lo que se vigila" % pesos(precio))
            elif not vendedor_oficial(prod, o["tienda"], op["vendedor"]):
                motivos.append("lo vende %s, no la tienda ni la marca" % op["vendedor"])
            else:
                motivos.append("viene importado")
            continue
        if of["var"] != o["var"]:
            motivos.append("es otra variante (%s)" % of["nombre"])
            continue
        if not nivel(of["precio"], prod, var):
            motivos.append("precio fuera de tus topes")
            continue
        (buenas if op.get("existencia") is True else sin_existencia).append(of)
    if buenas:
        mejor = min(buenas, key=lambda x: x["precio"])
        final = dict(o)
        final.update({k: mejor[k] for k in ("precio", "titulo", "vendedor", "url", "nota")})
        return "confirmada", final, "coincide"
    if sin_existencia:   # todo lo demas cuadra pero la ficha no dice si hay: se avisa con su nivel y la advertencia
        return "sin_releer", o, "la ficha no dice si hay existencia"
    vistos = []
    for m in motivos:
        if m not in vistos:
            vistos.append(m)
    pf = numero(fi.get("precio"))
    razon = "el listado decía %s; la ficha dice %s, vendido por %s" % (
        pesos(o["precio"]), pesos(pf), fi.get("vendedor") or "(sin dato)")
    return "no_coincide", dict(o, precio_ficha=pf), razon + " (" + "; ".join(vistos or ["no la sostiene"]) + ")"


def pasos_compra(o):
    """Pasos numerados para llegar al precio: cupones en orden (o.get("cupones"), los anade la linea B) o, mientras
    no existan, los que dice la nota ("con cupones A + B")."""
    cup = o.get("cupones")
    if cup:
        codigos = [c if isinstance(c, str) else str(c.get("codigo") or c.get("code") or c.get("cupon") or c)
                   if isinstance(c, dict) else str(c) for c in cup]
    else:
        m = re.search(r"con cupones ([^;]+)", o.get("nota") or "")
        codigos = [c.strip() for c in m.group(1).split("+")] if m else []
    pasos = ["Abre la liga de abajo."] + ["Teclea el cupón %s." % c for c in codigos if c]
    pasos.append("Revisa que el total sea {} antes de pagar.".format(pesos(o["precio"])))
    return pasos


def notas_compra(o):
    """Lo que dice la nota y no es un paso: renglones que van debajo de los pasos ("Sin cupones cuesta $N.", "Ojo: ...")."""
    salida, otras = [], []
    for x in (o.get("nota") or "").split(";"):
        x = x.strip()
        if not x or re.match(r"con cupones|promocion vigente hasta", x):
            continue
        m = re.match(r"sin cup[oó]n\s+(\$[\d,]+)", x)
        if m:
            salida.append("Sin cupones cuesta %s." % m.group(1))
        else:
            otras.append(x)
    if otras:
        salida.append("Ojo: " + "; ".join(otras) + ".")
    return salida


def fecha_lego(txt):
    """'2026-12-01' (o con hora) -> '1 dic'; lo que no sea fecha de ese tipo se deja como viene ('29 nov 23:59')."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d:\d\d))?", txt or "")
    if not m:
        return txt
    return "%d %s%s" % (int(m.group(3)), MESES[int(m.group(2)) - 1], " " + m.group(4) if m.group(4) else "")


def vigencia_de(o):
    """Renglon de vigencia en lego: la de la oferta, o la que diga la nota, o 'no se sabe'."""
    if o.get("vigencia"):
        v = fecha_lego(str(o["vigencia"]))
        return v if v.startswith("hasta") else "hasta el " + v
    m = re.search(r"vigente hasta ([\w:-]+)", o.get("nota") or "")
    return "hasta el %s (lo dice la tienda)" % fecha_lego(m.group(1)) if m else "no se sabe"


def razon_a(o, prod, var):
    """Texto de la linea A (funcion global razon, si existe) o ''; nunca tumba el aviso."""
    fn = globals().get("razon")
    if not callable(fn):
        return ""
    try:
        r = fn(o["precio"], prod, var)
        return str(r) if r else ""
    except Exception:
        return ""


def linea_razon(o, prod, var):
    r = razon_a(o, prod, var)
    return ("- Por qué: " + r) if r else ""


def texto_aviso(o, prod, var, niv, resultado, razon_ficha, mejores, hora, niv_listado=None):
    """(asunto, cuerpo) de un aviso de precio, para leerse en el celular: que es, donde y quien vende, cuanto pagas,
    como se llega, hasta cuando, la liga sola, que dijo la ficha, tus topes y la tabla al final."""
    p = pesos(o["precio"])
    if niv == "SIN CONFIRMAR":
        asunto = "SIN CONFIRMAR {} · {} · {} · abre la ficha antes de creerlo".format(p, var["nombre"], o["tienda"])
    elif resultado == "sin_releer":
        asunto = "{} {} · {} · {} · ficha sin releer".format(niv, p, var["nombre"], o["tienda"])
    else:
        asunto = "{} {} · {} · {}".format(niv, p, var["nombre"], o["tienda"])
    if resultado == "confirmada":
        conf = "Confirmado: se releyó la ficha a las %s y coincide." % hora
        if niv == "SIN CONFIRMAR":
            conf += " Aun así el precio es raro: ábrela antes de creerlo."
        elif niv_listado and niv_listado != niv:
            conf += " (El listado lo ponía como %s; con el precio de la ficha es %s.)" % (niv_listado, niv)
    elif resultado == "no_coincide":
        conf = "Se releyó la ficha a las %s y no coincide con el listado: ábrela antes de creerlo." % hora
    elif resultado == "sin_releer":
        conf = "No se pudo releer la ficha (%s): ábrela antes de comprar." % razon_ficha
    else:
        conf = "Aviso de %s: no se releyó la ficha (solo se relee para ganga y comprar)." % niv
    g, c, t = topes(prod, var)
    lineas = []
    if niv == "SIN CONFIRMAR":   # la advertencia va antes de todo
        if resultado == "no_coincide":
            lineas += ["Sin confirmar: %s." % razon_ficha, ""]
        else:
            ra = razon_a(o, prod, var)
            lineas += ["Sin confirmar: el precio está muy por debajo de lo que suele costar%s." % ((": " + ra) if ra else ""), ""]
    lineas += ["**%s**" % o["titulo"], "Variante vigilada: %s" % var["nombre"], "",
               "- Dónde: %s; lo vende %s" % (o["tienda"], o["vendedor"] or "(sin dato)"),
               ("- Precio que se vio: **%s**" if niv == "SIN CONFIRMAR" else "- Pagas: **%s**") % p, "- Cómo se llega:"]
    pasos = pasos_compra(o)
    if resultado == "no_coincide":   # la ficha ya dijo otro precio: no pedirle que busque el del listado
        pasos[-1] = "La ficha dice %s: compara antes de pagar." % pesos(o.get("precio_ficha"))
    lineas += ["    %d. %s" % (i + 1, x) for i, x in enumerate(pasos)]
    lineas += ["- " + x for x in notas_compra(o)]
    lineas += ["- Vigencia: %s" % vigencia_de(o), "", o["url"], "", conf, "",
               "- Tus topes de {}: ganga ${:,}; comprar ${:,}; mirar ${:,}".format(var["nombre"], g, c, t)]
    pq = linea_razon(o, prod, var)
    if pq:
        lineas.append(pq)
    lineas += ["", "Lo mejor de cada tienda ahora:", "", tabla(mejores), "",
               "No incluye devoluciones de banco (se ven hasta pagar)."]
    return asunto, "\n".join(lineas)


def avisado_de(v):
    """(precio, nivel) de un valor de avisado: un numero (el formato de siempre) o {"precio", "nivel"} (escrito por la
    primera version de confirmar, si quedo en algun estado)."""
    if isinstance(v, dict):
        return v.get("precio"), v.get("nivel")
    return v, None


def nivel_avisado(niveles, clave, precio):
    """Nivel con que se aviso la clave, si sigue valiendo para el precio avisado. avisado_nivel guarda [nivel, precio];
    si avisado[clave] ya no es ese precio (otra version del vigia volvio a avisar en medio), el nivel no vale.
    Acepta tambien el texto suelto de la ronda 2 (solo el nivel)."""
    v = niveles.get(clave)
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return v[0] if precio is not None and numero(v[1]) == numero(precio) else None
    return v if isinstance(v, str) else None


# Tercer elemento de avisado_nivel (la version d6ab147 no lee avisado_nivel; las rondas 2 y 3 solo leen los dos primeros):
RELEER = "releer"   # se aviso GANGA/COMPRAR con "ficha sin releer": queda pendiente de relectura en las corridas siguientes
HECHO = "hecho"     # ya salio el correo de confirmacion o de correccion de esa relectura: no se vuelve a releer


def marca_avisado(niveles, clave, precio):
    """Tercer elemento de avisado_nivel[clave] ("releer", "hecho" o None), si sigue valiendo para ese precio."""
    v = niveles.get(clave)
    if isinstance(v, (list, tuple)) and len(v) >= 3 and nivel_avisado(niveles, clave, precio):
        return v[2]
    return None


def normalizar_avisado(estado):
    """Deja estado["avisado"] como {clave: precio} (numeros, lo que lee cualquier version anterior del vigia, para poder
    volver atras sin que truene) y en estado["avisado_nivel"] {clave: [nivel, precio avisado]}; un nivel cuyo precio ya
    no es el de avisado se descarta."""
    avisado = estado.setdefault("avisado", {})
    niveles = estado.setdefault("avisado_nivel", {})
    for c, v in list(avisado.items()):
        if isinstance(v, dict):
            precio, niv = avisado_de(v)
            if precio is None:
                avisado.pop(c)
                niveles.pop(c, None)
                continue
            avisado[c] = precio
            if niv:
                niveles[c] = [niv, precio]
    for c in list(niveles):
        niv = nivel_avisado(niveles, c, avisado.get(c)) if c in avisado else None
        if niv:
            marca = marca_avisado(niveles, c, avisado[c])
            niveles[c] = [niv, avisado[c]] + ([marca] if marca else [])
        else:
            niveles.pop(c)
    return avisado, niveles


def avisos_de_precio(mejores, por_id, avisado, estado, ahora, arranque=None):
    """Avisos de precio de esta corrida. Lo que tocaria avisar (regla de avisado y BAJA_MINIMA) y es GANGA, COMPRAR o
    SIN CONFIRMAR relee su ficha (confirmar), del mas barato al mas caro. Devuelve [dict(clave, asunto, cuerpo, precio,
    nivel, resultado, razon)] y deja en mejores el precio y vendedor de la ficha cuando se confirmo. No abre nada.
    El nivel con que se aviso cada clave se lee de estado["avisado_nivel"]. arranque: time.monotonic() del inicio de la
    corrida; pasado SEGUNDOS_CONFIRMAR ya no se relee ("sin tiempo", el aviso sale igual). Una oferta que truene al
    revisarla se salta sola, sin tapar a las demas."""
    niveles = estado.setdefault("avisado_nivel", {})
    pend = []
    for clave, o in mejores.items():
        try:
            prod = por_id[o["prod"]]
            var = var_por_id(prod, o["var"])
            niv = nivel(o["precio"], prod, var)
            if not niv:
                avisado.pop(clave, None)   # subio del tope: la proxima baja vuelve a avisar
                niveles.pop(clave, None)
                continue
            ant, niv_ant = avisado_de(avisado[clave]) if clave in avisado else (None, None)
            niv_ant = niv_ant or nivel_avisado(niveles, clave, ant)
            marca = marca_avisado(niveles, clave, ant)
            # relectura: avisado con "ficha sin releer" y sin bajar lo bastante para un aviso nuevo
            relee = ant is not None and o["precio"] > ant - BAJA_MINIMA and marca == RELEER and niv in NIVELES_CONFIRMAR
            if ant is not None and o["precio"] > ant - BAJA_MINIMA and not relee and \
               not (niv_ant == "SIN CONFIRMAR" and marca != HECHO):
                continue
            pend.append((clave, o, prod, var, niv, ant, niv_ant, relee))
        except Exception as ex:
            print("   aviso de %s no se pudo revisar: %s" % (clave, str(ex)[:120]))
    # las ofertas nuevas primero (del mas barato al mas caro); las relecturas pendientes despues, dentro del mismo tope
    pend.sort(key=lambda x: (x[7], x[1]["precio"]))
    hechos = []
    if arranque is not None:
        estado["_arranque"] = arranque
    try:
        for clave, o, prod, var, niv, ant, niv_ant, relee in pend:
            resultado, final, razon_f = None, o, ""
            if niv in NIVELES_CONFIRMAR:
                resultado, final, razon_f = confirmar(o, prod, estado)
            if resultado == "confirmada":
                niv_f = nivel(final["precio"], prod, var) or niv
                mejores[clave] = final
            elif resultado == "no_coincide":
                niv_f = "SIN CONFIRMAR"
            else:
                niv_f = niv
            if relee:
                # pendiente de relectura: un solo correo mas cuando la ficha se deja leer (confirmacion o correccion);
                # si sigue sin poderse leer, nada (queda pendiente)
                if resultado not in ("confirmada", "no_coincide"):
                    continue
            elif ant is not None and final["precio"] > ant - BAJA_MINIMA:
                # sin bajar lo bastante de lo ya avisado: solo vuelve a avisar, una vez, si antes salio SIN CONFIRMAR y
                # ahora la ficha lo confirma (al precio que sea, dentro de su nivel; revisor R1-P3)
                if not (niv_ant == "SIN CONFIRMAR" and resultado == "confirmada" and niv_f != "SIN CONFIRMAR"):
                    continue
            hechos.append({"clave": clave, "o": final, "prod": prod, "var": var, "nivel": niv_f, "listado": niv,
                           "resultado": resultado, "razon": razon_f, "relectura": relee, "ant": ant})
    finally:
        estado.pop("_confirmaciones", None)
        estado.pop("_arranque", None)
    hora = ahora.astimezone(MX).strftime("%H:%M")
    for a in hechos:
        a["precio"] = a["o"]["precio"]
        try:
            a["asunto"], a["cuerpo"] = texto_aviso(a["o"], a["prod"], a["var"], a["nivel"], a["resultado"], a["razon"],
                                                   mejores, hora, a["listado"])
            if a.get("relectura"):
                mismo = a.get("ant") is None or numero(a["o"]["precio"]) == numero(a["ant"])
                previo = ("Confirmación del aviso anterior, que salió sin releer la ficha: ahora se releyó y coincide."
                          if a["resultado"] == "confirmada" and mismo else
                          "Se releyó la ficha: el precio ya es %s (el aviso anterior decía %s)." % (
                              pesos(numero(a["o"]["precio"])), pesos(numero(a["ant"])))
                          if a["resultado"] == "confirmada" else
                          "Corrección del aviso anterior, que salió sin releer la ficha: ahora se releyó y no coincide.")
                a["cuerpo"] = previo + "\n\n" + a["cuerpo"]
        except Exception as ex:   # texto de respaldo: el aviso sale aunque algo del texto falle
            print("   texto del aviso de %s fallo (%s); sale el texto corto" % (a["clave"], str(ex)[:100]))
            a["asunto"] = "%s %s · %s · %s" % (a["nivel"], pesos(numero(a["precio"])), a["var"]["nombre"], a["o"].get("tienda"))
            a["cuerpo"] = "Revisa la liga antes de comprar:\n\n%s\n\n%s" % (a["o"].get("url"), a["razon"] or "")
    return hechos


def publicar_aviso(av, avisado, seco, sin_avisos, abrir=None, niveles=None):
    """Abre el aviso y lo marca: avisado[clave] = precio (numero, como siempre) y niveles[clave] = nivel
    (estado["avisado_nivel"]). En seco o con SIN_AVISOS_PRECIO=1 no abre ni marca."""
    if seco:
        return False
    if sin_avisos:
        print("   (SIN_AVISOS_PRECIO=1: se habria avisado; no se abre ni se marca)")
        return False
    try:
        if (abrir or abrir_issue)(av["asunto"], av["cuerpo"]) is False:   # sin credenciales: no salio, no se marca
            return False
        avisado[av["clave"]] = av["precio"]
        if niveles is not None:
            # solo la confirmacion de una relectura la cierra; la correccion SIN CONFIRMAR sigue el camino de todo
            # SIN CONFIRMAR (se relee; sale un correo mas solo si luego confirma; revisor R4-H1)
            marca = HECHO if av.get("relectura") and av.get("resultado") == "confirmada" else \
                RELEER if av.get("resultado") == "sin_releer" and av.get("nivel") in ("GANGA", "COMPRAR") else None
            niveles[av["clave"]] = [av["nivel"], av["precio"]] + ([marca] if marca else [])
        return True
    except Exception as ex:
        print("   no se pudo abrir el aviso:", ex)
        return False


LECTORES_NUEVOS_ERROR = ""      # por que no se cargaron los lectores nuevos (lo usa main() al decir "lector no cargado")
_ENGANCHANDO = []
# LECTORES, VENDEDOR_OFICIAL y FICHAS tal como estaban antes del primer alta de lectores nuevos: el selftest del nucleo
# los usa para comprobar que pasa igual con los lectores nuevos apagados (revisor R5-H1)
_SOLO_NUCLEO = []


def _pruebas_lectores_nuevos(v):
    """correr(v) de pruebas\\casos_lectores_nuevos.py, sin red ni reloj; si la carpeta o el archivo faltan, no hace nada."""
    ruta = os.path.join(AQUI, "pruebas", "casos_lectores_nuevos.py")
    if not os.path.exists(ruta):
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("prueba_casos_lectores_nuevos", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def sin_red(*a, **k):
        raise AssertionError("una prueba de lectores nuevos quiso usar la red o dormir")
    guardados = (v.pedir, urllib.request.urlopen, time.sleep)
    try:
        v.pedir = urllib.request.urlopen = time.sleep = sin_red
        mod.correr(v)
    finally:
        v.pedir, urllib.request.urlopen, time.sleep = guardados


def enganchar_lectores_nuevos(prueba=None):
    """Suma los lectores de lectores_nuevos.py (Soriana, Cyberpuerta, Chedraui) y corre sus pruebas. Si el archivo falta,
    nada; si registrar truena o sus pruebas fallan, deja LECTORES, VENDEDOR_OFICIAL y FICHAS como estaban, lo dice en
    un renglon y el vigia sigue con sus lectores de siempre (main() dira "lector no cargado" de esas tiendas)."""
    global LECTORES_NUEVOS_ERROR
    if _ENGANCHANDO:           # las pruebas prueban este mismo enganche: sin recursion
        return
    yo = sys.modules[__name__]
    antes = [(d, dict(d)) for d in (LECTORES, VENDEDOR_OFICIAL, getattr(yo, "FICHAS", None)) if isinstance(d, dict)]
    if not _SOLO_NUCLEO:
        _SOLO_NUCLEO.extend(antes)
    etapa = "registrar"
    _ENGANCHANDO.append(1)
    try:
        import lectores_nuevos
        lectores_nuevos.registrar(yo)
        etapa = "pruebas"
        (prueba or _pruebas_lectores_nuevos)(yo)
        LECTORES_NUEVOS_ERROR = ""
    except Exception as ex:
        if etapa == "registrar" and isinstance(ex, ModuleNotFoundError) and ex.name == "lectores_nuevos":
            LECTORES_NUEVOS_ERROR = "no esta lectores_nuevos.py"
            return
        for d, copia in antes:
            d.clear()
            d.update(copia)
        LECTORES_NUEVOS_ERROR = ("sus pruebas fallan: " if etapa == "pruebas" else "") + "%s: %s" % (type(ex).__name__, ex)
        print("lectores nuevos apagados: %s" % LECTORES_NUEVOS_ERROR[:300])
    finally:
        _ENGANCHANDO.clear()


def tiendas_sin_lector(prods):
    """Tiendas configuradas en algun producto que no tienen lector cargado (revisor R3-H6): main() las pasa por fuente()
    como falla, para que corra el aviso de horas sin leer de siempre en vez de desaparecer en silencio."""
    return sorted({t for p in prods for t in p["tiendas"] if t not in LECTORES})


enganchar_lectores_nuevos()


# ------------------------------------------------------------------------------- foro (respaldo)
def a_numero(txt):
    m = re.search(r"\$?\s*([\d][\d,\.]*)", txt or "")
    try:
        return float(m.group(1).replace(",", "")) if m else None
    except ValueError:
        return None


def limpio(texto, tope=700):
    t = re.sub(r"<br\s*/?>", "\n", texto or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", t)).strip()[:tope]


def bancos(texto):
    propios = [n for n, p in BANCOS_PROPIOS.items() if re.search(p, texto, re.I)]
    ajenos = [n for n, p in BANCOS_AJENOS.items() if re.search(p, texto, re.I)]
    if propios:
        return "aplica con " + " o ".join(propios), True
    if ajenos:
        return "pide " + " o ".join(ajenos) + " (no la tienes)", False
    return "sin banco", True


def leer_foro(prods, estado):
    feeds = []
    for p in prods:
        for f in p.get("foro", {}).get("feeds", []):
            if f not in feeds:
                feeds.append(f)
    items = {}
    for f in feeds:
        e, t = pedir("https://www.promodescuentos.com/rss/" + f)
        if e != 200:
            continue
        for it in ET.fromstring(t.encode("utf-8")).iter("item"):
            d = {"titulo": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
                 "guid": (it.findtext("guid") or it.findtext("link") or "").strip(), "fecha": it.findtext("pubDate") or "",
                 "descripcion": it.findtext("description") or "", "tienda": "", "precio_txt": ""}
            for h in it:
                if h.tag.endswith("merchant"):
                    d["tienda"], d["precio_txt"] = h.attrib.get("name", ""), h.attrib.get("price", "")
            items.setdefault(d["guid"], d)
    if feeds and not items:
        raise RuntimeError("ningun feed del foro respondio")
    # Los codigos de cupon del foro los junta cosechar_cupones de los datos de cada hilo (campo voucherCode).
    return list(items.values())


DEVOLUCION = re.compile(r"bonificaci[oó]n|cashback|cash\s+back", re.I)
# frases con bonificacion que no es devolucion del banco (revisor D, 26 nov: monedero de la tienda, Mercado Pago, negacion)
NO_ES_DEVOLUCION = re.compile(r"monedero|mercado\s*pago|no\s+aplica|sin\s+bonificaci", re.I)


def devolucion_de_banco(texto):
    """Nombre del banco propio si una misma frase dice que el banco devuelve dinero despues (bonificacion o
    cashback); '' si no. Saul, 26 nov 2026 (respuesta 3, 'si, obviamente'): una oferta con devolucion de BBVA si
    cuenta como precio, pero el aviso dice que se paga completo y que la devolucion tiene tope al mes."""
    for f in re.split(r"(?<=[.!?;])\s+|\n", texto or ""):
        if DEVOLUCION.search(f) and not NO_ES_DEVOLUCION.search(f):
            if any(re.search(p, f, re.I) for p in BANCOS_AJENOS.values()):
                continue   # la frase nombra otro banco: no se sabe de quien es la devolucion (revisor D, H1)
            for nombre, pat in BANCOS_PROPIOS.items():
                if re.search(pat, f, re.I):
                    return nombre
    return ""


def aviso_foro(prods, it):
    for prod in prods:
        var = variante(prod, it["titulo"])
        if var:
            break
    else:
        return None
    if not re.search(TIENDAS_FORO, it["tienda"], re.I):
        return None
    precio = a_numero(it["precio_txt"])
    if precio is not None and (precio < prod["piso"] or not nivel(precio, prod, var)):
        return None
    texto_hilo = it["titulo"] + " " + limpio(it["descripcion"], 3000)
    nota, me_aplica = bancos(texto_hilo)
    niv = "SIN PRECIO, REVISAR" if precio is None else nivel(precio, prod, var) if me_aplica else "SOLO CON OTRO BANCO"
    dev = devolucion_de_banco(it["titulo"] + "\n" + limpio(it["descripcion"], 3000)) if me_aplica and precio is not None else ""
    ptxt = pesos(precio)
    g, c, t = topes(prod, var)
    tienda = it["tienda"] or "tienda sin dato"
    con_dev = " con devolución {}".format(dev) if dev else ""
    if niv == "SIN CONFIRMAR":
        asunto = "[foro] SIN CONFIRMAR {}{} · {} · {} · abre la ficha antes de creerlo".format(ptxt, con_dev, var["nombre"], tienda)
    else:
        asunto = "[foro] {} {}{} · {} · {} · ficha sin releer".format(niv, ptxt, con_dev, var["nombre"], tienda)
    raro = niv == "SIN CONFIRMAR"
    if dev:
        linea_precio = ("- Precio final después de la devolución de {}: **{}** (lo publicó el foro, no se leyó en la tienda; "
                        "en la tienda se cobra más)").format(dev, ptxt)
        linea_banco = ("    2. Banco: {}. Es devolución, no descuento: en la tienda pagas el precio completo y {} te regresa "
                       "una parte después, en tu estado de cuenta. Esa devolución tiene tope al mes: si ya lo llenaste con "
                       "otra compra{}, no te regresa nada y pagas el precio completo.").format(
                           nota, dev, " (como el 13 nov)" if dev == "BBVA" else "")
    else:
        linea_precio = "- {}: **{}** (precio publicado en el foro, no leído en la tienda)".format(
            "Precio que se vio" if raro else "Pagas", ptxt)
        linea_banco = "    2. Banco: {}.".format(nota)
    cuerpo = "\n".join((["Sin confirmar: el precio publicado está muy por debajo de lo que suele costar.", ""] if raro else []) + [
        "**{}**".format(it["titulo"]), "Variante vigilada: {}".format(var["nombre"]), "",
        "- Dónde: {}; quién vende: no se sabe (lo publicó un usuario del foro de ofertas)".format(tienda),
        linea_precio,
        "- Cómo se llega:",
        "    1. Abre la publicación del foro (liga de abajo) y de ahí la tienda.",
        linea_banco,
        ("    3. Confirma que lo venda la marca o la propia tienda, con garantía en México, y que tras la devolución "
         "quede en {}.".format(ptxt) if dev else
         "    3. Confirma que lo venda la marca o la propia tienda, con garantía en México, y que el total sea {}.".format(ptxt)),
        "- Vigencia: no se sabe (publicada {})".format(it["fecha"]), "",
        it["link"], "",
        "No se pudo releer la ficha (esta tienda no deja releer la ficha): ábrela antes de comprar.", "",
        *["- Por qué: " + r for r in [(globals().get("razon") or (lambda *a: ""))(precio, prod, var) if precio is not None and me_aplica else ""] if r],   # linea A (si falta razon, el aviso sale sin ese renglon)
        "- Tus topes de {}: ganga ${:,}; comprar ${:,}; mirar ${:,}".format(var["nombre"], g, c, t), "",
        "> " + limpio(it["descripcion"]).replace("\n", "\n> ")])
    return asunto, cuerpo


# ------------------------------------------------------------------------------- avisos
def abrir_issue(titulo, texto):
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    dueno = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    if not (repo and token):
        print("   (sin credenciales de GitHub: no se abre aviso) ::", titulo)
        return False
    if dueno:
        texto += "\n\nAviso para @" + dueno
    datos = json.dumps({"title": titulo[:250], "body": texto}).encode("utf-8")
    req = urllib.request.Request("https://api.github.com/repos/%s/issues" % repo, data=datos, method="POST", headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vigia-precios", "Content-Type": "application/json"})
    desde = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # mismo tope duro que pedir(): un aviso que no sale por tiempo no se marca y sale en la corrida siguiente
    try:
        with urllib.request.urlopen(req, timeout=tiempo_de_peticion(30)) as r:
            try:   # ya llego la respuesta 2xx: el issue existe aunque el cuerpo se corte o no se entienda
                liga = json.loads(r.read()).get("html_url")
            except Exception as ex:
                liga = "(respuesta 2xx sin cuerpo legible: %s)" % str(ex)[:80]
            print("   aviso abierto:", liga)
        return True
    except CORTE_AMBIGUO as ex:
        # 26 nov 2026 (pendiente 2): la peticion salio y la respuesta no llego; GitHub pudo haberlo creado. Se busca el
        # issue por titulo exacto entre los recien creados: si esta, cuenta como abierto (quien llama lo marca y no se
        # repite cada 15 min); si no esta o la busqueda falla, sigue el error de siempre (no se marca, sale despues).
        # Un HTTPError (GitHub dijo que no) o un corte antes de conectar no entran aqui: nunca se marcan.
        liga = _issue_ya_creado(repo, token, titulo[:250], desde)
        if liga:
            print("   aviso abierto (la respuesta no llego; confirmado en la lista de GitHub):", liga)
            return True
        raise ex


CORTE_AMBIGUO = (TimeoutError, ConnectionResetError, ConnectionAbortedError, http.client.RemoteDisconnected,
                 http.client.IncompleteRead)


def _issue_ya_creado(repo, token, titulo, desde):
    """html_url del issue con ese titulo exacto creado desde `desde` (ISO UTC), o None; una consulta, sin reintento."""
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/issues?state=all&sort=created&direction=desc&per_page=30" % repo, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vigia-precios"})
    try:
        with urllib.request.urlopen(req, timeout=tiempo_de_peticion(10)) as r:   # revisor E (H9): 10 s, no 30
            lista = json.loads(r.read())
    except Exception as ex:
        print("   no se pudo comprobar si el aviso quedo creado:", str(ex)[:120])
        return None
    for i in lista if isinstance(lista, list) else []:
        if isinstance(i, dict) and i.get("title") == titulo and (i.get("created_at") or "") >= desde:
            return i.get("html_url") or "creado"
    return None


def tabla(mejores):
    """Tres columnas para que quepa en el celular; quien vende y como se llega van debajo, un renglon por oferta."""
    orden = sorted(mejores.values(), key=lambda o: (o["prod"], o["nombre"], o["precio"]))
    filas = ["| Producto | Tienda | Precio |", "|---|---|---|"]
    for o in orden:
        filas.append("| {} | [{}]({}) | ${:,.0f} |".format(o["nombre"], o["tienda"], o["url"], o["precio"]))
    filas.append("")
    for o in orden:
        filas.append("- {}, {}: lo vende {}{}".format(o["tienda"], o["nombre"], o["vendedor"] or "(sin dato)",
                                                      "; " + o["nota"] if o.get("nota") else ""))
    return "\n".join(filas)


# Corridas seguidas sin ver un modelo en una tienda que si se leyo, antes de avisar que se agoto
# (unos 15 min por corrida: 4 = una hora; evita avisos por un tropiezo de lectura).
AUSENCIAS_AGOTADO = 4


def clave_de(prods, tienda, producto):
    for p in prods:
        for v in p["variantes"]:
            if v["nombre"] == producto:
                return "%s|%s|%s" % (tienda, p["id"], v["id"])
    return None


def sembrar_conocidos(estado, prods, mejores, leidas, ruta=None):
    """Primera vez: lo que el historial vio a la venta y hoy falta en una tienda leida queda como agotado, sin aviso."""
    conocidos = estado.setdefault("conocidos", {})
    agotados = estado.setdefault("agotados", {})
    try:
        with open(ruta or HISTORIAL, encoding="utf-8") as f:
            filas = list(csv.DictReader(f))
    except Exception:
        filas = []
    for fila in filas:
        c = clave_de(prods, fila["tienda"], fila["producto"])
        if c:
            conocidos[c] = {"producto": fila["producto"], "precio": float(fila["precio"]), "url": fila["liga"],
                            "visto": fila["fecha"]}
    for c, info in conocidos.items():
        if c not in mejores and c.split("|")[0] in leidas:
            agotados[c] = {"desde": "desde " + info["visto"], "precio": info["precio"]}


def nivel_regreso(c, o, prod, var, fichas):
    """(texto del nivel para el asunto de "volvio a la venta", renglon extra del cuerpo). GANGA o COMPRAR solo si la
    ficha se confirmo en esta corrida; si esta corrida ya mando el aviso de precio de esa clave, no se repite el nivel
    (revisor R1-F2: dos correos que se contradecian)."""
    info = (fichas or {}).get(c)
    if info and info.get("salio"):
        return "mira el aviso de precio de esta corrida", "El aviso de precio de esta misma corrida dice qué encontró la ficha."
    if info:
        res, niv = info.get("resultado"), info.get("nivel")
        if res == "confirmada":
            return niv, "Se releyó la ficha y coincide."
        if res == "no_coincide":
            return "SIN CONFIRMAR · abre la ficha antes de creerlo", "Sin confirmar: %s." % info.get("razon", "")
        if res == "sin_releer":
            return "%s · ficha sin releer" % niv, "No se pudo releer la ficha (%s): ábrela antes de comprar." % info.get("razon", "")
    niv = nivel(o["precio"], prod, var)
    if niv == "SIN CONFIRMAR":
        return "SIN CONFIRMAR · abre la ficha antes de creerlo", "Sin confirmar: el precio está muy por debajo de lo que suele costar."
    if niv in ("GANGA", "COMPRAR"):
        return "%s · ficha sin releer" % niv, "La ficha no se releyó: ábrela antes de comprar."
    return niv or "arriba de tus topes", ""


def disponibilidad(estado, prods, mejores, leidas, hoy, fichas=None):
    """Devuelve avisos [(asunto, cuerpo, clave)] de modelos agotados o de vuelta, y actualiza el estado.
    fichas: {clave: {"resultado", "nivel", "razon", "salio"}} de los avisos de precio de esta corrida (nivel_regreso).
    Un aviso que truene al armarse se salta y su clave queda como estaba (la corrida siguiente lo reintenta)."""
    por_id = {p["id"]: p for p in prods}
    conocidos = estado.setdefault("conocidos", {})
    agotados = estado.setdefault("agotados", {})
    ausente = estado.setdefault("ausente", {})
    avisos = []
    # claves que una regla de titulos mal puesta dejo sembradas: se olvidan sin aviso de "agotado"
    for p in prods:
        for c in p.get("olvidar", []):
            if c not in mejores:
                conocidos.pop(c, None); ausente.pop(c, None); agotados.pop(c, None)
                estado.get("avisado", {}).pop(c, None)   # y sin aviso de precio pendiente de esa clave
                estado.get("avisado_nivel", {}).pop(c, None)
    for c, o in mejores.items():
        conocidos[c] = {"producto": o["nombre"], "precio": o["precio"], "url": o["url"], "visto": hoy}
        ausente.pop(c, None)
        if c in agotados:
            try:
                prod = por_id[o["prod"]]
                var = var_por_id(prod, o["var"])
                g, cc, t = topes(prod, var)
                niv, renglon = nivel_regreso(c, o, prod, var, fichas)
                avisos.append((
                    "Volvio a la venta: {} en {} a ${:,.0f} · {}".format(o["nombre"], o["tienda"], o["precio"], niv),
                    "\n".join(([renglon, ""] if renglon.startswith("Sin confirmar") else []) + [
                        "**{}** estaba agotado en {} ({}; ultimo precio ${:,.0f}) y ya se puede comprar otra vez.".format(
                            o["nombre"], o["tienda"], agotados[c].get("desde", "?"), agotados[c].get("precio", 0)), "",
                        "- Qué es: {}".format(o.get("titulo") or o["nombre"]),
                        "- Dónde: {}; lo vende {}".format(o["tienda"], o["vendedor"] or "(sin dato)"),
                        "- Precio leído ahora en la tienda: **${:,.0f}**".format(o["precio"]),
                        "- Cómo se llega: {}".format(o["nota"] or "precio directo, sin cupón"),
                        "- Tus topes de {}: ganga ${:,}; comprar ${:,}; mirar ${:,}".format(var["nombre"], g, cc, t), "",
                        o["url"], ""] + ([renglon, ""] if renglon and not renglon.startswith("Sin confirmar") else []) + [
                        "Lo mejor de cada tienda ahora:", "", tabla(mejores)]), c))
                agotados.pop(c)
            except Exception as ex:
                print("   aviso de regreso de %s no se pudo armar: %s" % (c, str(ex)[:120]))
    for c, info in conocidos.items():
        if c in mejores or c.split("|")[0] not in leidas or c in agotados:
            continue
        ausente[c] = ausente.get(c, 0) + 1
        if ausente[c] >= AUSENCIAS_AGOTADO:
            try:
                tienda = c.split("|")[0]
                avisos.append((
                    "Agotado: {} ya no esta a la venta en {}".format(info["producto"], tienda),
                    "\n".join([
                        "{} no aparece a la venta en {} desde hace {} corridas seguidas (cerca de una hora), aunque la "
                        "tienda si se leyo.".format(info["producto"], tienda, ausente[c]), "",
                        "- Ultima vez a la venta: {} a ${:,.0f}".format(info["visto"], info["precio"]),
                        "- Liga: {}".format(info["url"]), "",
                        "Te aviso en cuanto vuelva a estar a la venta, con su precio."]), c))
                agotados[c] = {"desde": "desde " + info["visto"], "precio": info["precio"]}
                ausente.pop(c)
            except Exception as ex:
                print("   aviso de agotado de %s no se pudo armar: %s" % (c, str(ex)[:120]))
    return avisos


# Reloj general: pasado este tiempo desde el arranque, las tiendas que faltan se saltan como "no leidas esta corrida"
# (el trabajo de GitHub tiene 12 min; asi queda margen para escribir la memoria y lanzar la siguiente corrida).
SEGUNDOS_CORRIDA = 420


def reloj_vencido(arranque, ahora=None):
    return (reloj() if ahora is None else ahora) - arranque > SEGUNDOS_CORRIDA


def recortar_vistos(vistos, tope=400):
    """Los `tope` guids del foro vistos mas recientemente (el valor es la fecha del hilo, RFC 2822 o ISO);
    una fecha ilegible cuenta como la mas vieja."""
    def cuando(fecha):
        try:
            d = email.utils.parsedate_to_datetime(fecha)
        except Exception:
            try:
                d = dt.datetime.fromisoformat(str(fecha))
            except Exception:
                return float("-inf")
        return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()
    return dict(sorted(vistos.items(), key=lambda kv: cuando(kv[1]), reverse=True)[:tope])


def avisar_disponibilidad(estado, prods, mejores, leidas, hoy, abrir=None, callar=False, fichas=None):
    """Corre disponibilidad() y abre un aviso por cada resultado (abrir=None: solo imprime, para --seco).
    Si un aviso no se pudo abrir, se deshace lo que disponibilidad() marco para esa clave, para que la proxima
    corrida lo vuelva a intentar: un "agotado" vuelve a quedar a una ausencia de avisar; un "volvio" sigue agotado.
    callar=True (SIN_AVISOS_PRECIO=1): se calcula sobre una copia, se imprime lo que se habria avisado y el estado de
    disponibilidad no se toca, para que la siguiente corrida con pruebas en verde lo mande."""
    if callar:
        avisos = disponibilidad(json.loads(json.dumps(estado)), prods, mejores, leidas, hoy, fichas)
        for asunto, _, _ in avisos:
            print(" * DISPONIBILIDAD (SIN_AVISOS_PRECIO=1: se habria avisado; no se abre ni se marca)", asunto)
        return avisos
    antes_ag = dict(estado.get("agotados", {}))
    avisos = disponibilidad(estado, prods, mejores, leidas, hoy, fichas)
    ag = estado.get("agotados", {})
    for asunto, cuerpo, c in avisos:
        print(" * DISPONIBILIDAD", asunto)
        if abrir is None:
            continue
        try:
            if abrir(asunto, cuerpo) is False:
                raise RuntimeError("sin credenciales de GitHub")
        except Exception as ex:
            print("   no se pudo abrir el aviso:", ex)
            if c in antes_ag:
                ag[c] = antes_ag[c]
            else:
                ag.pop(c, None)
                estado.setdefault("ausente", {})[c] = AUSENCIAS_AGOTADO - 1
    return avisos


def _partes(regex):
    """Alternativas de primer nivel de una regla (lo separado por | fuera de parentesis y corchetes)."""
    partes, prof, act, i = [], 0, "", 0
    while i < len(regex):
        ch = regex[i]
        if ch == "\\" and i + 1 < len(regex):
            act += regex[i:i + 2]
            i += 2
            continue
        if ch in "([":
            prof += 1
        elif ch in ")]":
            prof -= 1
        if ch == "|" and prof == 0:
            partes.append(act)
            act = ""
        else:
            act += ch
        i += 1
    return [p for p in partes + [act] if p]


def _legible(regex):
    """Una regla de titulos dicha con palabras ('estaci[oó]n\\s+de' -> 'estacion de')."""
    m = re.match(r"^\^\(\?!\.\*\((.*?)\)\)\.\*\((.*)\)$", regex)
    if m:
        return "si el titulo no dice %s: %s" % (" ni ".join(_legible(x) for x in _partes(m.group(1))),
                                                 ", ".join(_legible(x) for x in _partes(m.group(2))))
    s = re.sub(r"\\b|\(\?:|\^|\$|®\??", "", regex)
    s = re.sub(r"\\s[*+?]?", " ", s)
    s = re.sub(r"\[([^\]])[^\]]*\]", r"\1", s)
    s = s.replace("\\d", "#").replace("\\", "").replace("?", "").replace("*", "").replace("+", "")
    s = s.replace("|", " o ")
    return re.sub(r"\s+", " ", s).strip()


def cobertura(prods=None):
    """Que se vigila, en palabras, para leer en una pantalla. No usa red."""
    prods = prods or cargar_config(CONFIG)
    pesos = lambda n: "${:,}".format(n)
    print("QUE VIGILA EL VIGIA (segun %s)" % os.path.basename(CONFIG))
    glob = EXCLUIR[3:-3] if EXCLUIR.startswith(r"\b(") and EXCLUIR.endswith(r")\b") else EXCLUIR
    print("Nunca entran, en ningun producto: " + ", ".join(_legible(x) for x in _partes(glob)))
    for p in prods:
        print("")
        print("== %s ==" % p["nombre"])
        colores = {}
        for sku, (vid, color, _pag) in p["tiendas"].get("Samsung", {}).get("skus", {}).items():
            colores.setdefault(vid, []).append(color)
        print("Se vigila (topes: ganga / comprar / mirar):")
        for v in p["variantes"]:
            g, c, t = topes(p, v)
            print("  - %s: %s / %s / %s (precio de lista %s)" % (v["nombre"], pesos(g), pesos(c), pesos(t), pesos(v["lista"])))
            pide = [_legible(x) for x in v.get("todas", [])]
            no = [_legible(x) for x in v.get("ninguna", [])]
            if pide or no:
                print("      el titulo debe decir: %s%s" % (" y ".join(pide) or "(nada mas)",
                                                         ("; y no decir: " + ", ".join(no)) if no else ""))
            if colores.get(v["id"]):
                print("      colores en Samsung: " + ", ".join(colores[v["id"]]))
        print("Queda fuera:")
        if p.get("excluir"):
            print("  - titulos que digan: " + ", ".join(_legible(x) for x in _partes(p["excluir"])))
        print("  - precios por debajo de %s (no pueden ser el aparato)" % pesos(p["piso"]))
        print("  - cualquier otra version del producto que no este en la lista de arriba")
        if colores:
            sin = [v["nombre"] for v in p["variantes"] if v["id"] not in colores]
            if sin:
                print("  - en Samsung no se piden estas versiones (no tienen codigo de modelo): " + ", ".join(sin))
        req = p["requisitos"]
        if req.get("solo_vendedor_oficial"):
            print("  - lo que venda un tercero: solo cuenta la marca o la tienda misma")
        for tienda, regla in sorted(req.get("vendedor_oficial", {}).items()):
            print("      en %s solo cuenta si vende: %s" % (tienda, _legible(regla)))
        if not req.get("permite_importados"):
            print("  - importados")
        for k in sorted(p):
            if k in ("_regla", "_comentario", "_variantes") and isinstance(p[k], str):
                print("  nota: " + p[k])
        for k in ("_regla", "_comentario"):
            if isinstance(req.get(k), str):
                print("  nota: " + req[k])
        for tienda, reglas in sorted((p.get("ficha_atributos") or {}).items()):
            for r in reglas:
                print("  en %s el titulo se completa con la ficha: si %s, se anade \"%s\"%s" % (
                    tienda, " y ".join("%s es %s" % (k, _legible(x)) for k, x in (r.get("si") or {}).items()),
                    r.get("agrega", ""), (" (salvo si dice %s)" % _legible(r["no_si"])) if r.get("no_si") else ""))
        directas = [t for t in LECTORES if t in p["tiendas"]]
        print("Tiendas que se leen directo: " + ", ".join(directas))
        sin_lector = [t for t in p["tiendas"] if t not in LECTORES]
        if sin_lector:
            print("Tiendas configuradas que aun no tienen lector: " + ", ".join(sin_lector))
        foro = [x for x in (_legible(a) for a in _partes(TIENDAS_FORO))
                if not any(re.search(re.escape(x), t, re.I) for t in directas)]
        print("Tiendas que solo entran por el foro Promodescuentos: " + ", ".join(foro))
    return 0


def _prueba_corte_ambiguo():
    """Pendiente 2 (26 nov 2026), sin red: abrir_issue con la respuesta cortada. Si el issue aparece en la lista de
    GitHub cuenta como abierto (quien llama lo marca: no se repite); si no aparece, o GitHub dijo que no (HTTPError), o
    el corte fue antes de enviar (URLError), lanza como siempre (no se marca: sale despues)."""
    class Resp:
        def __init__(self, cuerpo):
            self.cuerpo = cuerpo

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.cuerpo
    titulo = "Otra forma de pago: producto X a $31,999 en una tienda con PayPal"
    reciente = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    guardados = (urllib.request.urlopen, {k: os.environ.get(k) for k in ("GITHUB_REPOSITORY", "GITHUB_TOKEN",
                                                                          "GITHUB_REPOSITORY_OWNER")})
    try:
        os.environ.update({"GITHUB_REPOSITORY": "x/y", "GITHUB_TOKEN": "t", "GITHUB_REPOSITORY_OWNER": ""})
        for corte, lista, espera in (
                (TimeoutError("read timed out"), [{"title": titulo, "created_at": reciente, "html_url": "u/1"}], True),
                (http.client.RemoteDisconnected("cerro"), [{"title": titulo, "created_at": reciente, "html_url": "u/1"}], True),
                (TimeoutError("read timed out"), [{"title": titulo, "created_at": "2026-01-01T00:00:00Z"}], "lanza"),
                (TimeoutError("read timed out"), [{"title": "otro", "created_at": reciente}], "lanza"),
                (TimeoutError("read timed out"), RuntimeError("lista caida"), "lanza"),
                (urllib.error.HTTPError("u", 500, "x", {}, None), None, "lanza"),
                (urllib.error.URLError("getaddrinfo failed"), None, "lanza")):
            pedidas = []

            def falso(req, timeout=None, corte=corte, lista=lista, pedidas=pedidas):
                pedidas.append(req.get_method())
                if req.get_method() == "POST":
                    raise corte
                if isinstance(lista, Exception):
                    raise lista
                return Resp(json.dumps(lista).encode("utf-8"))
            urllib.request.urlopen = falso
            try:
                r = abrir_issue(titulo, "cuerpo")
            except Exception as ex:
                r = "lanza"
                assert ex is corte or isinstance(lista, Exception) or lista is not None, ex
            assert r == espera, (corte, lista, r)
            if lista is None:
                assert pedidas == ["POST"], pedidas          # GitHub dijo que no o no se conecto: ni se busca
        # 2xx con cuerpo cortado: ya se creo
        urllib.request.urlopen = lambda req, timeout=None: Resp(b'{"html_url": "u/2"')
        assert abrir_issue(titulo, "cuerpo") is True
    finally:
        urllib.request.urlopen = guardados[0]
        for k, v in guardados[1].items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def selftest():
    prods = cargar_config()
    p = prods[0]
    casos = [("Samsung Galaxy Z Fold8 Ultra Grafito 512GB", "ultra-512"), ("Galaxy Z Fold 8 512GB Crema", "normal-512"),
             ("Samsung Galaxy ZFold8 Ultra 1TB Grafito", "ultra-1tb"), ("Funda para Samsung Galaxy Z Fold8 Ultra", None),
             ("Samsung Galaxy Z Fold8 256GB", None), ("Galaxy Z Fold8 Ultra 512GB version internacional", None),
             ("Samsung Galaxy Z Fold7 512GB", None), ("Smartphone Galaxy Z Fold8 Ultra Super AMOLED", "ultra"),
             # titulos reales del 24 nov 2026: Elektra (Ultra 256 GB, sin lista de Samsung: se ignora), Costco, Liverpool
             ("Samsung Galaxy Z Fold 8 Ultra 256 GB Grafito", None), ("Samsung Galaxy Z Fold Ultra 8 512GB Violeta", "ultra-512"),
             ("Samsung Galaxy Z Fold8 12+512GB Lavanda", "normal-512"), ("Fold8 super AMOLED plus 7.6 pulgadas", "normal"),
             ("Funda para Samsung Galaxy Fold8 Ultra de policarbonato", None), ("Smartphone Samsung Galaxy Z Fold7 5G 256GB", None)]
    for titulo, esperado in casos:
        v = variante(p, titulo)
        assert (v["id"] if v else None) == esperado, (titulo, v and v["id"], esperado)
    PROSPECTIVA.clear()   # linea A: las cifras fijas de abajo prueban el camino de fracciones, sin prospectiva.json
    assert topes(p, var_por_id(p, "ultra-512")) == (22000, 24000, 26000), topes(p, var_por_id(p, "ultra-512"))
    # Fold8 512 con topes_fijos (Saul, 26 nov 2026: comprar sube a 22,600): ganan tambien sin prospectiva.json
    assert topes(p, var_por_id(p, "normal-512")) == (19900, 22600, 25600), topes(p, var_por_id(p, "normal-512"))
    assert topes(p, var_por_id(p, "normal")) == (19900, 22600, 25600), topes(p, var_por_id(p, "normal"))
    assert nivel(23999, p, var_por_id(p, "ultra-512")) == "COMPRAR" and nivel(23999, p, var_por_id(p, "normal-512")) == "MIRAR"
    assert nivel(22600, p, var_por_id(p, "normal-512")) == "COMPRAR" and nivel(22601, p, var_por_id(p, "normal-512")) == "MIRAR"
    assert "Cyberpuerta" not in p["tiendas"] and "Cyberpuerta" in next(x for x in prods if x["id"] == "ps5")["tiendas"]
    # requisitos: tercero e importado fuera cuando el producto lo pide
    assert oferta(p, "Elektra", "Samsung Galaxy Z Fold8 512GB", 26999, "u", "Systore") is None
    assert oferta(p, "Amazon", "Samsung Galaxy Z Fold8 512GB", 30000, "u", "Amazon Remates de Almacén") is None
    assert oferta(p, "Amazon", "Samsung Galaxy Z Fold8 512GB", 30000, "u", "Amazon México")["precio"] == 30000
    assert oferta(p, "Amazon", "Samsung Galaxy Z Fold8 512GB", 30000, "u", "Amazon México", importado=True) is None
    # producto que si admite terceros e importados: el precio comparado ya suma el cargo estimado
    libre = json.loads(json.dumps(p))
    libre["requisitos"] = {"solo_vendedor_oficial": False, "permite_importados": True, "importacion_estimada": 0.17}
    o = oferta(libre, "Amazon", "Samsung Galaxy Z Fold8 512GB", 20000, "u", "Tienda X", importado=True)
    assert o["precio"] == 23400.0 and "importado" in o["nota"], o
    assert "tercero" in oferta(libre, "Elektra", "Samsung Galaxy Z Fold8 512GB", 26999, "u", "Systore")["nota"]
    # Amazon: rechazos medidos desde GitHub y lectura de la ficha (estructura real de una ficha del 20 nov 2026)
    assert amazon_rechazo(503, "x" * 1669, "s-search-result") == "503 de 1669 bytes"
    assert amazon_rechazo(200, "x" * 2321, "s-search-result") == "200 de 2321 bytes"
    assert amazon_rechazo(200, '<div data-component-type="s-search-result">', "s-search-result") == ""
    ficha = ('<span id="productTitle" class="a-size-medium">  Samsung Galaxy Z Fold8 Crema 512GB </span>'
             '<span class="a-price aok-align-center priceToPay apex-pricetopay-value" data-a-size="xl">'
             '<span class="a-offscreen"> </span><span aria-hidden="true"><span class="a-price-symbol">$</span>'
             '<span class="a-price-whole">34,499<span class="a-price-decimal">.</span></span>'
             '<span class="a-price-fraction">00</span></span></span>'
             '<div id="desktop_buybox"><span>Vendido por</span> <span>Amazon México</span></div>')
    assert ficha_amazon(ficha) == ("Samsung Galaxy Z Fold8 Crema 512GB", 34499.0, "Amazon México", False), ficha_amazon(ficha)
    assert ficha_amazon(ficha.replace("apex-pricetopay-value", ""))[1] is None   # agotado: sin precio a pagar
    # agotado y regreso: 4 ausencias seguidas avisan una vez; tienda no leida no cuenta; al volver avisa con precio
    est = {"conocidos": {"Samsung|fold8|ultra-512": {"producto": "Fold8 Ultra 512 GB", "precio": 34199.2, "url": "u",
                                                     "visto": "2026-11-20 14:34"}}}
    for _ in range(AUSENCIAS_AGOTADO - 1):
        assert disponibilidad(est, prods, {}, {"Samsung"}, "h") == []
    assert disponibilidad(est, prods, {}, set(), "h") == []          # Samsung sin leer: no suma ausencia
    av = disponibilidad(est, prods, {}, {"Samsung"}, "h")
    assert len(av) == 1 and av[0][0].startswith("Agotado: Fold8 Ultra 512 GB"), av
    assert disponibilidad(est, prods, {}, {"Samsung"}, "h") == []    # ya avisado: no repite
    vuelta = {"Samsung|fold8|ultra-512": {"prod": "fold8", "var": "ultra-512", "nombre": "Fold8 Ultra 512 GB",
                                          "tienda": "Samsung", "precio": 23999.0, "url": "u", "nota": "", "vendedor": "Samsung"}}
    av = disponibilidad(est, prods, vuelta, {"Samsung"}, "h")
    assert len(av) == 1 and av[0][0].startswith("Volvio a la venta") and "COMPRAR" in av[0][0], av
    assert disponibilidad(est, prods, vuelta, {"Samsung"}, "h") == [] and not est["agotados"]
    # PS5: titulos reales de Amazon Mexico del 24 nov 2026 (precio de la ficha; vendedor Amazon Mexico)
    ps = next(x for x in prods if x["id"] == "ps5")
    casos_ps5 = [
        ("Consola PlayStation 5 - Edición Digital de 825 GB: Astro Bot y Gran Turismo 7 - Digital Edition", 12499, "digital"),
        ("Consola PlayStation 5 de 1 TB: ASTRO BOT y Gran Turismo 7, Modelo PS5STA-ASTRO - Standard Edition", 14599, None),
        ("PlayStation 5 Consola Slim Disco Digital + PS5 Digital 825GB con ASTRO BOT y GT7", 14498, "digital"),
        ("Sony Death Stranding 2 PS5 + PlayStation 5 1TB ASTRO BOT/GT7", 15490, None),
        ("PlayStation 5 Unidad de Disco- Consola Slim digital", 1999, None),
        ("PlayStation 5 Cubierta de Consola Digital Slim – Chroma Pearl", 1417.79, None),
        ("PlayStation 5 Reproductor Remoto Portal Midnight Black", 5199, None),
        ("PlayStation DualSense® Wireless Controller - Cosmic Red", 1153.33, None),
        ("Saros - Playstation 5", 899, None),
        ("Consola PlayStation 5 Pro Digital 2 TB", 19299, None)]
    for titulo, precio, esperado in casos_ps5:
        o = oferta(ps, "Amazon", titulo, precio, "u", "Amazon México")
        assert (o["var"] if o else None) == esperado, (titulo, o and o["var"], esperado)
        assert not esperado or marca_ok(ps, titulo), titulo
    assert variante(ps, "Consola PlayStation 5 Pro Digital 2 TB") is None        # la Pro no casa ni sin el piso
    assert variante(ps, "Consola PS5 Edicion Digital (sin lector de disco) 825 GB")["id"] == "digital"
    assert variante(ps, "Consola PlayStation 5 Slim") is None                    # Saul 24 nov: solo la Digital
    assert [v["id"] for v in ps["variantes"]] == ["digital"]
    # olvidar: las claves de variantes retiradas se borran sin aviso de agotado y sin aviso de precio pendiente
    est_o = {"conocidos": {"Liverpool|ps5|disco": {"producto": "PS5 con lector de disco", "precio": 14699, "url": "u",
                                                   "visto": "h"}},
             "ausente": {"Liverpool|ps5|disco": 3}, "avisado": {"Amazon|ps5|sin-dato": 9000}}
    for _ in range(AUSENCIAS_AGOTADO + 1):
        assert disponibilidad(est_o, prods, {}, {"Liverpool", "Amazon"}, "h") == []
    assert not est_o["conocidos"] and not est_o["ausente"] and not est_o["avisado"], est_o
    # Liverpool titula "Consola PS5" a secas al PlayStation Portal ($5,999; aviso falso de GANGA, 24 nov 2026)
    assert oferta(ps, "Liverpool", "Consola PS5", 5999, "u", "Liverpool") is None
    assert variante(ps, "Consola PS5") is None and variante(ps, "Consola PlayStation 5") is None
    dig = "Consola PlayStation 5 - Edición Digital de 825 GB"
    assert oferta(ps, "Amazon", dig, 9500, "u", "GameStore MX") is None          # tercero: no entra ni avisa
    assert oferta(ps, "Amazon", dig, 9500, "u", "Sony") is None                  # en Amazon solo Amazon Mexico
    assert oferta(ps, "Amazon", dig, 9500, "u", "Amazon México")["precio"] == 9500
    assert oferta(ps, "Elektra", dig, 9500, "u", "Elektra") and oferta(ps, "Elektra", dig, 9500, "u", "Gamers MX") is None
    assert topes(ps, var_por_id(ps, "digital")) == (7500, 9000, 10800), topes(ps, var_por_id(ps, "digital"))
    # productos separados: la variante de cada clave del historial se encuentra en su producto
    assert clave_de(prods, "Amazon", "PS5 Digital (sin lector)") == "Amazon|ps5|digital"
    assert clave_de(prods, "Amazon", "Fold8 512 GB") == "Amazon|fold8|normal-512"
    # Gameplanet: titulos reales de su catalogo del 24 nov 2026 (precio de la ficha; vendedor leido del ld+json)
    casos_gp = [
        ("CONSOLA PLAYSTATION 5 DIGITAL FORTNITE FLOWERING", 10999.99, "Gameplanet", "digital"),
        ("CONSOLA PLAYSTATION 5 DIGITAL 30 ANIVERSARIO", 19999.99, "Gameplanet", "digital"),
        ("CONSOLA PLAYSTATION 5 SLIM", 11999.99, "Gameplanet", None),          # con lector: fuera (solo Digital)
        ("CONSOLA PLAYSTATION 5 BUNDLE GOD OF WAR RAGNAROK", 11999.99, "Gameplanet", None),
        ("CONSOLA PLAYSTATION 5", 5999.99, "Gameplanet", None),               # a secas: sin pista de consola completa
        ("CONSOLA PLAYSTATION 5 PRO DIGITAL 2TB", 9999.99, "Gameplanet", None),
        ("CONSOLA PLAYSTATION 5 PORTAL REMOTE", 6999.99, "Gameplanet", None),
        ("CONTROL PLAYSTATION 5 DUALSENSE EDICION LIMITADA GRAN TURISMO", 1699.99, "Gameplanet", None),
        ("CONSOLA PLAYSTATION 5 DIGITAL FORTNITE FLOWERING", 10999.99, "Vendedor Externo MX", None),
        ("CONSOLA PLAYSTATION 5 DIGITAL FORTNITE FLOWERING", 10999.99, "", None)]   # vendedor sin leer: no entra
    for titulo, precio, vend, esperado in casos_gp:
        o = oferta(ps, "Gameplanet", titulo, precio, "u", vend)
        assert (o["var"] if o else None) == esperado, (titulo, vend, o and o["var"], esperado)
    ficha_gp = ('<script type="application/ld+json" data-testid="product-jsonld">{"@context":"https://schema.org",'
                '"@type":"Product","name":"CONSOLA PLAYSTATION 5 DIGITAL FORTNITE FLOWERING","offers":[{"@type":"Offer",'
                '"price":"10999.99","priceCurrency":"MXN","itemCondition":"https://schema.org/NewCondition",'
                '"availability":"https://schema.org/InStock","sku":"711719022046","seller":{"@type":"Organization",'
                '"name":"Gameplanet"}}]}</script>')
    assert ficha_gameplanet(ficha_gp) == ("CONSOLA PLAYSTATION 5 DIGITAL FORTNITE FLOWERING",
                                          [(10999.99, "InStock", "NewCondition", "Gameplanet")]), ficha_gameplanet(ficha_gp)
    assert ficha_gameplanet("<html></html>") == ("", [])
    # cupones: textos reales del 25 nov 2026 (banners de samsung.com/mx y campo voucherCode del foro)
    assert decodificar("Obtén $1,000 con el cupón BF1000".encode("cp1252")) == "Obtén $1,000 con el cupón BF1000"
    assert decodificar("cupón".encode("utf-8")) == "cupón"
    assert cupones_en_texto("Obtén $1,000 de descuento con el cupón BF1000 en tu nuevo Galaxy S26 Ultra") == ["BF1000"]
    assert cupones_en_texto("Galaxy Tab S12+ 20% cupón ECO_20") == ["ECO_20"]
    assert cupones_en_texto("Recibe 15% de dto. con el cupón S26CRP_NOV") == ["S26CRP_NOV"]
    assert cupones_en_texto("Sin este producto, el cupón no puede canjearse; cupón de primera compra") == []
    casos_cup = [("BFLIVESEM + CRP26TV + CARRITO5", ["BFLIVESEM", "CRP26TV", "CARRITO5"]),
                 ("BFLIVESEM - CARRITO5 - PRO-20 - ePromoter", ["BFLIVESEM", "CARRITO5", "PRO-20"]),
                 ("CUPON ASESOR/ CARRITO5/ LIVESEM/ S26_CLUBJUK", ["CARRITO5", "LIVESEM", "S26_CLUBJUK"]),
                 ("EPP10_NOV + Cashback Santander a 6 MSI o más", ["EPP10_NOV"]),
                 ("FOLD8_20CLUB | BF1000 | ePromoter", ["FOLD8_20CLUB", "BF1000"]),
                 ("MONDAYSPECIALSLIVE, NAVIDAD-30, XMAS-FOLD30CLUB1", ["MONDAYSPECIALSLIVE", "NAVIDAD-30", "XMAS-FOLD30CLUB1"]),
                 ("Cupón del Asesor", [])]
    for txt, esperado in casos_cup:
        assert codigos_de(txt) == esperado, (txt, codigos_de(txt))
    hilo = ('"publishedAt":1794710067,"lastCommentAt":1794755671,"updatedAt":1794771602,"voucherCode":"FLASHBP25",'
            '"linkHost":"www.samsung.com","shareableLink":"x"')
    assert HILO_FORO.findall(hilo) == [("1794710067", "FLASHBP25", "www.samsung.com")]
    est_c = {"cupones_cosechados": {"NUEVO10": "2026-11-25", "VIEJO5": "2026-11-10"},
             "cupones_reprobados": {"fold8|VIEJO5": int(time.time())}}
    cand = cupones_candidatos(prods[0], est_c)
    assert "NUEVO10" in cand and "VIEJO5" not in cand, cand   # linea B (E6): la lista fija va primero; lo nuevo, detras
    # --- robustez (25 nov 2026)
    import io
    import contextlib
    import importlib.util
    import subprocess
    # pedir(): un corte de red se reintenta una vez; si sigue, RuntimeError con el host; HTTPError sigue dando su codigo
    class Abridor:
        def __init__(self, fallas, error):
            self.fallas, self.error, self.veces = fallas, error, 0

        def open(self, req, timeout=None):
            self.veces += 1
            if self.veces <= self.fallas:
                raise self.error
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b"nada"))
    for err in (urllib.error.URLError("dns caido"), TimeoutError("tope"), ConnectionResetError("cortada")):
        a = Abridor(1, err)
        assert pedir("https://x.example/a", abridor=a, reintento=0) == (404, "nada") and a.veces == 2, (err, a.veces)
        a = Abridor(2, err)
        try:
            pedir("https://x.example/a", abridor=a, reintento=0)
            raise AssertionError("pedir no lanzo tras dos cortes")
        except RuntimeError as ex:
            assert str(ex).startswith("sin respuesta: x.example: ") and a.veces == 2, ex
    a = Abridor(0, None)
    assert pedir("https://x.example/a", abridor=a, reintento=0) == (404, "nada") and a.veces == 1
    # linea B: intentos=1 abre una sola vez y lanza al primer corte
    a = Abridor(1, TimeoutError("tope"))
    try:
        pedir("https://x.example/a", abridor=a, reintento=0, intentos=1)
        raise AssertionError("pedir con intentos=1 no lanzo")
    except RuntimeError:
        assert a.veces == 1, a.veces
    # avisos de disponibilidad: si el aviso no se abre, la proxima corrida lo vuelve a intentar
    def falla(asunto, cuerpo):
        raise RuntimeError("GitHub no responde")
    abiertos = []
    est_d = {"conocidos": {"Samsung|fold8|ultra-512": {"producto": "Fold8 Ultra 512 GB", "precio": 34199.2, "url": "u",
                                                       "visto": "h"}}}
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(AUSENCIAS_AGOTADO):
            avisar_disponibilidad(est_d, prods, {}, {"Samsung"}, "h", falla)
        assert not est_d["agotados"] and est_d["ausente"]["Samsung|fold8|ultra-512"] == AUSENCIAS_AGOTADO - 1, est_d
        av = avisar_disponibilidad(est_d, prods, {}, {"Samsung"}, "h", lambda a, c: abiertos.append(a))
        assert len(av) == 1 and abiertos == [av[0][0]] and "Samsung|fold8|ultra-512" in est_d["agotados"], est_d
        assert avisar_disponibilidad(est_d, prods, vuelta, {"Samsung"}, "h", falla)[0][0].startswith("Volvio")
        assert "Samsung|fold8|ultra-512" in est_d["agotados"], est_d                 # fallo: sigue agotado
        av = avisar_disponibilidad(est_d, prods, vuelta, {"Samsung"}, "h", lambda a, c: abiertos.append(a))
        assert av[0][0].startswith("Volvio") and not est_d["agotados"] and len(abiertos) == 2, est_d
    # recorte del foro: se quedan los mas recientes por fecha, no por orden alfabetico del guid
    vis = {"a-viejo": "Mon, 02 Nov 2026 10:00:00 -0600", "z-nuevo": "Wed, 25 Nov 2026 10:00:00 -0600",
           "m-medio": "Fri, 20 Nov 2026 10:00:00 -0600", "b-iso": "2026-11-24T09:00:00+00:00", "c-roto": "sin fecha"}
    assert list(recortar_vistos(vis, 3)) == ["z-nuevo", "b-iso", "m-medio"], recortar_vistos(vis, 3)
    # reloj general: vencido solo pasado SEGUNDOS_CORRIDA
    assert not reloj_vencido(100.0, 100.0 + SEGUNDOS_CORRIDA) and reloj_vencido(100.0, 101.0 + SEGUNDOS_CORRIDA)
    # --cobertura: corre sin red y nombra cada variante, sus topes y las tiendas
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        assert cobertura(prods) == 0
    texto = salida.getvalue()
    for x in prods:
        for v in x["variantes"]:
            assert v["nombre"] in texto and "{:,}".format(topes(x, v)[0]) in texto, v["nombre"]
    assert "Grafito" in texto and "foro" in texto and "Gameplanet" in texto, texto[:300]
    # pruebas\casos_*.py con correr(v), en orden alfabetico; durante ellas la red y las pausas quedan prohibidas
    def sin_red(*a, **k):
        raise AssertionError("una prueba quiso usar la red o dormir")
    yo = sys.modules[__name__]
    guardados = (yo.pedir, urllib.request.urlopen, time.sleep)
    carpeta = os.path.join(AQUI, "pruebas")
    corridos = []
    try:
        yo.pedir = urllib.request.urlopen = time.sleep = sin_red
        for nombre in sorted(os.listdir(carpeta)) if os.path.isdir(carpeta) else []:
            if not (nombre.startswith("casos_") and nombre.endswith(".py")):
                continue
            # las pruebas de lectores nuevos ya corren al arrancar y, si fallan, apagan solo esos lectores; en la nube
            # (SELFTEST_SOLO_NUCLEO=1) no se dejan aqui, porque un fallo de este paso apaga todos los avisos de precio
            if nombre == "casos_lectores_nuevos.py" and os.environ.get("SELFTEST_SOLO_NUCLEO") == "1":
                continue
            spec = importlib.util.spec_from_file_location("prueba_" + nombre[:-3], os.path.join(carpeta, nombre))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "correr"):
                mod.correr(yo)
                corridos.append(nombre)
        # (R5-H1) con los lectores nuevos dados de baja (archivo ausente, registrar roto o su prueba fallida), los casos
        # de titulos del nucleo siguen pasando: saltan, diciendolo, los de tiendas sin lector o sin regla de vendedor
        ruta_t = os.path.join(carpeta, "casos_titulos.py")
        if _SOLO_NUCLEO and os.path.exists(ruta_t):
            ahora_d = [(d, dict(d)) for d, _ in _SOLO_NUCLEO]
            try:
                for d, copia in _SOLO_NUCLEO:
                    d.clear()
                    d.update(copia)
                spec = importlib.util.spec_from_file_location("prueba_casos_titulos_sin_nuevos", ruta_t)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                salida_t = io.StringIO()
                with contextlib.redirect_stdout(salida_t):
                    mod.correr(yo)
                nuevas = sorted({t for d, c in ahora_d if d is LECTORES for t in c} - set(LECTORES))
                if nuevas:
                    assert "saltados" in salida_t.getvalue(), "casos_titulos no dijo que salto tiendas sin lector"
            finally:
                for d, copia in ahora_d:
                    d.clear()
                    d.update(copia)
            corridos.append("casos_titulos.py sin lectores nuevos")
    finally:
        yo.pedir, urllib.request.urlopen, time.sleep = guardados
    _prueba_corte_ambiguo()
    corridos.append("aviso con respuesta cortada")
    # modulos de las lineas A y B (si existen junto a vigia.py), cada uno en su propio proceso
    if os.environ.get("SELFTEST_SOLO_NUCLEO") != "1":
        for mod in ("prospectiva.py", "promos.py"):
            ruta = os.path.join(AQUI, mod)
            if not os.path.exists(ruta):
                continue
            try:
                r = subprocess.run([sys.executable, "-X", "utf8", ruta, "--selftest"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60, cwd=AQUI)
            except subprocess.TimeoutExpired:
                raise AssertionError("%s --selftest no termino en 60 s" % mod)
            assert r.returncode == 0, "%s --selftest fallo (codigo %s): %s" % (mod, r.returncode,
                                                                             (r.stdout + r.stderr)[-600:])
            corridos.append(mod)
    print("selftest OK: %d titulos Fold8, %d PS5 y %d Gameplanet, topes, vendedor oficial, importacion y agotado/regreso"
          % (len(casos), len(casos_ps5), len(casos_gp)))
    print("selftest OK: red cortada, avisos de disponibilidad que fallan, recorte del foro, reloj, --cobertura; "
          "pruebas aparte: %s" % (", ".join(corridos) or "ninguna"))
    return 0


def main():
    try:
        return _main()
    finally:
        _ARRANQUE[0] = None   # el tope duro solo vale dentro de una corrida


def _main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--cobertura" in sys.argv:
        return cobertura()
    seco = "--seco" in sys.argv
    if not es_principal(CONFIG) and not seco:
        print("VIGIA_CONFIG=%s: con otro archivo de productos solo se corre --seco o --cobertura (no se escribe estado "
              "ni se avisa). Quita la variable para la corrida normal." % CONFIG)
        return 3
    prueba = os.environ.get("PRUEBA", "").lower() in ("1", "true", "yes")
    ahora = dt.datetime.now(dt.timezone.utc)
    arranque = reloj()   # reloj general de la corrida (SEGUNDOS_CORRIDA) y del tope duro (SEGUNDOS_DUROS)
    _ARRANQUE[0] = arranque
    LIVERPOOL_LEIDAS.clear()
    prods = cargar_config(CONFIG)
    por_id = {p["id"]: p for p in prods}
    try:
        with open(ESTADO, encoding="utf-8") as f:
            estado = json.load(f)
    except Exception:
        estado = {}
    if not es_principal(CONFIG):   # archivo de muestra: no se mezcla con la memoria de los productos vigilados
        estado = {}
    antes = json.dumps(estado, sort_keys=True)
    fallos = estado.setdefault("fallos_fuente", {})
    avisado, avisado_nivel = normalizar_avisado(estado)   # avisado: solo numeros (vuelta atras segura)
    vistos = estado.setdefault("vistos", {})

    caidas, avisadas = estado.setdefault("caida_desde", {}), estado.setdefault("caida_avisada", [])

    def fuente(nombre, funcion):
        try:
            r = funcion()
            if nombre in avisadas and not seco:
                try:
                    abrir_issue("El vigia volvio a leer %s" % nombre, "La fuente %s responde de nuevo (sin leerse desde %s UTC)."
                                % (nombre, caidas.get(nombre, "?")))
                    avisadas.remove(nombre)
                except Exception as ex2:
                    print("   no se pudo abrir el aviso:", ex2)
            caidas.pop(nombre, None)
            fallos[nombre] = 0
            estado.setdefault("ultimo_error", {}).pop(nombre, None)
            print("fuente ok    %-10s %d" % (nombre, len(r)))
            return r
        except Exception as ex:
            fallos[nombre] = fallos.get(nombre, 0) + 1
            estado.setdefault("ultimo_error", {})[nombre] = str(ex)[:160]   # queda en visto.json para diagnosticar
            desde = caidas.setdefault(nombre, ahora.isoformat(timespec="minutes"))
            horas = (ahora - dt.datetime.fromisoformat(desde)).total_seconds() / 3600
            print("fuente FALLA %-10s (%d seguidas, %.1f h sin leer) %s" % (nombre, fallos[nombre], horas, str(ex)[:150]))
            limite = HORAS_SIN_LEER.get(nombre, HORAS_SIN_LEER_DEFECTO)
            if horas >= limite and nombre not in avisadas and not seco:
                try:
                    abrir_issue("El vigia no puede leer %s desde hace %d horas" % (nombre, int(horas)),
                                "Ultimo error: %s\n\nCorridas seguidas con falla: %d. Mientras siga asi la tabla conserva el ultimo "
                                "precio leido de esa fuente; las demas siguen vigiladas." % (str(ex)[:300], fallos[nombre]))
                    avisadas.append(nombre)
                except Exception as ex2:
                    print("   no se pudo abrir el aviso:", ex2)
            return None

    foro = fuente("Foro", lambda: leer_foro(prods, estado))
    # Busqueda de cupones antes de las tiendas: lo hallado se prueba en el carrito de Samsung en esta misma corrida.
    if any("Samsung" in p["tiendas"] for p in prods):
        fuente("Cupones", lambda: cosechar_cupones(prods, estado, ahora))
    ofertas, leidas = [], set()
    for tienda, lector in LECTORES.items():
        usan = [p for p in prods if tienda in p["tiendas"]]
        if not usan:
            continue
        if reloj_vencido(arranque):   # se trata como "no leida esta corrida": ni leidas, ni ausencias, ni falla
            print("tienda saltada %-10s se acabo el tiempo de la corrida (%d s): no se lee esta vez"
                  % (tienda, SEGUNDOS_CORRIDA))
            continue

        def todas(lector=lector, usan=usan, tienda=tienda):
            r = []
            for p in usan:
                r += lector(p, p["tiendas"][tienda], estado)
            return r
        res = fuente(tienda, todas)
        if res is not None:
            leidas.add(tienda)
            ofertas += res
    for tienda in tiendas_sin_lector(prods):   # configurada y sin lector: falla visible, con su aviso a las horas

        def sin_lector(tienda=tienda):
            raise RuntimeError("lector no cargado: %s" % (LECTORES_NUEVOS_ERROR or "ninguna funcion lee %s" % tienda))
        fuente(tienda, sin_lector)

    if promos and not os.environ.get("SIN_PROMOS"):
        try:
            promos.mejorar(ofertas, estado, prods, ahora, seco, pedir, abrir_issue)
        except Exception as ex:
            print("promos: se omite (%s)" % str(ex)[:120])

    mejores = {}
    for o in ofertas:
        clave = "%s|%s|%s" % (o["tienda"], o["prod"], o["var"])
        if clave not in mejores or o["precio"] < mejores[clave]["precio"]:
            mejores[clave] = o
    for clave, o in sorted(mejores.items()):
        print("  %-34s $%9.0f  %-13s %s | %s" % (clave, o["precio"], nivel(o["precio"], por_id[o["prod"]], var_por_id(por_id[o["prod"]], o["var"])) or "", o["vendedor"], o["nota"]))

    # --- avisos por precio leido en tienda: GANGA, COMPRAR y SIN CONFIRMAR releen antes su ficha (confirmar)
    # Cada bloque de avisos va en su propio try: uno roto no tapa a los demas ni impide escribir visto.json y precios.json.
    sin_avisos = os.environ.get("SIN_AVISOS_PRECIO", "").lower() in ("1", "true", "yes")
    fichas = {}   # clave -> que dijo la ficha en esta corrida (para el aviso de "volvio a la venta")
    try:
        hechos = avisos_de_precio(mejores, por_id, avisado, estado, ahora, arranque)
    except Exception as ex:
        print("   los avisos de precio fallaron:", str(ex)[:200])
        hechos = []
    for av in hechos:
        try:
            print(" * AVISO", av["asunto"], "|", av["resultado"] or "sin releer (mirar)", av["razon"])
            salio = publicar_aviso(av, avisado, seco, sin_avisos, niveles=avisado_nivel)
            fichas[av["clave"]] = {"resultado": av["resultado"], "nivel": av["nivel"], "razon": av["razon"], "salio": salio}
        except Exception as ex:
            print("   aviso de precio roto:", str(ex)[:200])

    # --- avisos de modelo agotado o de vuelta a la venta (solo cuenta tiendas leidas en esta corrida)
    try:
        if "conocidos" not in estado:
            sembrar_conocidos(estado, prods, mejores, leidas)
            print("   disponibilidad sembrada; agotados de arranque:", ", ".join(estado["agotados"]) or "ninguno")
        avisar_disponibilidad(estado, prods, mejores, leidas, ahora.astimezone(MX).strftime("%Y-%m-%d %H:%M"),
                              None if seco else abrir_issue, callar=sin_avisos, fichas=fichas)
    except Exception as ex:
        print("   los avisos de disponibilidad fallaron:", str(ex)[:200])

    # --- avisos del foro (tiendas que no se dejan leer, y recetas con banco)
    sembrar = not estado.get("sembrado")
    for it in foro or []:
        try:
            av = aviso_foro(prods, it)
            if not av or it["guid"] in vistos:
                continue
            try:
                fresco = (ahora - email.utils.parsedate_to_datetime(it["fecha"])) <= dt.timedelta(days=DIAS_FRESCO)
            except Exception:
                fresco = True
            if not fresco:
                continue
            print(" * FORO", av[0])
            if seco:
                continue
            if sin_avisos:   # ni se abre ni se marca como visto: saldra cuando vuelvan los avisos, si sigue fresca
                print("   (SIN_AVISOS_PRECIO=1: se habria avisado; no se abre ni se marca)")
                continue
            if not sembrar:
                if abrir_issue(av[0], av[1]) is False:   # sin credenciales: no salio, no se marca
                    continue
            vistos[it["guid"]] = it["fecha"]
        except Exception as ex:
            print("   aviso del foro roto:", str(ex)[:200])
    if foro is not None and not seco and not sin_avisos:
        estado["sembrado"] = True

    if prueba and not seco:
        lineas = []
        for p in prods:
            for v in p["variantes"]:
                lineas.append("{}: ganga ${:,}, comprar ${:,}, mirar ${:,}".format(v["nombre"], *topes(p, v)))
        abrir_issue("PRUEBA del vigia: precios leidos ahora directamente en las tiendas",
                    "Esto es una prueba de que el vigia lee las tiendas y de que el aviso te llega.\n\n" + tabla(mejores)
                    + "\n\nFuentes caidas en esta corrida: " + (", ".join(n for n, v in fallos.items() if v) or "ninguna")
                    + "\n\nCupones hallados en esta busqueda: " + (", ".join(
                        "%s (%s)" % kv for kv in sorted(estado.get("cupones_ultima_busqueda", {}).get("hallados", {}).items()))
                        or "ninguno")
                    + "\n\nTopes:\n- " + "\n- ".join(lineas))

    if seco:
        return 0
    # --- bitacora de precios (solo se reescribe cuando algo cambia)
    try:
        with open(PRECIOS, encoding="utf-8") as f:
            previos = json.load(f)
    except Exception:
        previos = {}
    # lo que una tienda ya no ofrece (o ya no cumple los requisitos) sale de la tabla; si la tienda no se
    # pudo leer en esta corrida, se conserva su ultimo dato
    actuales = {k: v for k, v in previos.items() if k.split("|")[0] not in leidas}
    hoy = ahora.astimezone(MX).strftime("%Y-%m-%d %H:%M")
    cambios = []
    for clave, o in mejores.items():
        ant = previos.get(clave, {}).get("precio")
        if ant != o["precio"]:
            cambios.append([hoy, o["tienda"], o["nombre"], "%.2f" % o["precio"], o["vendedor"], o["nota"], o["url"]])
        actuales[clave] = {"producto": o["nombre"], "precio": o["precio"], "vendedor": o["vendedor"], "nota": o["nota"],
                           "url": o["url"], "leido": hoy if ant != o["precio"] else previos.get(clave, {}).get("leido", hoy)}
    if cambios:
        nuevo = not os.path.exists(HISTORIAL)
        with open(HISTORIAL, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(["fecha", "tienda", "producto", "precio", "vendedor", "como", "liga"])
            w.writerows(cambios)
    if cambios or set(actuales) != set(previos):
        with open(PRECIOS, "w", encoding="utf-8") as f:
            json.dump(actuales, f, ensure_ascii=False, indent=1, sort_keys=True)
    if len(vistos) > 600:
        estado["vistos"] = recortar_vistos(vistos, 400)
    if json.dumps(estado, sort_keys=True) != antes:
        with open(ESTADO, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
