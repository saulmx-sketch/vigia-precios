"""Lectores de tiendas nuevas del vigia, aparte de vigia.py para que un error aqui no tumbe al vigia.

vigia.py lo importa al arrancar y llama registrar(v), con v = el modulo vigia ya cargado (cuando corre como programa,
vigia.py es __main__, por eso todo llega por v y aqui no se importa vigia). Si este archivo falta o truena, el vigia
sigue con sus lectores de siempre. Solo biblioteca estandar.

Soriana (medido el 25 nov 2026, desde casa y desde la nube de GitHub, peticion simple):
  busqueda https://www.soriana.com/buscar?q=<consulta>  -> ld+json ItemList con las ligas de ficha; el nombre de cada
  tarjeta viene en <a class="link plp-link ..." href="/<nombre>/<id>.html">titulo</a>.
  ficha https://www.soriana.com/<nombre>/<id>.html -> ld+json Product: name, offers.price ("11990.00"),
  offers.availability ("http://schema.org/InStock"), offers.seller.name ("Soriana").

Cyberpuerta (medido el 25 nov 2026; nube 200 segun TIENDAS_2026-11-25.md):
  busqueda https://www.cyberpuerta.mx/index.php?cl=search&searchparam=<consulta> -> ld+json @graph -> ItemList ->
  itemListElement[].item (Product): name, url, offers.price (numero), offers.availability, offers.itemCondition,
  offers.seller.name ("Cyberpuerta"). La ficha trae Product con los mismos offers, pero name = modelo; titulo en <h1>.

Chedraui (medido el 25 nov 2026; solo PS5, el Fold8 no lo vende: "galaxy z fold8" responde []):
  catalogo VTEX https://www.chedraui.com.mx/api/catalog_system/pub/products/search?ft=<consulta>; ficha ?fq=productId:<id>.
  productName; items[].sellers[]: sellerName ("Chedraui"; en su marketplace hay "Doto" y otros), commertialOffer.Price,
  commertialOffer.AvailableQuantity.
"""
import html
import json
import re
import urllib.parse

SORIANA = "https://www.soriana.com"
SORIANA_MAX_FICHAS = 6
SORIANA_EN_VENTA = {"InStock": "", "LimitedAvailability": "pocas piezas"}
CYBERPUERTA = "https://www.cyberpuerta.mx"
CP_EN_VENTA = {"InStock": "", "LimitedAvailability": "pocas piezas"}
CHEDRAUI_API = "https://www.chedraui.com.mx/api/catalog_system/pub/products/search"
LD = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'


def _bloques(t):
    """Los bloques ld+json de una pagina que se dejan leer como JSON (los rotos se saltan)."""
    salida = []
    for m in re.finditer(LD, t, re.S):
        try:
            salida.append(json.loads(m.group(1)))
        except ValueError:
            continue
    return salida


def _del_tipo(t, tipo):
    """El primer nodo ld+json del tipo pedido, suelto o dentro de un @graph."""
    for d in _bloques(t):
        nodos = d.get("@graph") if isinstance(d, dict) and isinstance(d.get("@graph"), list) else [d]
        for n in nodos:
            if isinstance(n, dict) and n.get("@type") == tipo:
                return n
    return None


EN_EXISTENCIA = ("InStock", "LimitedAvailability", "OnlineOnly", "InStoreOnly")
SIN_EXISTENCIA = ("OutOfStock", "SoldOut", "Discontinued")


def existencia_si_no(texto):
    """schema.org availability ("http://schema.org/InStock" o "InStock") a True/False/None (no se sabe)."""
    fin = (texto or "").rsplit("/", 1)[-1]
    return True if fin in EN_EXISTENCIA else False if fin in SIN_EXISTENCIA else None


def _precio(x, donde):
    if x in (None, ""):
        return None
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        raise RuntimeError("precio ilegible en %s: %r" % (donde, x))


def _oferta_ld(o):
    o = o or {}
    o = o[0] if isinstance(o, list) and o else o
    return o if isinstance(o, dict) else None


