"""Sondeo: que tiendas responden a una peticion simple (sin navegador) desde donde corre esto.
Escribe sondeo.json con estado HTTP, tamano y senales de precio por cada direccion."""
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
CAB = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
       "Accept-Language": "es-MX,es;q=0.9,en;q=0.6", "Accept-Encoding": "gzip"}

URLS = {
    "samsung_pagina": "https://www.samsung.com/mx/smartphones/galaxy-z-fold8-ultra/buy/",
    "samsung_searchapi": "https://searchapi.samsung.com/v6/front/b2c/product/card/detail/newhybris?siteCode=mx&modelList=SM-F976BZKMLTM,SM-F971BZKMLTM&commonCodeYN=N&saleSkuYN=N&onlyRequestSkuYN=Y&keySummaryYN=N&shopSiteCode=mx",
    "samsung_simpleinfo": "https://shop.samsung.com/mx/servicesv2/getSimpleProductsInfo?productCodes=SM-F976BZKMLTM,SM-F971BZKMLTM",
    "samsung_addcart": "https://shop.samsung.com/mx/ng/p4v1/addCart?productCode=SM-F976BZKMLTM&quantity=1",
    "walmart_busca": "https://www.walmart.com.mx/search?q=galaxy+z+fold8+ultra",
    "walmart_ficha": "https://www.walmart.com.mx/ip/Smartphone-Samsung-Galaxy-ZFold8-Ultra-512GB-Grafito-Desbloqueado/00880609919959",
    "sams_busca": "https://www.sams.com.mx/search?q=galaxy+z+fold8",
    "bodega_busca": "https://www.bodegaaurrera.com.mx/search?q=galaxy+z+fold8",
    "amazon_ficha": "https://www.amazon.com.mx/dp/B0H92G9GNW?th=1&psc=1",
    "amazon_busca": "https://www.amazon.com.mx/s?k=samsung+galaxy+z+fold8+ultra+512",
    "ml_producto": "https://www.mercadolibre.com.mx/samsung-galaxy-z-fold8-ultra-grafito-512gb/p/MLM73221955",
    "ml_listado": "https://listado.mercadolibre.com.mx/samsung-galaxy-z-fold8-ultra-512gb",
    "ml_api_producto": "https://api.mercadolibre.com/products/MLM73221955",
    "ml_api_busca": "https://api.mercadolibre.com/sites/MLM/search?q=galaxy%20z%20fold8%20ultra&limit=10",
    "liverpool_busca": "https://www.liverpool.com.mx/tienda?s=galaxy+z+fold8",
    "suburbia_busca": "https://www.suburbia.com.mx/tienda?s=galaxy+z+fold8",
    "costco_api": "https://www.costco.com.mx/rest/v2/mexico/products/search?query=galaxy%20z%20fold&fields=FULL&lang=es_MX&curr=MXN",
    "costco_busca": "https://www.costco.com.mx/search?text=galaxy+z+fold8",
    "elektra_api": "https://www.elektra.mx/api/catalog_system/pub/products/search?ft=galaxy%20z%20fold8",
    "coppel_busca": "https://www.coppel.com/search?q=galaxy%20z%20fold8",
    "sears_busca": "https://www.sears.com.mx/resultados/q=galaxy%20z%20fold8/pagina=1",
    "palacio_busca": "https://www.elpalaciodehierro.com/buscar?q=galaxy+z+fold8",
    "telcel_busca": "https://www.telcel.com/tienda/buscar?q=fold8",
    "gameplanet_ps5": "https://www.gameplanet.com/catalogo?q=consola%20playstation%205",
    "gameplanet_fold8": "https://www.gameplanet.com/catalogo?q=galaxy%20z%20fold8",
    "sanborns_ps5": "https://www.sanborns.com.mx/resultados/q=playstation%205/pagina=1",
    "sanborns_fold8": "https://www.sanborns.com.mx/resultados/q=galaxy%20z%20fold8/pagina=1",
    "promodescuentos_rss":"https://www.promodescuentos.com/rss/grupo/smartphones-samsung",
    # --- 25 nov: tiendas nuevas (encargo 05 de la linea C) ---
    "ml_listado_ps5": "https://listado.mercadolibre.com.mx/consola-playstation-5-digital",
    "ml_tienda_samsung": "https://www.mercadolibre.com.mx/tienda/samsung",
    "ml_api_busca_ps5": "https://api.mercadolibre.com/sites/MLM/search?q=playstation%205%20digital&limit=10",
    "walmart_ps5": "https://www.walmart.com.mx/search?q=consola+playstation+5+digital",
    "sams_ps5": "https://www.sams.com.mx/search?q=consola+playstation+5",
    "bodega_ps5": "https://www.bodegaaurrera.com.mx/search?q=consola+playstation+5",
    "coppel_ps5": "https://www.coppel.com/search?q=consola%20playstation%205",
    "coppel_vtex": "https://www.coppel.com/api/catalog_system/pub/products/search?ft=playstation%205",
    "palacio_ps5": "https://www.elpalaciodehierro.com/buscar?q=consola+playstation+5",
    "sears_ps5": "https://www.sears.com.mx/resultados/q=consola%20playstation%205/pagina=1",
    "claroshop_fold8": "https://www.claroshop.com/resultados/q=galaxy%20z%20fold8/pagina=1",
    "claroshop_ps5": "https://www.claroshop.com/resultados/q=consola%20playstation%205/pagina=1",
    "suburbia_ps5": "https://www.suburbia.com.mx/tienda?s=consola+playstation+5",
    "liverpool_ps5": "https://www.liverpool.com.mx/tienda?s=consola+playstation+5",
    "liverpool_pdp": "https://www.liverpool.com.mx/tienda/pdp/p/1156059630",
    "elektra_ps5": "https://www.elektra.mx/api/catalog_system/pub/products/search?ft=consola%20playstation%205",
    "costco_ps5": "https://www.costco.com.mx/rest/v2/mexico/products/search?query=playstation%205&fields=FULL&lang=es_MX&curr=MXN",
    "att_inicio": "https://www.att.com.mx/",
    "movistar_inicio": "https://tienda.movistar.com.mx/",
    "sony_vtex": "https://store.sony.com.mx/api/catalog_system/pub/products/search?ft=playstation%205",
    "sony_inicio": "https://store.sony.com.mx/",
    "officedepot_ps5": "https://www.officedepot.com.mx/officedepot/en/search/?text=consola+playstation+5",
    "officemax_vtex": "https://www.officemax.com.mx/api/catalog_system/pub/products/search?ft=playstation%205",
    "radioshack_ps5": "https://www.radioshack.com.mx/store/radioshack/en/search/?text=consola+playstation+5",
    "cyberpuerta_ps5": "https://www.cyberpuerta.mx/index.php?cl=search&searchparam=consola+playstation+5",
    "ddtech_ps5": "https://ddtech.mx/buscar/consola%20playstation%205",
    "soriana_ps5": "https://www.soriana.com/buscar?q=consola+playstation+5",
    "chedraui_vtex": "https://www.chedraui.com.mx/api/catalog_system/pub/products/search?ft=playstation%205",
    "heb_vtex": "https://www.heb.com.mx/api/catalog_system/pub/products/search?ft=playstation%205",
    "doto_shopify": "https://doto.com.mx/search/suggest.json?q=playstation&resources%5Btype%5D=product",
    "linio_inicio": "https://www.linio.com.mx/",
    "mixup_vtex": "https://www.mixup.com/api/catalog_system/pub/products/search?ft=playstation%205",
    "pcel_ps5": "https://pcel.com/index.php?route=product/search&search=playstation%205",
    "intercompras_inicio": "https://intercompras.com/",
    "zegucom_inicio": "https://www.zegucom.com.mx/",
    # --- ronda 2 ---
    "elektra_ficha_fold8": "https://www.elektra.mx/api/catalog_system/pub/products/search?fq=productId:1302051914",
    "elektra_ps5_digital": "https://www.elektra.mx/api/catalog_system/pub/products/search?ft=consola%20playstation%205%20digital",
    "costco_ficha_fold8": "https://www.costco.com.mx/rest/v2/mexico/products/648321?fields=FULL&lang=es_MX&curr=MXN",
    "ml_tienda_sony": "https://www.mercadolibre.com.mx/tienda/sony",
    "ml_tienda_playstation": "https://www.mercadolibre.com.mx/tienda/playstation",
    "sony_vtex_consola": "https://store.sony.com.mx/api/catalog_system/pub/products/search?ft=consola",
    "sony_vtex_ps5": "https://store.sony.com.mx/api/catalog_system/pub/products/search?ft=ps5",
    "chedraui_ps5_digital": "https://www.chedraui.com.mx/api/catalog_system/pub/products/search?ft=consola%20playstation%205%20digital",
    "chedraui_fold8": "https://www.chedraui.com.mx/api/catalog_system/pub/products/search?ft=galaxy%20z%20fold8",
    "doto_ps5_digital": "https://doto.com.mx/search/suggest.json?q=consola%20ps5%20digital&resources%5Btype%5D=product",
    "doto_fold8": "https://doto.com.mx/search/suggest.json?q=galaxy%20fold8&resources%5Btype%5D=product",
    "soriana_ficha_ps5": "https://www.soriana.com/consola-playstation-5-digital-slim-astro-bot-y-gran-turismo-7-825gb/11912222.html",
    "soriana_fold8": "https://www.soriana.com/buscar?q=galaxy+z+fold8",
    "claroshop_consolas": "https://www.claroshop.com/cat/videojuegos/playstation/consolas?id=20129",
    "cyberpuerta_fold8": "https://www.cyberpuerta.mx/index.php?cl=search&searchparam=galaxy+z+fold8",
    "officedepot_fold8": "https://www.officedepot.com.mx/officedepot/en/search/?text=galaxy+z+fold8",
    "movistar_fold8": "https://tienda.movistar.com.mx/catalogsearch/result/?q=fold8",
    "att_celulares": "https://www.att.com.mx/celulares",
    "heb_busca_ps5": "https://www.heb.com.mx/catalogsearch/result/?q=playstation+5",
    "mixup_inicio": "https://www.mixup.com/",
    "profeco_qqp": "https://datos.profeco.gob.mx/datos_abiertos/qqp.php",
    # --- ronda 3 ---
    "profeco_buenfin": "https://elbuenfin.profeco.gob.mx/",
    "profeco_qqp_inicio": "https://qqp.profeco.gob.mx/",
    "ml_dev_crear_app": "https://developers.mercadolibre.com.mx/es_ar/crea-una-aplicacion-en-mercado-libre-es",
    "ml_devcenter": "https://developers.mercadolibre.com.mx/devcenter",
    "ml_form_alta": "https://forms.gle/2Q39SStHLJNQLjuK9",
    "ml_articulo_fold8": "https://articulo.mercadolibre.com.mx/MLM-3166627273",
    "cyberpuerta_ps5_digital": "https://www.cyberpuerta.mx/index.php?cl=search&searchparam=playstation+5+digital",
    "soriana_fold8_b": "https://www.soriana.com/buscar?q=samsung+galaxy+z+fold",
    "soriana_ficha_fold8": "https://www.soriana.com/samsung-galaxy-z-fold8-7.6-pulgadas-512gb-lavanda-desbloqueado/11944979.html",
    "chedraui_ficha_ps5": "https://www.chedraui.com.mx/api/catalog_system/pub/products/search?fq=productId:3879487",
    "chedraui_ps5_digital_b": "https://www.chedraui.com.mx/api/catalog_system/pub/products/search?ft=playstation%205%20digital",
}

