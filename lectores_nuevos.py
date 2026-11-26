"""Lectores de tiendas nuevas del vigia, aparte de vigia.py para que un error aqui no tumbe al vigia.

vigia.py lo importa al arrancar y llama registrar(v), con v = el modulo vigia ya cargado (cuando corre como programa,
vigia.py es __main__, por eso todo llega por v y aqui no se importa vigia). Si este archivo falta o truena, el vigia
sigue con sus lectores de siempre. Solo biblioteca estandar.

Soriana (medido el 25 nov 2026, desde casa y desde la nube de GitHub, peticion simple):
  busqueda https://www.soriana.com/buscar?q=<consulta>  -> ld+json ItemList con las ligas de ficha; el nombre de cada
  tarjeta viene en <a class="link plp-link ..." href="/<nombre>/<id>.html">titulo</a>.
  ficha https://www.soriana.com/<nombre>/<id>.html -> ld+json Product: name, offers.price ("11990.00"),
  offers.availability ("http://schema.org/InStock"), offers.seller.name ("Soriana").
"""
import html
import json
import re
import urllib.parse

SORIANA = "https://www.soriana.com"
SORIANA_MAX_FICHAS = 6
SORIANA_EN_VENTA = {"InStock": "", "LimitedAvailability": "pocas piezas"}
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
    for d in _bloques(t):
        if isinstance(d, dict) and d.get("@type") == tipo:
            return d
    return None


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

    lectores = {"Soriana": leer_soriana}
    vendedores = {"Soriana": r"^\s*soriana\s*$"}       # solo Soriana misma; sin terceros
    fichas = {"Soriana": ficha_soriana}
    v.LECTORES.update(lectores)
    v.VENDEDOR_OFICIAL.update(vendedores)
    if isinstance(getattr(v, "FICHAS", None), dict):
        v.FICHAS.update(fichas)
    return sorted(lectores)