def registrar(v):
    """Da de alta los lectores nuevos en el modulo vigia `v`."""

    def ficha_soriana(url):
        """{"titulo", "precio", "vendedor", "existencia", "importado"} del ld+json Product de una ficha de Soriana;
        RuntimeError si la ficha no respondio o no trae ese bloque."""
        e, t = v.pedir(url, pausa=1.5)
        if e != 200:
            raise RuntimeError("Soriana respondio %s en la ficha %s" % (e, url))
        d = _del_tipo(t, "Product")
        if not d:
            raise RuntimeError("Soriana: la ficha %s no trae el bloque ld+json Product (%d bytes)" % (url, len(t)))
        o = d.get("offers") or {}
        o = o[0] if isinstance(o, list) and o else o
        if not isinstance(o, dict):
            raise RuntimeError("Soriana: la ficha %s trae offers ilegible" % url)
        try:
            precio = float(str(o.get("price")).replace(",", "")) if o.get("price") not in (None, "") else None
        except ValueError:
            raise RuntimeError("Soriana: precio ilegible en %s: %r" % (url, o.get("price")))
        fin = lambda x: (x or "").rsplit("/", 1)[-1]
        nombre = html.unescape(d.get("name") or "").strip()
        return {"titulo": nombre, "precio": precio,
                "vendedor": html.unescape(((o.get("seller") or {}).get("name") or "")).strip(),
                "existencia": fin(o.get("availability")),
                "condicion": fin(o.get("itemCondition")),
                "importado": bool(re.search(v.SENAL_IMPORTADO, nombre + " " + (d.get("description") or ""), re.I))}

    def leer_soriana(prod, cfg, estado):
        """Busqueda (ligas y nombres) y, para lo que casa con el producto, su ficha (precio, existencia, vendedor).
        Solo lo nuevo y en existencia; sin vendedor leido no se publica."""
        e, t = v.pedir(SORIANA + "/buscar?q=" + urllib.parse.quote_plus(cfg["consulta"]))
        lista = _del_tipo(t, "ItemList") if e == 200 else None
        if e != 200 or lista is None:
            raise RuntimeError("Soriana respondio %s (%d bytes) sin el bloque ld+json ItemList" % (e, len(t)))
        nombres = {urllib.parse.urlsplit(SORIANA + h).path: html.unescape(x).strip()
                   for h, x in re.findall(r'class="link plp-link[^"]*" href="([^"]+)">([^<]+)</a>', t)}
        ligas = []
        for it in lista.get("itemListElement") or []:
            url = (it or {}).get("url") or ""
            if not url:
                continue
            url = urllib.parse.urljoin(SORIANA, url)
            titulo = nombres.get(urllib.parse.urlsplit(url).path)
            if titulo is not None and not v.variante(prod, titulo):
                continue          # el listado ya dice que no es el producto: no se abre
            if url not in ligas:
                ligas.append(url)
        salida = []
        for url in ligas[:SORIANA_MAX_FICHAS]:
            f = ficha_soriana(url)
            if f["existencia"] not in SORIANA_EN_VENTA or f["condicion"] not in ("", "NewCondition"):
                continue
            if not f["vendedor"]:
                continue          # sin "vendido por" leido no se publica
            of = v.oferta(prod, "Soriana", f["titulo"], f["precio"], url, f["vendedor"],
                          SORIANA_EN_VENTA[f["existencia"]], importado=f["importado"])
            if of:
                salida.append(of)
        return salida

    def ficha_soriana_si_no(url):
        """Para v.FICHAS: la ficha de Soriana con existencia True/False/None (lo usado no cuenta como existencia)."""
        f = dict(ficha_soriana(url))
        nuevo = f.pop("condicion", "") in ("", "NewCondition")
        f["existencia"] = existencia_si_no(f["existencia"]) if nuevo else False
        f["url"] = url
        return f

    # ------------------------------------------------------------------ Cyberpuerta
    def _cp_productos(t):
        """[(titulo, precio, existencia_texto, condicion, vendedor, url)] del ItemList del ld+json de la busqueda."""
        lista = _del_tipo(t, "ItemList")
        if lista is None:
            return None
        salida = []
        for it in lista.get("itemListElement") or []:
            p = (it or {}).get("item") or it or {}
            o = _oferta_ld(p.get("offers")) or {}
            salida.append((html.unescape(p.get("name") or "").strip(), _precio(o.get("price"), "Cyberpuerta"),
                           (o.get("availability") or "").rsplit("/", 1)[-1],
                           (o.get("itemCondition") or "").rsplit("/", 1)[-1],
                           html.unescape(((o.get("seller") or {}).get("name") or "")).strip(),
                           urllib.parse.urljoin(CYBERPUERTA, p.get("url") or o.get("url") or "")))
        return salida

    def leer_cyberpuerta(prod, cfg, estado):
        """Todo sale del ld+json de la busqueda (titulo, precio, existencia, condicion, vendedor, liga): sin fichas.
        Solo lo nuevo y en existencia; sin vendedor leido no se publica."""
        e, t = v.pedir(CYBERPUERTA + "/index.php?cl=search&searchparam=" + urllib.parse.quote_plus(cfg["consulta"]))
        prods = _cp_productos(t) if e == 200 else None
        if prods is None:
            raise RuntimeError("Cyberpuerta respondio %s (%d bytes) sin el bloque ld+json ItemList" % (e, len(t)))
        salida = []
        for titulo, precio, existe, cond, vend, url in prods:
            if existe not in CP_EN_VENTA or cond not in ("", "NewCondition") or not vend:
                continue
            of = v.oferta(prod, "Cyberpuerta", titulo, precio, url, vend, CP_EN_VENTA[existe])
            if of:
                salida.append(of)
        return salida

    def ficha_cyberpuerta(url):
        """Para v.FICHAS. La ficha trae su ld+json Product (precio, existencia, condicion, vendedor), pero su "name" es
        el modelo ("Samsung SM-F971B 7.6...", medido el 25 nov): el titulo sale del <h1>, que es el de la busqueda."""
        e, t = v.pedir(url, pausa=1.0)
        if e != 200:
            raise RuntimeError("Cyberpuerta respondio %s en la ficha %s" % (e, url))
        d = _del_tipo(t, "Product")
        o = _oferta_ld(d.get("offers")) if d else None
        if not o:
            raise RuntimeError("Cyberpuerta: la ficha %s no trae el bloque ld+json Product con offers" % url)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S)
        titulo = html.unescape(re.sub(r"<!--.*?-->|<[^>]+>", "", h1.group(1))).strip() if h1 else ""
        nuevo = (o.get("itemCondition") or "").rsplit("/", 1)[-1] in ("", "NewCondition")
        return {"titulo": titulo or html.unescape(d.get("name") or "").strip(),
                "precio": _precio(o.get("price"), url),
                "vendedor": html.unescape(((o.get("seller") or {}).get("name") or "")).strip(),
                "existencia": existencia_si_no(o.get("availability")) if nuevo else False,
                "importado": bool(re.search(v.SENAL_IMPORTADO, titulo)), "url": url}

    # ------------------------------------------------------------------ Chedraui
    def _ch_json(e, t, que):
        if e not in (200, 206):
            raise RuntimeError("Chedraui respondio %s en %s (%d bytes)" % (e, que, len(t)))
        try:
            d = json.loads(t)
        except ValueError:
            raise RuntimeError("Chedraui: %s no es JSON (%d bytes)" % (que, len(t)))
        if not isinstance(d, list):
            raise RuntimeError("Chedraui: %s no trae la lista de productos" % que)
        return d

    def _ch_opciones(p):
        """Una opcion por vendedor de cada item: titulo, precio, vendedor, existencia (AvailableQuantity > 0)."""
        salida = []
        for it in p.get("items") or []:
            for s in it.get("sellers") or []:
                co = s.get("commertialOffer") or {}
                salida.append({"titulo": p.get("productName") or it.get("nameComplete") or "",
                               "precio": float(co["Price"]) if co.get("Price") else None,
                               "vendedor": (s.get("sellerName") or "").strip(),
                               "existencia": (co.get("AvailableQuantity") or 0) > 0, "importado": False,
                               "url": p.get("link") or "", "defecto": bool(s.get("sellerDefault"))})
        return salida

    def leer_chedraui(prod, cfg, estado):
        """Catalogo publico VTEX (como Elektra): por cada vendedor de cada item, precio y existencia; vendedor real."""
        e, t = v.pedir(CHEDRAUI_API + "?ft=%s&_from=0&_to=49" % urllib.parse.quote(cfg["consulta"]))
        salida = []
        for p in _ch_json(e, t, "la busqueda"):
            for op in _ch_opciones(p):
                if op["existencia"] and op["precio"] and op["vendedor"]:
                    of = v.oferta(prod, "Chedraui", op["titulo"], op["precio"], op["url"], op["vendedor"])
                    if of:
                        salida.append(of)
        return salida

    def ficha_chedraui(url_o_id):
        """Para v.FICHAS: el catalogo por numero de articulo (?fq=productId:<id>), con todas sus opciones."""
        m = re.search(r"(\d{6,})(?:/p)?/?$", str(url_o_id))
        if not m:
            raise RuntimeError("liga de Chedraui sin numero de articulo")
        d = _ch_json(*v.pedir(CHEDRAUI_API + "?fq=productId:" + m.group(1), pausa=1.0), que="la ficha " + m.group(1))
        opciones = _ch_opciones(d[0]) if d else []
        if not opciones:
            raise RuntimeError("Chedraui no devolvio vendedores del articulo %s" % m.group(1))
        top = next((o for o in opciones if o["existencia"] and o["defecto"]), None) or \
            next((o for o in opciones if o["existencia"]), opciones[0])
        return dict(top, opciones=opciones)

    lectores = {"Soriana": leer_soriana, "Cyberpuerta": leer_cyberpuerta, "Chedraui": leer_chedraui}
    vendedores = {"Soriana": r"^\s*soriana\s*$",       # solo Soriana misma; sin terceros
                  "Cyberpuerta": r"^\s*cyberpuerta\s*$",
                  "Chedraui": r"^\s*chedraui\s*$"}     # no "Doto" ni otros vendedores de su marketplace
    fichas = {"Soriana": ficha_soriana_si_no, "Cyberpuerta": ficha_cyberpuerta, "Chedraui": ficha_chedraui}
    v.LECTORES.update(lectores)
    v.VENDEDOR_OFICIAL.update(vendedores)
    if isinstance(getattr(v, "FICHAS", None), dict):
        v.FICHAS.update(fichas)
    return sorted(lectores)