FOLD = re.compile(r"fold\s?-?8", re.I)
PS5 = re.compile(r"play\s*station\s*®?\s*5|\bps5\b", re.I)
NUM = re.compile(r"(?<![\d.,])(\d{1,2}[,.]?\d{3})(?:\.\d{1,2})?(?![\d])")
BLOQ = re.compile(r"captcha|verifica tu identidad|access denied|acceso denegado|robot|px-captcha|blocked", re.I)
VEND = re.compile(r"(sellerName|\"seller\"|vendido por|vendedor|soldBy|sold_by|seller_name)[\"':\s>]{0,6}(.{0,60})", re.I)
PLAT = {"vtex": r"vtex", "shopify": r"shopify", "magento": r"magento|mage-", "sfcc": r"demandware|sfcc",
        "hybris": r"hybris|/_ui/", "next": r"__NEXT_DATA__", "ldjson": r"application/ld\+json"}
CABS = ("server", "x-powered-by", "x-vtex-cache-status", "powered-by", "x-shopid", "x-magento-cache-debug",
        "x-akamai-transformed", "x-cache", "cf-ray", "content-type")


def pedir(url, tiempo=30):
    req = urllib.request.Request(url, headers=CAB)
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            crudo = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                crudo = gzip.decompress(crudo)
            return r.status, r.geturl(), crudo.decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        cuerpo = b""
        try:
            cuerpo = e.read()
            if e.headers.get("Content-Encoding") == "gzip":
                cuerpo = gzip.decompress(cuerpo)
        except Exception:
            pass
        return e.code, url, cuerpo.decode("utf-8", "replace"), dict(e.headers or {})
    except Exception as e:
        return 0, url, "ERROR " + repr(e)[:200], {}


