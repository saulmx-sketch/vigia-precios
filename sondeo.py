"""Sondeo: que tiendas responden a una peticion simple (sin navegador) desde donde corre esto.
Escribe sondeo.json con estado HTTP, tamano y senales de precio por cada direccion."""
import gzip
import json
import re
import sys
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
    "promodescuentos_rss": "https://www.promodescuentos.com/rss/grupo/smartphones-samsung",
}


def pedir(url):
    req = urllib.request.Request(url, headers=CAB)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            crudo = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                crudo = gzip.decompress(crudo)
            return r.status, r.geturl(), crudo.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        cuerpo = b""
        try:
            cuerpo = e.read()
            if e.headers.get("Content-Encoding") == "gzip":
                cuerpo = gzip.decompress(cuerpo)
        except Exception:
            pass
        return e.code, url, cuerpo.decode("utf-8", "replace")
    except Exception as e:
        return 0, url, "ERROR " + repr(e)[:200]


def main():
    salida = {}
    for nombre, url in URLS.items():
        estado, final, texto = pedir(url)
        folds = len(re.findall(r"fold\s?-?8", texto, re.I))
        precios = re.findall(r"(?<![\d.])([2-6]\d[,.]?\d{3})(?:\.\d{2})?(?![\d])", texto)
        tops = sorted({p.replace(",", "").replace(".", "") for p in precios})[:12]
        bloqueo = bool(re.search(r"captcha|verifica tu identidad|access denied|robot|px-captcha|blocked", texto[:20000], re.I))
        salida[nombre] = {"estado": estado, "bytes": len(texto), "fold8": folds, "precios_vistos": tops,
                          "bloqueo": bloqueo, "final": final[:120], "inicio": re.sub(r"\s+", " ", texto[:160])}
        print("%-22s %3s %8d fold8=%-3d bloqueo=%-5s %s" % (nombre, estado, len(texto), folds, bloqueo, tops[:6]))
    with open("sondeo.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
