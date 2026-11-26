"""Lectores de tiendas nuevas del vigia, aparte de vigia.py para que un error aqui no tumbe al vigia.

vigia.py lo importa al arrancar y llama registrar(v), con v = el modulo vigia ya cargado (cuando corre como programa,
vigia.py es __main__, por eso todo llega por v y aqui no se importa vigia). Despues corre pruebas\\casos_lectores_nuevos.py;
si este archivo falta, truena o sus pruebas fallan, el vigia sigue con sus lectores de siempre y las tiendas de aqui
salen como "lector no cargado" (con su aviso de horas sin leer). Solo biblioteca estandar.

Soriana (medido el 25 nov 2026, desde casa y desde la nube de GitHub, peticion simple):
  busqueda https://www.soriana.com/buscar?q=<consulta>  -> ld+json ItemList con las ligas de ficha; el nombre de cada
  tarjeta viene en <a class="link plp-link ..." href="/<nombre>/<id>.html">titulo</a>.
  ficha https://www.soriana.com/<nombre>/<id>.html -> ld+json Product: name, offers.price ("11990.00"),
  offers.availability ("http://schema.org/InStock"), offers.seller.name ("Soriana").

Cyberpuerta (medido el 25 nov 2026; nube 200 segun TIENDAS_2026-11-25.md):
  busqueda https://www.cyberpuerta.mx/index.php?cl=search&searchparam=<consulta> -> ld+json @graph -> ItemList ->
  itemListElement[].item (Product): name, sku, url, offers.price (numero), offers.availability, offers.itemCondition,
  offers.seller.name ("Cyberpuerta"). La ficha trae Product con los mismos offers y sku, pero name = modelo; titulo en <h1>.
  Regla de clave por region: la trae cada producto en productos.json, requisitos.clave_region.<tienda> =
  {"forma": expresion con un grupo que captura la region, "vale": expresion de las regiones que valen}. Producto sin esa
  regla = no se filtra por clave. Aqui no hay nombres de producto, modelo ni marca.

Chedraui (medido el 25 nov 2026):
  catalogo VTEX https://www.chedraui.com.mx/api/catalog_system/pub/products/search?ft=<consulta>; ficha ?fq=productId:<id>.
  productName; items[].sellers[]: sellerName ("Chedraui"; en su marketplace hay "Doto" y otros), commertialOffer.Price,
  commertialOffer.AvailableQuantity.

Regla comun contra el "Agotado" falso (revisor R3-H1): si un articulo pasa variante() y el piso pero se descarto por un
dato ausente o ilegible (vendedor vacio, existencia que no es ni "hay" ni "agotado"), y el producto queda sin ofertas y
sin ningun agotado explicito, el lector lanza RuntimeError("formato cambiado: ...") en vez de devolver lista vacia.
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
CP_SIN_REGION = "Cyberpuerta no dice de qué región es el modelo: confirma en la ficha que sea versión México"
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


class Ilegible(ValueError):
    pass


def _precio(x):
    """Precio a numero; None si falta; Ilegible si viene algo que no es numero ("Desde 1,679")."""
    if x in (None, ""):
        return None
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        raise Ilegible(repr(x))


def _oferta_ld(o):
    o = o or {}
    o = o[0] if isinstance(o, list) and o else o
    return o if isinstance(o, dict) else None


def regla_region(prod, tienda):
    """La regla de clave por region del producto para esa tienda (productos.json, requisitos.clave_region), o None."""
    r = (prod.get("requisitos") or {}).get("clave_region", {}).get(tienda)
    return r if isinstance(r, dict) and r.get("forma") and r.get("vale") else None


def region_de(regla, sku):
    """(region, vale): la region que la `forma` de la regla captura en el sku y si `vale`; (None, None) si la clave no
    la dice."""
    m = re.match(regla["forma"], (sku or "").strip(), re.I)
    if not m or not m.group(1):
        return None, None
    return m.group(1), bool(re.search(regla["vale"], m.group(1), re.I))


def region_a_revisar(regla, reg):
    """Nota si la region `reg` (que no vale) casa el campo opcional `revisar` de la regla: entra con esa nota en vez de
    quedar fuera. La nota sale de `nota_revisar` ({region} se cambia por la region) o de un texto general."""
    if not reg or not regla.get("revisar") or not re.search(regla["revisar"], reg, re.I):
        return ""
    return (regla.get("nota_revisar") or "clave de modelo {region}: confirma en la ficha que tenga garantía en México"
            ).replace("{region}", reg)


class Cuenta:
    """Lleva la cuenta de lo que paso variante() y el piso y no entro, para distinguir "agotado" de "formato cambiado".
    La cuenta es por variante: el agotado de una variante no disculpa a otra que vino sin vendedor (con la busqueda real
    de Cyberpuerta sin "seller", un articulo agotado de otra variante escondia el cambio de formato)."""

    def __init__(self, tienda, prod, variante):
        self.tienda, self.prod, self.variante, self.raros, self.agotados = tienda, prod, variante, {}, set()

    def _var(self, titulo):
        var = self.variante(self.prod, titulo)
        return var["id"] if var else "?"

    def raro(self, titulo, motivo):
        self.raros.setdefault(self._var(titulo), []).append("%s (%s)" % (titulo[:60], motivo))

    def agotado(self, titulo):
        self.agotados.add(self._var(titulo))

    def cerrar(self, salida):
        """Sin ninguna oferta del producto y con una variante que solo trae articulos sin dato (ningun agotado
        explicito de esa variante): RuntimeError("formato cambiado: ...")."""
        if not salida:
            for vid, lista in self.raros.items():
                if vid not in self.agotados:
                    raise RuntimeError("formato cambiado: %s no trae ofertas de %s y %d articulo(s) de %s vienen sin "
                                       "dato: %s" % (self.tienda, self.prod["id"], len(lista), vid, "; ".join(lista[:3])))
        return salida


def registrar(v):
    """Da de alta los lectores nuevos en el modulo vigia `v`."""

    def interesa(prod, titulo, precio):
        return bool(v.variante(prod, titulo)) and precio is not None and precio >= prod["piso"]

    # ------------------------------------------------------------------ Soriana
    def ficha_soriana(url):
        """{"titulo", "precio", "vendedor", "existencia", "importado"} del ld+json Product de una ficha de Soriana;
        RuntimeError si la ficha no respondio o no trae ese bloque."""
        e, t = v.pedir(url, pausa=1.5)
        if e != 200:
            raise RuntimeError("Soriana respondio %s en la ficha %s" % (e, url))
        d = _del_tipo(t, "Product")
        if not d:
            raise RuntimeError("Soriana: la ficha %s no trae el bloque ld+json Product (%d bytes)" % (url, len(t)))
        o = _oferta_ld(d.get("offers"))
        if not o:
            raise RuntimeError("Soriana: la ficha %s trae offers ilegible" % url)
        try:
            precio = _precio(o.get("price"))
        except Ilegible as ex:
            raise RuntimeError("Soriana: precio ilegible en %s: %s" % (url, ex))
        fin = lambda x: (x or "").rsplit("/", 1)[-1]
        nombre = html.unescape(d.get("name") or "").strip()
        return {"titulo": nombre, "precio": precio,
                "vendedor": html.unescape(((o.get("seller") or {}).get("name") or "")).strip(),
                "existencia": fin(o.get("availability")),
                "condicion": fin(o.get("itemCondition")),
                "importado": bool(re.search(v.SENAL_IMPORTADO, nombre + " " + (d.get("description") or ""), re.I))}

    def leer_soriana(prod, cfg, estado):
        """Busqueda (ligas y nombres) y, para lo que casa con el producto, su ficha (precio, existencia, vendedor).
        Solo lo nuevo y en existencia; sin vendedor leido no se publica. Una ficha que falla se salta; si fallan todas
        las abiertas, RuntimeError."""
        e, t = v.pedir(SORIANA + "/buscar?q=" + urllib.parse.quote_plus(cfg["consulta"]))
        lista = _del_tipo(t, "ItemList") if e == 200 else None
        if e != 200 or lista is None:
            raise RuntimeError("Soriana respondio %s (%d bytes) sin el bloque ld+json ItemList" % (e, len(t)))
        nombres = {urllib.parse.urlsplit(SORIANA + h).path: html.unescape(x).strip()
                   for h, x in re.findall(r'class="link plp-link[^"]*" href="([^"]+)">([^<]+)</a>', t)}
        elementos = [((it or {}).get("url") or "") for it in lista.get("itemListElement") or []]
        elementos = [urllib.parse.urljoin(SORIANA, u) for u in elementos if u]
        if elementos and not nombres:
            raise RuntimeError("formato cambiado: el listado de Soriana trae %d ligas y ningun nombre" % len(elementos))
        ligas = []
        for url in elementos:
            titulo = nombres.get(urllib.parse.urlsplit(url).path)
            if titulo is not None and not v.variante(prod, titulo):
                continue          # el listado ya dice que no es el producto: no se abre
            if url not in ligas:
                ligas.append(url)
        salida, cuenta, fallas = [], Cuenta("Soriana", prod, v.variante), []
        abiertas = ligas[:SORIANA_MAX_FICHAS]
        for url in abiertas:
            try:
                f = ficha_soriana(url)
            except RuntimeError as ex:
                fallas.append(str(ex)[:120])
                print("   Soriana: ficha saltada: %s" % str(ex)[:150])
                continue
            existe = existencia_si_no(f["existencia"])
            if f["condicion"] not in ("", "NewCondition"):
                continue
            if not interesa(prod, f["titulo"], f["precio"]):
                continue
            if existe is False:
                cuenta.agotado(f["titulo"])
                continue
            if existe is None or f["existencia"] not in SORIANA_EN_VENTA:
                cuenta.raro(f["titulo"], "existencia %r" % f["existencia"])
                continue
            if not f["vendedor"]:
                cuenta.raro(f["titulo"], "sin vendedor")   # sin "vendido por" leido no se publica
                continue
            of = v.oferta(prod, "Soriana", f["titulo"], f["precio"], url, f["vendedor"],
                          SORIANA_EN_VENTA[f["existencia"]], importado=f["importado"])
            if of:
                salida.append(of)
        if abiertas and len(fallas) == len(abiertas):
            raise RuntimeError("Soriana: fallaron las %d fichas abiertas: %s" % (len(fallas), fallas[0]))
        return cuenta.cerrar(salida)

    def ficha_soriana_si_no(url):
        """Para v.FICHAS: la ficha de Soriana con existencia True/False/None (lo usado no cuenta como existencia)."""
        f = dict(ficha_soriana(url))
        nuevo = f.pop("condicion", "") in ("", "NewCondition")
        f["existencia"] = existencia_si_no(f["existencia"]) if nuevo else False
        f["url"] = url
        return f

    # ------------------------------------------------------------------ Cyberpuerta
    def _cp_productos(t, prod):
        """[dict(titulo, precio, existencia, condicion, vendedor, url, sku)] del ItemList de la busqueda. Un precio
        ilegible se salta (None) salvo en un articulo que pasa variante(): ahi es RuntimeError."""
        lista = _del_tipo(t, "ItemList")
        if lista is None:
            return None
        salida = []
        for it in lista.get("itemListElement") or []:
            p = (it or {}).get("item") or it or {}
            o = _oferta_ld(p.get("offers")) or {}
            titulo = html.unescape(p.get("name") or "").strip()
            try:
                precio = _precio(o.get("price"))
            except Ilegible as ex:
                if titulo and v.variante(prod, titulo):
                    raise RuntimeError("Cyberpuerta: precio ilegible en %s: %s" % (titulo[:60], ex))
                precio = None
            salida.append({"titulo": titulo, "precio": precio, "existencia": o.get("availability") or "",
                           "condicion": (o.get("itemCondition") or "").rsplit("/", 1)[-1],
                           "vendedor": html.unescape(((o.get("seller") or {}).get("name") or "")).strip(),
                           "url": urllib.parse.urljoin(CYBERPUERTA, p.get("url") or o.get("url") or ""),
                           "sku": (p.get("sku") or "").strip()})
        return salida

    def leer_cyberpuerta(prod, cfg, estado):
        """Todo sale del ld+json de la busqueda (titulo, precio, existencia, condicion, vendedor, sku, liga): sin fichas.
        Solo lo nuevo y en existencia; sin vendedor leido no se publica. Si el producto trae regla de clave por region
        para esta tienda: clave de una region que no vale = importado (queda fuera, y se dice); clave sin region = entra
        con nota. Sin regla, no se mira la clave."""
        e, t = v.pedir(CYBERPUERTA + "/index.php?cl=search&searchparam=" + urllib.parse.quote_plus(cfg["consulta"]),
                       pausa=1.0)
        prods = _cp_productos(t, prod) if e == 200 else None
        if prods is None:
            raise RuntimeError("Cyberpuerta respondio %s (%d bytes) sin el bloque ld+json ItemList" % (e, len(t)))
        if not prods:
            raise RuntimeError("formato cambiado: la busqueda de Cyberpuerta trae el ItemList vacio (una busqueda real "
                               "siempre devuelve algo)")
        if not any(a["titulo"] for a in prods):
            raise RuntimeError("formato cambiado: el ItemList de Cyberpuerta trae %d articulos sin nombre" % len(prods))
        salida, cuenta = [], Cuenta("Cyberpuerta", prod, v.variante)
        for a in prods:
            if a["condicion"] not in ("", "NewCondition") or not interesa(prod, a["titulo"], a["precio"]):
                continue
            existe = existencia_si_no(a["existencia"])
            if existe is False:
                cuenta.agotado(a["titulo"])
                continue
            fin = a["existencia"].rsplit("/", 1)[-1]
            if existe is None or fin not in CP_EN_VENTA:
                cuenta.raro(a["titulo"], "existencia %r" % a["existencia"])
                continue
            if not a["vendedor"]:
                cuenta.raro(a["titulo"], "sin vendedor")
                continue
            importado, nota = False, CP_EN_VENTA[fin]
            regla = regla_region(prod, "Cyberpuerta")
            if regla:
                reg, vale = region_de(regla, a["sku"])
                revisar = region_a_revisar(regla, reg) if reg and not vale else ""
                if revisar:
                    nota = "; ".join(x for x in (nota, revisar) if x)
                elif reg and not vale:
                    importado = True
                    print("   Cyberpuerta: fuera %s $%s: clave de otra region (%s)"
                          % (a["titulo"][:70], "{:,.0f}".format(a["precio"]), a["sku"]))
                elif not reg:
                    nota = "; ".join(x for x in (nota, CP_SIN_REGION) if x)
            of = v.oferta(prod, "Cyberpuerta", a["titulo"], a["precio"], a["url"], a["vendedor"], nota,
                          importado=importado)
            if of:
                salida.append(of)
        return cuenta.cerrar(salida)

    def ficha_cyberpuerta(url):
        """Para v.FICHAS. La ficha trae su ld+json Product (precio, existencia, condicion, vendedor, sku), pero su "name"
        es el modelo (medido el 25 nov): el titulo sale del <h1>, que es el de la busqueda. La ficha no sabe de que
        producto es (contrato de FICHAS: solo la url), asi que mira las reglas de clave por region de todos los productos
        de productos.json para Cyberpuerta: si la forma de alguna casa con el sku y su region no vale = importado."""
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
        try:
            precio = _precio(o.get("price"))
        except Ilegible as ex:
            raise RuntimeError("Cyberpuerta: precio ilegible en %s: %s" % (url, ex))
        sku = d.get("sku") or d.get("mpn") or ""
        otra_region = False
        for p in v.cargar_config():
            regla = regla_region(p, "Cyberpuerta")
            if regla:
                reg, vale = region_de(regla, sku)
                otra_region = otra_region or bool(reg and not vale and not region_a_revisar(regla, reg))
        return {"titulo": titulo or html.unescape(d.get("name") or "").strip(), "precio": precio,
                "vendedor": html.unescape(((o.get("seller") or {}).get("name") or "")).strip(),
                "existencia": existencia_si_no(o.get("availability")) if nuevo else False,
                "importado": bool(re.search(v.SENAL_IMPORTADO, titulo + " " + (d.get("description") or ""), re.I))
                or otra_region,
                "url": url, "sku": d.get("sku") or ""}

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
        """Una opcion por vendedor de cada item: titulo, precio, vendedor, existencia (True si AvailableQuantity > 0,
        False si es 0, None si falta o no se lee). Precio que no se lee = "ilegible": True."""
        salida = []
        for it in p.get("items") or []:
            for s in it.get("sellers") or []:
                co = s.get("commertialOffer") or {}
                ilegible = False
                try:
                    precio = float(str(co["Price"]).replace(",", "")) if co.get("Price") not in (None, "", 0) else None
                except ValueError:
                    precio, ilegible = None, True
                try:
                    q = co.get("AvailableQuantity")
                    existe = None if q in (None, "") else int(float(q)) > 0
                except (TypeError, ValueError):
                    existe = None
                salida.append({"titulo": p.get("productName") or it.get("nameComplete") or "", "precio": precio,
                               "vendedor": (s.get("sellerName") or "").strip(), "existencia": existe,
                               "importado": False, "url": p.get("link") or "", "defecto": bool(s.get("sellerDefault")),
                               "ilegible": ilegible})
        return salida

    def leer_chedraui(prod, cfg, estado):
        """Catalogo publico VTEX (como Elektra): por cada vendedor de cada item, precio y existencia; vendedor real."""
        e, t = v.pedir(CHEDRAUI_API + "?ft=%s&_from=0&_to=49" % urllib.parse.quote(cfg["consulta"]))
        salida, cuenta = [], Cuenta("Chedraui", prod, v.variante)
        for p in _ch_json(e, t, "la busqueda"):
            for op in _ch_opciones(p):
                if op["ilegible"] and v.variante(prod, op["titulo"]):
                    cuenta.raro(op["titulo"], "precio ilegible")
                    continue
                if not interesa(prod, op["titulo"], op["precio"]):
                    continue
                if op["existencia"] is False:
                    cuenta.agotado(op["titulo"])
                    continue
                if op["existencia"] is None:
                    cuenta.raro(op["titulo"], "existencia sin dato")
                    continue
                if not op["vendedor"]:
                    cuenta.raro(op["titulo"], "sin vendedor")
                    continue
                of = v.oferta(prod, "Chedraui", op["titulo"], op["precio"], op["url"], op["vendedor"])
                if of:
                    salida.append(of)
        return cuenta.cerrar(salida)

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