def rango(texto, a, b):
    vistos = set()
    for p in NUM.findall(texto):
        n = int(p.replace(",", "").replace(".", ""))
        if a <= n <= b:
            vistos.add(n)
    return sorted(vistos)[:12]


def analizar(estado, final, texto, cab):
    cabl = {k.lower(): v for k, v in cab.items()}
    return {"estado": estado, "bytes": len(texto),
            "fold8": len(FOLD.findall(texto)), "ps5": len(PS5.findall(texto)),
            "precios_vistos": rango(texto, 20000, 69999), "precios_ps5": rango(texto, 6000, 16000),
            "bloqueo": bool(BLOQ.search(texto[:20000])) or estado in (403, 412, 429),
            "final": final[:160],
            "vendedor_pistas": sorted({(m[0] + ": " + re.sub(r"\s+", " ", m[1])) for m in VEND.findall(texto[:400000])})[:6],
            "plataforma": [k for k, rx in PLAT.items() if re.search(rx, texto[:300000], re.I)],
            "cabeceras": {k: str(cabl[k])[:80] for k in CABS if k in cabl},
            "inicio": re.sub(r"\s+", " ", texto[:300])}


def main():
    salida = {}
    t0 = time.time()
    for nombre, url in URLS.items():
        if time.time() - t0 > 500:
            salida[nombre] = {"estado": None, "nota": "sin tiempo en esta corrida"}
            continue
        estado, final, texto, cab = pedir(url, 12)
        salida[nombre] = analizar(estado, final, texto, cab)
        d = salida[nombre]
        print("%-22s %3s %8d fold8=%-3d ps5=%-3d bloqueo=%-5s %s %s" % (
            nombre, estado, len(texto), d["fold8"], d["ps5"], d["bloqueo"], d["precios_vistos"][:4], d["precios_ps5"][:4]))
    with open("sondeo.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
