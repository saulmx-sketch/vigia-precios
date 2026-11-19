"""Vigia de precios de primera fuente. Lee directamente las tiendas (Samsung con prueba de cupones en un
carrito anonimo, Amazon, Costco, Elektra, Liverpool) y, como red de respaldo para las tiendas que rechazan
lectores automaticos (Walmart, Sam's, Coppel...), los feeds publicos del foro Promodescuentos.
Abre un aviso (issue de GitHub, que llega por correo) cuando un modelo vigilado baja de su tope.

Solo biblioteca estandar. Corre en GitHub Actions cada 15 minutos.
Prueba local:  python vigia.py --seco
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

AQUI = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(AQUI, "visto.json")
PRECIOS = os.path.join(AQUI, "precios.json")
HISTORIAL = os.path.join(AQUI, "historial.csv")
CUPONES = os.path.join(AQUI, "cupones_samsung.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
MX = dt.timezone(dt.timedelta(hours=-6))

# ---------------------------------------------------------------------------------------------
# QUE SE VIGILA Y CON QUE TOPES
# Cada modelo tiene topes propios: el mismo porcentaje sobre SU precio de lista en Samsung.
#   ganga = 41 % de lista, comprar = 45 %, tope (mirar) = 48.6 %.
# Para cambiar un tope a mano, poner la cifra en TOPES_FIJOS, p. ej. {("fold8", 512): (19000, 21000, 23000)}.
# ---------------------------------------------------------------------------------------------
LISTA = {("ultra", 512): 53499, ("ultra", 1024): 63499, ("fold8", 512): 48499, ("fold8", 1024): 57600}
FRACCIONES = (0.411, 0.449, 0.486)
TOPES_FIJOS = {}
PISO = 12000            # menos que esto no es el telefono (fundas, micas, errores)
BAJA_MINIMA = 300       # una tienda ya avisada vuelve a avisar solo si baja al menos esto
EXCLUIR = r"\b(funda|mica|protector|case|carcasa|cargador|reacondicionad[oa]|seminuevo|usado|refurbished|open\s*box|renewed)\b"
PATRON = r"fold\s*-?\s*8|zfold8|fold\s*ultra\s*8"

BANCOS_PROPIOS = {"BBVA": r"bbva|bancomer", "Revolut": r"revolut", "PayPal": r"paypal"}
BANCOS_AJENOS = {"Banorte": r"banorte", "Santander": r"santander", "Banamex": r"banamex|citibanamex",
                 "HSBC": r"hsbc", "Scotiabank": r"scotiabank", "Amex": r"amex|american\s*express",
                 "Inbursa": r"inbursa", "Afirme": r"afirme", "BanCoppel": r"bancoppel",
                 "Mercado Pago": r"mercado\s*pago", "Nu": r"\bnu\b", "Hey Banco": r"hey\s*banco"}

FEEDS = ["https://www.promodescuentos.com/rss/grupo/smartphones-samsung",
         "https://www.promodescuentos.com/rss/grupo/samsung",
         "https://www.promodescuentos.com/rss/grupo/celulares",
         "https://www.promodescuentos.com/rss/tendencias",
         "https://www.promodescuentos.com/rss/todas"]
DIAS_FRESCO = 3
FALLOS_PARA_AVISAR = 8   # corridas seguidas con una fuente caida (8 x 15 min = 2 horas)

SAMSUNG_API = "https://p1-smb-api-cdn.shop.samsung.com/tokocommercewebservices/v2/mx"
SAMSUNG_SKUS = {
    "SM-F976BZKMLTM": ("ultra", 512, "Grafito"), "SM-F976BZVMLTM": ("ultra", 512, "Violeta"),
    "SM-F976BDGMLTM": ("ultra", 512, "Verde"), "SM-F976BZKVLTM": ("ultra", 1024, "Grafito"),
    "SM-F976BZVVLTM": ("ultra", 1024, "Violeta"), "SM-F976BDGVLTM": ("ultra", 1024, "Verde"),
    "SM-F971BZKMLTM": ("fold8", 512, "Grafito"), "SM-F971BLVMLTM": ("fold8", 512, "Lavanda"),
    "SM-F971BZWMLTM": ("fold8", 512, "Crema"), "SM-F971BZGMLTM": ("fold8", 512, "Verde"),
}
NOMBRE = {"ultra": "Fold8 Ultra", "fold8": "Fold8"}


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
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        b = e.read() or b""
        try:
            b = gzip.decompress(b)
        except Exception:
            pass
        return e.code, b.decode("utf-8", "replace")


def topes(modelo, cap):
    clave = (modelo, cap if cap in (512, 1024) else 512)
    if clave in TOPES_FIJOS:
        return TOPES_FIJOS[clave]
    lista = LISTA[clave]
    return tuple(int(round(lista * f / 100.0)) * 100 for f in FRACCIONES)


def clasifica(titulo):
    """(modelo, capacidad) si el titulo es un Fold8 nuevo; None si no interesa."""
    if not re.search(PATRON, titulo, re.I) or re.search(EXCLUIR, titulo, re.I):
        return None
    modelo = "ultra" if re.search(r"ultra", titulo, re.I) else "fold8"
    if re.search(r"\b1\s*tb\b|1024\s*gb", titulo, re.I):
        cap = 1024
    elif re.search(r"512", titulo):
        cap = 512
    elif re.search(r"256", titulo):
        cap = 256
    else:
        cap = None
    return modelo, cap


def cap_txt(cap):
    return {1024: "1 TB", 512: "512 GB", 256: "256 GB"}.get(cap, "capacidad sin dato")


def nivel(precio, modelo, cap):
    g, c, t = topes(modelo, cap)
    return "GANGA" if precio <= g else "COMPRAR" if precio <= c else "MIRAR" if precio <= t else None


def oferta(tienda, titulo, precio, url, vendedor="", nota=""):
    cl = clasifica(titulo)
    if not cl or precio is None or precio < PISO or cl[1] == 256:
        return None
    return {"tienda": tienda, "modelo": cl[0], "cap": cl[1], "precio": float(precio), "url": url,
            "vendedor": vendedor, "nota": nota, "titulo": titulo.strip()[:140]}


# ------------------------------------------------------------------------------- tiendas
def cupones_candidatos(estado):
    lista = []
    try:
        with open(CUPONES, encoding="utf-8") as f:
            lista = [l.split("#")[0].strip() for l in f if l.split("#")[0].strip()]
    except Exception:
        pass
    for c in estado.get("cupones_cosechados", []):
        if c not in lista:
            lista.append(c)
    return lista[:30]


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
        return float(d["totalPriceWithTax"]["value"]), d

    actual, d = total()
    base, buenos = actual, []
    for cup in cupones:
        e2, t2 = pedir(url_vou + "?lang=es_MX&curr=MXN", {"voucherId": cup, "voucherPopup": False}, abridor=ab,
                       json_api=True, pausa=0.25)
        if e2 != 200 or '"errors" : [ ]' not in t2 and '"errors":[]' not in t2:
            continue
        nuevo, d = total()
        if nuevo < actual - 1:
            actual = nuevo
            buenos.append(cup)
        elif nuevo > actual + 1:  # cupon que encarece (reemplaza un descuento mejor): se quita
            pedir(url_vou + "/" + urllib.parse.quote(cup) + "?lang=es_MX&curr=MXN", metodo="DELETE", abridor=ab,
                  json_api=True, pausa=0.25)
            vuelto, d = total()
            if vuelto > actual + 1:
                raise RuntimeError("el cupon %s encarecio el carrito y no se pudo quitar" % cup)
    fin = ""
    for p in d.get("appliedProductPromotions", []) or []:
        fin = (p.get("promotion", {}).get("endDate") or "")[:10] or fin
    return base, actual, buenos, fin


def leer_samsung(estado):
    e, t = pedir("https://searchapi.samsung.com/v6/front/b2c/product/card/detail/newhybris?siteCode=mx&modelList="
                 + ",".join(SAMSUNG_SKUS) + "&commonCodeYN=N&saleSkuYN=N&onlyRequestSkuYN=Y&keySummaryYN=N&shopSiteCode=mx")
    if e != 200:
        raise RuntimeError("catalogo de Samsung respondio %s" % e)
    venta = {}
    for prod in json.loads(t)["response"]["resultData"]["productList"]:
        for m in prod.get("modelList", []):
            sku, precio = m.get("modelCode"), m.get("promotionPrice") or m.get("price")
            if sku in SAMSUNG_SKUS and precio and m.get("ctaType") in ("inStock", "preOrder", "lowStock"):
                modelo, cap, color = SAMSUNG_SKUS[sku]
                previo = venta.get((modelo, cap))
                if not previo or float(precio) < previo[1]:
                    venta[(modelo, cap)] = (sku, float(precio), color, m.get("ctaType"))
    salida, cupones = [], cupones_candidatos(estado)
    for (modelo, cap), (sku, precio, color, cta) in venta.items():
        pagina = "https://www.samsung.com/mx/smartphones/galaxy-z-fold8%s/buy/" % ("-ultra" if modelo == "ultra" else "")
        nota = "preventa" if cta == "preOrder" else ""
        try:
            base, final, buenos, fin = samsung_carrito(sku, cupones)
            nota = "; ".join(x for x in [
                ("con cupones " + " + ".join(buenos)) if buenos else "ningun cupon conocido baja el precio",
                "sin cupon ${:,.0f}".format(base) if buenos else "",
                ("promocion vigente hasta " + fin) if fin else "", nota] if x)
            precio = final
        except Exception as ex:
            nota = "precio de pagina, cupones sin probar (%s)" % str(ex)[:60]
        salida.append({"tienda": "Samsung", "modelo": modelo, "cap": cap, "precio": precio, "url": pagina,
                       "vendedor": "Samsung", "nota": nota, "titulo": "Galaxy Z %s %s %s" % (NOMBRE[modelo], cap_txt(cap), color)})
    if not salida:
        raise RuntimeError("Samsung no listo ningun modelo a la venta")
    return salida


def leer_amazon(estado):
    salida, visto = [], set()
    for q in ("samsung+galaxy+z+fold8+ultra", "samsung+galaxy+z+fold8+512"):
        e, t = pedir("https://www.amazon.com.mx/s?k=" + q, pausa=1.0)
        if e != 200 or "s-search-result" not in t:
            raise RuntimeError("Amazon respondio %s (%d bytes)" % (e, len(t)))
        marcas = list(re.finditer(r'data-asin="([A-Z0-9]{10})"[^>]{0,400}data-component-type="s-search-result"', t))
        for i, m in enumerate(marcas):
            bloque = t[m.end():marcas[i + 1].start() if i + 1 < len(marcas) else m.end() + 20000]
            tit = re.search(r"<h2[^>]*>(.*?)</h2>", bloque, re.S)
            pr = re.search(r'class="a-offscreen">\$([\d,]+\.\d\d)<', bloque)
            if not tit or not pr or m.group(1) in visto:
                continue
            titulo = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tit.group(1)))).strip()
            if not re.search(r"samsung", titulo, re.I):
                continue
            of = oferta("Amazon", titulo, float(pr.group(1).replace(",", "")),
                        "https://www.amazon.com.mx/dp/%s?th=1&psc=1" % m.group(1),
                        nota="con cupon en la ficha" if re.search(r"cup[oó]n", bloque, re.I) else "")
            if of:
                visto.add(m.group(1))
                of["vendedor"] = "ver ficha"
                salida.append(of)
    for of in salida:  # solo si esta bajo tope se gasta una peticion en saber quien vende
        if nivel(of["precio"], of["modelo"], of["cap"]):
            e, t = pedir(of["url"], pausa=1.0)
            v = re.search(r'offer-display-feature-text-message">\s*([^<]{2,60})<', t)
            of["vendedor"] = v.group(1).strip() if v else "ver ficha"
    return salida


def leer_costco(estado):
    e, t = pedir("https://www.costco.com.mx/rest/v2/mexico/products/search?query=galaxy%20z%20fold&fields=FULL&lang=es_MX&curr=MXN")
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
        of = oferta("Costco", p.get("name", ""), precio, "https://www.costco.com.mx" + p.get("url", ""), "Costco (pide membresia)", nota)
        if of:
            salida.append(of)
    return salida


def leer_elektra(estado):
    e, t = pedir("https://www.elektra.mx/api/catalog_system/pub/products/search?ft=galaxy%20z%20fold8&_from=0&_to=49")
    if e not in (200, 206):
        raise RuntimeError("Elektra respondio %s" % e)
    salida = []
    for p in json.loads(t):
        for it in p.get("items", []):
            for s in it.get("sellers", []):
                co = s.get("commertialOffer", {})
                if co.get("AvailableQuantity", 0) > 0 and co.get("Price"):
                    of = oferta("Elektra", p.get("productName", ""), co["Price"], p.get("link", ""), s.get("sellerName", ""))
                    if of:
                        salida.append(of)
    return salida


def leer_liverpool(estado):
    e, t = pedir("https://www.liverpool.com.mx/tienda?s=galaxy+z+fold8")
    if e != 200 or "-price" not in t:
        raise RuntimeError("Liverpool respondio %s (%d bytes)" % (e, len(t)))
    salida = []
    for m in re.finditer(r'<h3[^>]*>([^<]*)</h3><div[^>]*data-testid="(\d+)-price">(.{0,700}?)</div>', t, re.S):
        pr = re.search(r'data-testid="discounted".{0,200}?\$(?:<!-- -->)?([\d,]+)', m.group(3), re.S) or \
             re.search(r"\$(?:<!-- -->)?([\d,]+)", m.group(3))
        if not pr:
            continue
        of = oferta("Liverpool", html.unescape(m.group(1)), float(pr.group(1).replace(",", "")),
                    "https://www.liverpool.com.mx/tienda/pdp/p/" + m.group(2), "Liverpool",
                    "la tarjeta no dice capacidad: es el precio mas bajo de sus variantes")
        if of:
            salida.append(of)
    return salida


TIENDAS = [("Samsung", leer_samsung), ("Amazon", leer_amazon), ("Costco", leer_costco),
           ("Elektra", leer_elektra), ("Liverpool", leer_liverpool)]


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


def leer_foro(estado):
    items = {}
    for url in FEEDS:
        e, t = pedir(url)
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
    if not items:
        raise RuntimeError("ningun feed del foro respondio")
    cosecha = estado.setdefault("cupones_cosechados", [])
    for d in items.values():  # codigos de cupon que la gente publica para Samsung: se prueban en el carrito real
        if re.search(r"samsung", d["tienda"], re.I):
            for c in re.findall(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b|\b[A-Z]{3,}\d{2,}[A-Z0-9]*\b", limpio(d["descripcion"], 3000)):
                if c not in cosecha and 5 <= len(c) <= 22:
                    cosecha.append(c)
    del cosecha[:-25]
    return list(items.values())


def aviso_foro(it):
    cl = clasifica(it["titulo"])
    if not cl or cl[1] == 256:
        return None
    precio = a_numero(it["precio_txt"])
    if precio is not None and (precio < PISO or not nivel(precio, cl[0], cl[1])):
        return None
    nota, me_aplica = bancos(it["titulo"] + " " + limpio(it["descripcion"], 3000))
    niv = "SIN PRECIO, REVISAR" if precio is None else nivel(precio, cl[0], cl[1]) if me_aplica else "SOLO CON OTRO BANCO"
    ptxt = "${:,.0f}".format(precio) if precio is not None else "sin precio"
    g, c, t = topes(cl[0], cl[1])
    asunto = "[foro] {} {} {} en {} · {} · {}".format(NOMBRE[cl[0]], cap_txt(cl[1]), ptxt, it["tienda"] or "tienda", niv, nota)
    cuerpo = "\n".join([
        "**{}**".format(it["titulo"]), "",
        "Fuente: publicacion de un usuario en el foro de ofertas (no leida en la tienda; esta tienda no deja leer sus precios a programas).",
        "", "- Precio publicado: **{}** en {}".format(ptxt, it["tienda"] or "(sin dato)"),
        "- Veredicto: **{}** ({})".format(niv, nota),
        "- Topes de {} {}: ganga ${:,}; comprar ${:,}; mirar ${:,}".format(NOMBRE[cl[0]], cap_txt(cl[1]), g, c, t),
        "- Publicada: {}".format(it["fecha"]), "- Oferta: {}".format(it["link"]), "",
        "> " + limpio(it["descripcion"]).replace("\n", "\n> "), "",
        "Antes de pagar: confirma en la tienda que siga viva y quien vende (Samsung o la tienda, no un tercero)."])
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
    filas = ["| Modelo | Tienda | Precio | Vendedor | Como |", "|---|---|---|---|---|"]
    for o in sorted(mejores.values(), key=lambda o: (o["modelo"] != "ultra", o["cap"] or 0, o["precio"])):
        filas.append("| {} {} | [{}]({}) | ${:,.0f} | {} | {} |".format(
            NOMBRE[o["modelo"]], cap_txt(o["cap"]), o["tienda"], o["url"], o["precio"], o["vendedor"] or "", o["nota"] or ""))
    return "\n".join(filas)


def main():
    seco = "--seco" in sys.argv
    prueba = os.environ.get("PRUEBA", "").lower() in ("1", "true", "yes")
    ahora = dt.datetime.now(dt.timezone.utc)
    try:
        with open(ESTADO, encoding="utf-8") as f:
            estado = json.load(f)
    except Exception:
        estado = {}
    antes = json.dumps(estado, sort_keys=True)
    fallos, avisado = estado.setdefault("fallos_fuente", {}), estado.setdefault("avisado", {})
    vistos = estado.setdefault("vistos", {})

    def fuente(nombre, funcion):
        try:
            r = funcion(estado)
            if fallos.get(nombre, 0) >= FALLOS_PARA_AVISAR and not seco:
                abrir_issue("El vigia volvio a leer %s" % nombre, "La fuente %s responde de nuevo." % nombre)
            fallos[nombre] = 0
            print("fuente ok    %-10s %d" % (nombre, len(r)))
            return r
        except Exception as ex:
            fallos[nombre] = fallos.get(nombre, 0) + 1
            print("fuente FALLA %-10s (%d seguidas) %s" % (nombre, fallos[nombre], str(ex)[:150]))
            if fallos[nombre] == FALLOS_PARA_AVISAR and not seco:
                abrir_issue("El vigia no puede leer %s desde hace 2 horas" % nombre,
                            "Ultimo error: %s\n\nMientras siga asi no vera precios de esa fuente; las demas siguen vigiladas." % str(ex)[:300])
            return None

    # El foro va primero: de ahi salen codigos de cupon nuevos que Samsung prueba en esta misma corrida.
    foro = fuente("Foro", leer_foro)
    ofertas = []
    for nombre, funcion in TIENDAS:
        ofertas += fuente(nombre, funcion) or []

    mejores = {}
    for o in ofertas:
        clave = "%s|%s|%s" % (o["tienda"], o["modelo"], o["cap"])
        if clave not in mejores or o["precio"] < mejores[clave]["precio"]:
            mejores[clave] = o
    for clave, o in sorted(mejores.items()):
        print("  %-28s $%9.0f  %-6s %s | %s" % (clave, o["precio"], nivel(o["precio"], o["modelo"], o["cap"]) or "", o["vendedor"], o["nota"]))

    # --- avisos por precio leido en tienda
    for clave, o in mejores.items():
        niv = nivel(o["precio"], o["modelo"], o["cap"])
        if not niv:
            avisado.pop(clave, None)   # subio del tope: la proxima baja vuelve a avisar
            continue
        if clave in avisado and o["precio"] > avisado[clave] - BAJA_MINIMA:
            continue
        g, c, t = topes(o["modelo"], o["cap"])
        asunto = "{} {} ${:,.0f} en {} · {} · leido en la tienda".format(NOMBRE[o["modelo"]], cap_txt(o["cap"]), o["precio"], o["tienda"], niv)
        cuerpo = "\n".join([
            "**{}**".format(o["titulo"]), "",
            "- Precio leido ahora en la tienda: **${:,.0f}** ({})".format(o["precio"], o["tienda"]),
            "- Como se llega: {}".format(o["nota"] or "precio directo, sin cupon"),
            "- Quien vende: {}".format(o["vendedor"] or "sin dato"),
            "- Veredicto: **{}**. Topes de {} {}: ganga ${:,}; comprar ${:,}; mirar ${:,}".format(niv, NOMBRE[o["modelo"]], cap_txt(o["cap"]), g, c, t),
            "- Liga: {}".format(o["url"]), "",
            "Lo mejor de cada tienda en este momento:", "", tabla(mejores), "",
            "No incluye devoluciones de banco (se ven hasta pagar). El seguro Care+ se contrata aparte dentro de 30 dias."])
        print(" * AVISO", asunto)
        if not seco:
            try:
                abrir_issue(asunto, cuerpo)
                avisado[clave] = o["precio"]
            except Exception as ex:
                print("   no se pudo abrir el aviso:", ex)

    # --- avisos del foro (tiendas que no se dejan leer, y recetas con banco)
    sembrar = not estado.get("sembrado")
    for it in foro or []:
        av = aviso_foro(it)
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
        abrir_issue("PRUEBA del vigia: precios leidos ahora directamente en las tiendas",
                    "Esto es una prueba de que el vigia lee las tiendas y de que el aviso te llega.\n\n" + tabla(mejores)
                    + "\n\nFuentes caidas en esta corrida: " + (", ".join(n for n, v in fallos.items() if v) or "ninguna")
                    + "\n\nTopes: " + "; ".join("{} {}: ganga ${:,}, comprar ${:,}, mirar ${:,}".format(NOMBRE[m], cap_txt(cp), *topes(m, cp)) for (m, cp) in LISTA))

    if seco:
        return 0
    # --- bitacora de precios (solo se reescribe cuando algo cambia)
    try:
        with open(PRECIOS, encoding="utf-8") as f:
            previos = json.load(f)
    except Exception:
        previos = {}
    actuales = dict(previos)
    hoy = ahora.astimezone(MX).strftime("%Y-%m-%d %H:%M")
    cambios = []
    for clave, o in mejores.items():
        ant = previos.get(clave, {}).get("precio")
        if ant != o["precio"]:
            cambios.append([hoy, o["tienda"], NOMBRE[o["modelo"]], cap_txt(o["cap"]), "%.2f" % o["precio"], o["vendedor"], o["nota"], o["url"]])
        actuales[clave] = {"precio": o["precio"], "vendedor": o["vendedor"], "nota": o["nota"], "url": o["url"], "leido": hoy if ant != o["precio"] else previos.get(clave, {}).get("leido", hoy)}
    if cambios:
        nuevo = not os.path.exists(HISTORIAL)
        with open(HISTORIAL, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(["fecha", "tienda", "modelo", "capacidad", "precio", "vendedor", "como", "liga"])
            w.writerows(cambios)
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
