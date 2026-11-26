"""Baja la grafica publica de historial de precios de Keepa (Amazon Mexico) por ASIN.

Uso:
  py -X utf8 bajar_keepa.py probar <ASIN> <rango> [param=valor ...]   -> guarda keepa/_prueba_<ASIN>_<rango>_<n>.png
  py -X utf8 bajar_keepa.py bajar <ASIN> [<ASIN> ...]                  -> keepa/<ASIN>_<RANGO_MAX>.png y keepa/<ASIN>_90.png

Biblioteca estandar. Pausa de 2 s entre peticiones. Si Keepa responde otra cosa que PNG, se anota y se sigue.
"""
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(AQUI, "keepa")
BASE = "https://graph.keepa.com/pricehistory.png"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) vigia-precios"
# Parametros medidos el 25 nov 2026 con la PS5 Digital (ver informe A3)
PARAMS = {"domain": "com.mx", "width": "1600", "height": "600", "amazon": "1", "new": "1", "bb": "0",
          "salesrank": "0", "used": "0", "fba": "0", "fbm": "0"}
# range=3650 es aceptado y Keepa recorta la grafica al primer dato del articulo (medido con un articulo de 2020):
# es el rango mas largo. bb=0 deja ver la linea naranja de Amazon y su relleno sin la del Buy Box encima.
RANGO_MAX = "3650"
PAUSA = 2.0


def url_de(asin, rango, extra=None):
    q = {"asin": asin}
    q.update(PARAMS)
    q["range"] = str(rango)
    if extra:
        q.update(extra)
    return BASE + "?" + urllib.parse.urlencode(q)


def pedir(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/png,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", e.read() or b""
    except Exception as e:  # red
        return 0, "", repr(e).encode()


def guardar(asin, rango, nombre, extra=None):
    os.makedirs(DIR, exist_ok=True)
    url = url_de(asin, rango, extra)
    cod, tipo, b = pedir(url)
    es_png = b[:8] == b"\x89PNG\r\n\x1a\n"
    ruta = os.path.join(DIR, nombre)
    if es_png:
        with open(ruta, "wb") as f:
            f.write(b)
    print(f"{nombre}\t{cod}\t{tipo}\t{len(b)} bytes\t{'png' if es_png else 'NO PNG'}\t{url}")
    return es_png


def main():
    modo = sys.argv[1]
    if modo == "probar":
        asin, rango = sys.argv[2], sys.argv[3]
        extra = dict(a.split("=", 1) for a in sys.argv[4:])
        n = 0
        while os.path.exists(os.path.join(DIR, f"_prueba_{asin}_{rango}_{n}.png")):
            n += 1
        guardar(asin, rango, f"_prueba_{asin}_{rango}_{n}.png", extra)
    elif modo in ("bajar", "largo", "corto"):   # largo = solo rango maximo; corto = solo 90 dias
        rangos = {"bajar": (RANGO_MAX, "90"), "largo": (RANGO_MAX,), "corto": ("90",)}[modo]
        primero = True
        for asin in sys.argv[2:]:
            for rango in rangos:
                if not primero:
                    time.sleep(PAUSA)
                primero = False
                guardar(asin, rango, f"{asin}_{rango}.png")


if __name__ == "__main__":
    main()
