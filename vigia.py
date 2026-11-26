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

AQUI = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(AQUI, "visto.json")
PRECIOS = os.path.join(AQUI, "precios.json")
HISTORIAL = os.path.join(AQUI, "historial_precios.csv")
CONFIG = os.path.join(AQUI, "productos.json")
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
           r"importad[oa]|versi[oó]n\s+(internacional|global|china|usa)|international|global\s+version)\b")
# Quien cuenta como vendedor oficial en cada tienda cuando el producto pide "solo_vendedor_oficial".
# {marca} se cambia por la marca del producto. Tienda que no aparece aqui = la tienda vende ella misma.
VENDEDOR_OFICIAL = {
    "Amazon": r"^\s*(amazon(\s+m[eé]xico)?|{marca}(\s+m[eé]xico)?)\s*$",   # no terceros ni "Remates de Almacen"
    "Elektra": r"^\s*elektra\s*$",                                        # no vendedores del marketplace
    "Gameplanet": r"^\s*game\s*planet\s*$",                               # vendedor leido de la ficha (ld+json)
}
SENAL_IMPORTADO = r"importaci[oó]n|desde Estados Unidos|Amazon Estados Unidos|Amazon Global|Env[ií]o internacional"
# En el foro solo se atienden tiendas establecidas en Mexico (no importadores ni revendedores).
TIENDAS_FORO = (r"samsung|walmart|bodega|sam'?s|amazon|mercado\s*libre|liverpool|suburbia|coppel|costco|elektra|sears|"
                r"sanborns|palacio|telcel|at&t|office\s*depot|best\s*buy|claro\s*shop|chedraui|soriana|heb|doto|huawei|"
                r"lenovo|apple|xiaomi|motorola|sony|lg\b|hp\b|dell")
BANCOS_PROPIOS = {"BBVA": r"bbva|bancomer", "Revolut": r"revolut", "PayPal": r"paypal"}
BANCOS_AJENOS = {"Banorte": r"banorte", "Santander": r"santander", "Banamex": r"banamex|citibanamex",
                 "HSBC": r"hsbc", "Scotiabank": r"scotiabank", "Amex": r"amex|american\s*express",
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
TOPE_CUPONES = 40        # codigos probados por corrida y producto (los recien hallados primero)
SEGUNDOS_CARRITO = 90    # tope de tiempo de prueba de cupones por modelo (el trabajo de GitHub dura maximo 12 min)
# Codigos de tienda cerrada (empleados o estudiantes: llevan "EPP"). En el carrito normal se rechazaron 24 de 24 el
# 25 nov 2026 (8 codigos x 3 modelos). Se siguen cosechando (quedan en visto.json), pero no se prueban en el carrito.
MARCA_TIENDA_CERRADA = "EPP"
NO_ES_CUPON = re.compile(r"^(SAMSUNG|GALAXY|ULTRA|FOLD\d?|FLIP\d?|MSI|IVA|MXN|BBVA|PAYPAL|SANTANDER|BANORTE|"
                         r"CUPON|CODIGO|ASESOR|EPROMOTER|DESCUENTO|GRATIS|ENVIO)$")


# ------------------------------------------------------------------------------- utilidades
def pedir(url, datos=None, metodo=None, abridor=None, json_api=False, pausa=0.0):
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
        time.sleep(pausa)
    try:
        with abre(req, timeout=30) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                b = gzip.decompress(b)
            return r.status, decodificar(b, r.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as e:
        b = e.read() or b""
        try:
            b = gzip.decompress(b)
        except Exception:
            pass
        return e.code, decodificar(b, e.headers.get("Content-Type", "") if e.headers else "")


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


def cargar_config(ruta=CONFIG):
    with open(ruta, encoding="utf-8") as f:
        prods = json.load(f)["productos"]
    for p in prods:  # validacion: un producto mal escrito aborta con mensaje claro, no vigila a medias
        for campo in ("id", "nombre", "patron", "piso", "fracciones", "variantes", "tiendas", "requisitos"):
            if campo not in p:
                raise SystemExit("productos.json: al producto %s le falta '%s'" % (p.get("id", "?"), campo))
        for v in p["variantes"]:
            if "id" not in v or "nombre" not in v or "lista" not in v:
                raise SystemExit("productos.json: variante incompleta en %s: %s" % (p["id"], v))
        re.compile(p["patron"])
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


def topes(prod, var):
    fijos = prod.get("topes_fijos", {}).get(var["id"])
    if fijos:
        return tuple(fijos)
    return tuple(int(round(var["lista"] * f / 100.0)) * 100 for f in prod["fracciones"])


def nivel(precio, prod, var):
    g, c, t = topes(prod, var)
    return "GANGA" if precio <= g else "COMPRAR" if precio <= c else "MIRAR" if precio <= t else None


def marca_ok(prod, titulo):
    """En Amazon el titulo debe nombrar la marca (deja fuera accesorios de otras marcas). Un producto cuyo titulo
    no suele traer la marca (PS5: "Consola PlayStation 5" sin "Sony") la da en marca_titulo."""
    regla = prod.get("marca_titulo", prod.get("marca"))
    return not regla or bool(re.search(regla, titulo, re.I))


def vendedor_oficial(prod, tienda, vendedor):
    # el producto puede fijar su propia regla por tienda (PS5: en Amazon solo Amazon Mexico; Sony no vende directo)
    regla = prod["requisitos"].get("vendedor_oficial", {}).get(tienda) or VENDEDOR_OFICIAL.get(tienda)
    if not regla:
        return True
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
        for info in sam["skus"].values():
            if len(info) > 2 and info[2] not in paginas:
                paginas.append(info[2])
        for b in sam.get("cupones_foro", [p["nombre"]]):
            if b not in busquedas:
                busquedas.append(b)
    hallados, leidas = {}, 0
    for u in paginas:
        try:
            e, t = pedir(u)
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
            e, t = pedir("https://www.promodescuentos.com/search?q=" + urllib.parse.quote(b) + "&sortBy=new")
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
    if not leidas:
        raise RuntimeError("ninguna fuente de cupones respondio")
    hoy = ahora.astimezone(MX).strftime("%Y-%m-%d")
    for c in hallados:
        cosecha[c] = hoy
    limite = (ahora.astimezone(MX) - dt.timedelta(days=DIAS_CUPON)).strftime("%Y-%m-%d")
    estado["cupones_cosechados"] = {c: f for c, f in cosecha.items() if f >= limite}
    estado["cupones_ultima_busqueda"] = {"cuando": ahora.astimezone(MX).strftime("%Y-%m-%d %H:%M"),
                                         "fuentes_leidas": leidas, "hallados": hallados}
    return sorted(hallados)


def cupones_candidatos(prod, estado):
    """Primero lo recien hallado (lo mas fresco), luego la lista fija; fuera los reprobados de las ultimas horas."""
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
    salida = []
    for c in sorted(cosecha, key=lambda k: cosecha[k], reverse=True) + lista:
        if c in salida or MARCA_TIENDA_CERRADA in c.upper() or time.time() - reprobados.get("%s|%s" % (prod["id"], c), 0) < HORAS_REPROBADO * 3600:
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
            nota = "precio de pagina, cupones sin probar (%s)" % str(ex)[:60]
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


def _leer_amazon(prod, cfg, estado, diag):
    memo = estado.setdefault("vendedor_amazon", {})
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
                nota, final = ("con cupon en la ficha" if re.search(r"cup[oó]n", bloque, re.I) else ""), precio
                if promos and nota:
                    try:   # promos.py resta el cupon de ficha si la busqueda da su cifra y cuadra; si no, como antes
                        final, n = promos.amazon_precio(precio, promos.amazon_busqueda(bloque))
                        nota, final = (n or nota), (final if final is not None and precio * 0.5 <= final <= precio else precio)
                    except Exception:
                        final = precio
                candidatas.append((m.group(1), titulo, precio, nota, final))
    salida = []
    if not fallo:
        # Quien vende y si viene de fuera solo se ve en la ficha. Se recuerda por articulo y precio.
        for asin, titulo, precio, nota, final in candidatas:
            url = "https://www.amazon.com.mx/dp/%s?th=1&psc=1" % asin
            previo = memo.get(asin)
            if previo and previo[0] == precio:
                vend, importado = previo[1], bool(previo[2]) if len(previo) > 2 else False
            else:
                e, t = pedir(url, pausa=1.0)
                diag.append("ficha %s %s" % (asin, amazon_rechazo(e, t, 'id="productTitle"') or "ok"))
                _, _, vend, importado = ficha_amazon(t)
            if vend:
                memo[asin] = [precio, vend, importado, prod["id"]]
            of = oferta(prod, "Amazon", titulo, final, url, vend or "sin confirmar", nota, importado=importado)
            if of and final < precio:
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
        titulo, precio, vend, importado = ficha_amazon(t)
        if precio is None or not marca_ok(prod, titulo):
            continue   # agotado o sin precio a la vista: hoy no se ofrece
        if vend:
            memo[asin] = [precio, vend, importado, prod["id"]]
        nota, final = "", precio
        if promos:
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


def leer_costco(prod, cfg, estado):
    e, t = pedir("https://www.costco.com.mx/rest/v2/mexico/products/search?query=%s&fields=FULL&lang=es_MX&curr=MXN"
                 % urllib.parse.quote(cfg["consulta"]))
    if e != 200:
        raise RuntimeError("Costco respondio %s" % e)
    salida = []
    for p in json.loads(t).get("products", []):
        base = (p.get("basePrice") or p.get("price") or {}).get("value")
        if base is None or (p.get("stock") or {}).get("stockLevelStatus") == "outOfStock":
            continue
        cup = (p.get("couponDiscount") or {})
        precio = base - (cup.get("discountValue") or 0)
        nota = "descuento de tienda ${:,.0f} hasta {}".format(cup["discountValue"], (cup.get("discountEndDate") or "")[:10]) if cup.get("discountValue") else ""
        of = oferta(prod, "Costco", p.get("name", ""), precio, "https://www.costco.com.mx" + p.get("url", ""), "Costco (pide membresia)", nota)
        if of:
            salida.append(of)
    return salida


def leer_elektra(prod, cfg, estado):
    e, t = pedir("https://www.elektra.mx/api/catalog_system/pub/products/search?ft=%s&_from=0&_to=49" % urllib.parse.quote(cfg["consulta"]))
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


def leer_liverpool(prod, cfg, estado):
    e, t = pedir("https://www.liverpool.com.mx/tienda?s=" + cfg["consulta"])
    if e != 200 or "-price" not in t:
        raise RuntimeError("Liverpool respondio %s (%d bytes)" % (e, len(t)))
    salida = []
    for m in re.finditer(r'<h3[^>]*>([^<]*)</h3><div[^>]*data-testid="(\d+)-price">(.{0,700}?)</div>', t, re.S):
        pr = re.search(r'data-testid="discounted".{0,200}?\$(?:<!-- -->)?([\d,]+)', m.group(3), re.S) or \
             re.search(r"\$(?:<!-- -->)?([\d,]+)", m.group(3))
        if not pr:
            continue
        of = oferta(prod, "Liverpool", html.unescape(m.group(1)), float(pr.group(1).replace(",", "")),
                    "https://www.liverpool.com.mx/tienda/pdp/p/" + m.group(2), "Liverpool",
                    "es el precio mas bajo de sus variantes")
        if of:
            salida.append(of)
    return salida


GAMEPLANET = "https://www.gameplanet.com"
GP_EN_VENTA = {"InStock": "", "LimitedAvailability": "pocas piezas", "PreOrder": "preventa"}


def ficha_gameplanet(t):
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
    nota, me_aplica = bancos(it["titulo"] + " " + limpio(it["descripcion"], 3000))
    niv = "SIN PRECIO, REVISAR" if precio is None else nivel(precio, prod, var) if me_aplica else "SOLO CON OTRO BANCO"
    ptxt = "${:,.0f}".format(precio) if precio is not None else "sin precio"
    g, c, t = topes(prod, var)
    asunto = "[foro] {} {} en {} · {} · {}".format(var["nombre"], ptxt, it["tienda"] or "tienda", niv, nota)
    cuerpo = "\n".join([
        "**{}**".format(it["titulo"]), "",
        "Fuente: publicacion de un usuario en el foro de ofertas (no leida en la tienda; esta tienda no deja leer sus precios a programas).",
        "", "- Precio publicado: **{}** en {}".format(ptxt, it["tienda"] or "(sin dato)"),
        "- Veredicto: **{}** ({})".format(niv, nota),
        "- Topes de {}: ganga ${:,}; comprar ${:,}; mirar ${:,}".format(var["nombre"], g, c, t),
        "- Publicada: {}".format(it["fecha"]), "- Oferta: {}".format(it["link"]), "",
        "> " + limpio(it["descripcion"]).replace("\n", "\n> "), "",
        "Antes de pagar: confirma en la tienda que siga viva y que la venda la marca o la propia tienda, con garantia en "
        "Mexico. Si la vende un tercero o es importado, revisa si cumple lo que pediste para este producto."])
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
    with urllib.request.urlopen(req, timeout=30) as r:
        print("   aviso abierto:", json.loads(r.read()).get("html_url"))
    return True


def tabla(mejores):
    filas = ["| Producto | Tienda | Precio | Vendedor | Como |", "|---|---|---|---|---|"]
    for o in sorted(mejores.values(), key=lambda o: (o["prod"], o["nombre"], o["precio"])):
        filas.append("| {} | [{}]({}) | ${:,.0f} | {} | {} |".format(
            o["nombre"], o["tienda"], o["url"], o["precio"], o["vendedor"] or "", o["nota"] or ""))
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


def disponibilidad(estado, prods, mejores, leidas, hoy):
    """Devuelve avisos [(asunto, cuerpo)] de modelos agotados o de vuelta, y actualiza el estado."""
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
    for c, o in mejores.items():
        conocidos[c] = {"producto": o["nombre"], "precio": o["precio"], "url": o["url"], "visto": hoy}
        ausente.pop(c, None)
        if c in agotados:
            prod = por_id[o["prod"]]
            var = var_por_id(prod, o["var"])
            g, cc, t = topes(prod, var)
            niv = nivel(o["precio"], prod, var) or "arriba de tus topes"
            avisos.append((
                "Volvio a la venta: {} en {} a ${:,.0f} · {}".format(o["nombre"], o["tienda"], o["precio"], niv),
                "\n".join([
                    "**{}** estaba agotado en {} ({}; ultimo precio ${:,.0f}) y ya se puede comprar otra vez.".format(
                        o["nombre"], o["tienda"], agotados[c].get("desde", "?"), agotados[c].get("precio", 0)), "",
                    "- Precio leido ahora: **${:,.0f}**".format(o["precio"]),
                    "- Como se llega: {}".format(o["nota"] or "precio directo, sin cupon"),
                    "- Quien vende: {}".format(o["vendedor"] or "sin dato"),
                    "- Veredicto: **{}**. Topes de {}: ganga ${:,}; comprar ${:,}; mirar ${:,}".format(niv, var["nombre"], g, cc, t),
                    "- Liga: {}".format(o["url"]), "",
                    "Lo mejor de cada tienda en este momento:", "", tabla(mejores)])))
            agotados.pop(c)
    for c, info in conocidos.items():
        if c in mejores or c.split("|")[0] not in leidas or c in agotados:
            continue
        ausente[c] = ausente.get(c, 0) + 1
        if ausente[c] >= AUSENCIAS_AGOTADO:
            tienda = c.split("|")[0]
            avisos.append((
                "Agotado: {} ya no esta a la venta en {}".format(info["producto"], tienda),
                "\n".join([
                    "{} no aparece a la venta en {} desde hace {} corridas seguidas (cerca de una hora), aunque la tienda "
                    "si se leyo.".format(info["producto"], tienda, ausente[c]), "",
                    "- Ultima vez a la venta: {} a ${:,.0f}".format(info["visto"], info["precio"]),
                    "- Liga: {}".format(info["url"]), "",
                    "Te aviso en cuanto vuelva a estar a la venta, con su precio."])))
            agotados[c] = {"desde": "desde " + info["visto"], "precio": info["precio"]}
            ausente.pop(c)
    return avisos


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
    assert topes(p, var_por_id(p, "ultra-512")) == (22000, 24000, 26000), topes(p, var_por_id(p, "ultra-512"))
    assert topes(p, var_por_id(p, "normal-512")) == (19900, 21800, 23600), topes(p, var_por_id(p, "normal-512"))
    assert nivel(23999, p, var_por_id(p, "ultra-512")) == "COMPRAR" and nivel(23999, p, var_por_id(p, "normal-512")) is None
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
    assert cand[0] == "NUEVO10" and "VIEJO5" not in cand, cand
    print("selftest OK: %d titulos Fold8, %d PS5 y %d Gameplanet, topes, vendedor oficial, importacion y agotado/regreso"
          % (len(casos), len(casos_ps5), len(casos_gp)))
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    seco = "--seco" in sys.argv
    prueba = os.environ.get("PRUEBA", "").lower() in ("1", "true", "yes")
    ahora = dt.datetime.now(dt.timezone.utc)
    prods = cargar_config()
    por_id = {p["id"]: p for p in prods}
    try:
        with open(ESTADO, encoding="utf-8") as f:
            estado = json.load(f)
    except Exception:
        estado = {}
    antes = json.dumps(estado, sort_keys=True)
    fallos, avisado = estado.setdefault("fallos_fuente", {}), estado.setdefault("avisado", {})
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

        def todas(lector=lector, usan=usan, tienda=tienda):
            r = []
            for p in usan:
                r += lector(p, p["tiendas"][tienda], estado)
            return r
        res = fuente(tienda, todas)
        if res is not None:
            leidas.add(tienda)
            ofertas += res

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
        print("  %-34s $%9.0f  %-7s %s | %s" % (clave, o["precio"], nivel(o["precio"], por_id[o["prod"]], var_por_id(por_id[o["prod"]], o["var"])) or "", o["vendedor"], o["nota"]))

    # --- avisos por precio leido en tienda
    for clave, o in mejores.items():
        prod = por_id[o["prod"]]
        var = var_por_id(prod, o["var"])
        niv = nivel(o["precio"], prod, var)
        if not niv:
            avisado.pop(clave, None)   # subio del tope: la proxima baja vuelve a avisar
            continue
        if clave in avisado and o["precio"] > avisado[clave] - BAJA_MINIMA:
            continue
        g, c, t = topes(prod, var)
        asunto = "{} ${:,.0f} en {} · {} · leido en la tienda".format(var["nombre"], o["precio"], o["tienda"], niv)
        cuerpo = "\n".join([
            "**{}**".format(o["titulo"]), "",
            "- Precio leido ahora en la tienda: **${:,.0f}** ({})".format(o["precio"], o["tienda"]),
            "- Como se llega: {}".format(o["nota"] or "precio directo, sin cupon"),
            "- Quien vende: {}".format(o["vendedor"] or "sin dato"),
            "- Veredicto: **{}**. Topes de {}: ganga ${:,}; comprar ${:,}; mirar ${:,}".format(niv, var["nombre"], g, c, t),
            "- Liga: {}".format(o["url"]), "",
            "Lo mejor de cada tienda en este momento:", "", tabla(mejores), "",
            "No incluye devoluciones de banco (se ven hasta pagar)."])
        print(" * AVISO", asunto)
        if not seco:
            try:
                abrir_issue(asunto, cuerpo)
                avisado[clave] = o["precio"]
            except Exception as ex:
                print("   no se pudo abrir el aviso:", ex)

    # --- avisos de modelo agotado o de vuelta a la venta (solo cuenta tiendas leidas en esta corrida)
    if "conocidos" not in estado:
        sembrar_conocidos(estado, prods, mejores, leidas)
        print("   disponibilidad sembrada; agotados de arranque:", ", ".join(estado["agotados"]) or "ninguno")
    for asunto, cuerpo in disponibilidad(estado, prods, mejores, leidas, ahora.astimezone(MX).strftime("%Y-%m-%d %H:%M")):
        print(" * DISPONIBILIDAD", asunto)
        if not seco:
            try:
                abrir_issue(asunto, cuerpo)
            except Exception as ex:
                print("   no se pudo abrir el aviso:", ex)

    # --- avisos del foro (tiendas que no se dejan leer, y recetas con banco)
    sembrar = not estado.get("sembrado")
    for it in foro or []:
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
        if not sembrar:
            try:
                abrir_issue(av[0], av[1])
            except Exception as ex:
                print("   no se pudo abrir el aviso:", ex)
                continue
        vistos[it["guid"]] = it["fecha"]
    if foro is not None and not seco:
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
        estado["vistos"] = dict(list(vistos.items())[-400:])
    if json.dumps(estado, sort_keys=True) != antes:
        with open(ESTADO, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
