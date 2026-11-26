"""Serie de precios de Amazon Mexico leida por pixeles de una grafica publica de Keepa.

Adaptado de buen-fin-2026/leer_keepa_pixeles.py (que usaba numpy y PIL) a biblioteca estandar: el PNG se
decodifica con zlib. Las graficas se bajan con bajar_keepa.py (amazon=1, new=1, bb=0, sin rango de ventas).

Que lee:
  - Amazon (vende Amazon Mexico): el relleno durazno que Keepa pinta debajo del precio de Amazon. La orilla de
    arriba del relleno es el precio; donde no hay relleno Amazon no tenia existencia (curva cortada).
  - New (el nuevo mas barato del marketplace): la linea morada. En estas graficas New coincide con Amazon cuando
    Amazon es el mas barato, asi que solo se toma como "tercero" cuando queda por debajo de Amazon o Amazon falta.

Calibracion: <png sin extension>.calib.json, escrita a mano leyendo los rotulos en la imagen:
  {"y": [[fila, precio], ...], "x": [[columna, "AAAA-MM-DD"], ...], "fin": "AAAA-MM-DD"}
  Las filas y columnas son las de las lineas de reticula que imprime el modo "grid".

Uso:
  py -X utf8 keepa_pixeles.py grid <png>         -> marco, filas y columnas de reticula, colores mas comunes
  py -X utf8 keepa_pixeles.py serie <png>        -> serie diaria (fecha, amazon, tercero) por consola
  py -X utf8 keepa_pixeles.py csv <producto> <variante> <asin> <png_largo> [<png_90>]  -> renglones CSV por consola
"""
import collections
import csv
import json
import math
import os
import struct
import sys
import zlib
from datetime import date, timedelta


# ------------------------------------------------------------------ PNG con zlib
def leer_png(ruta):
    with open(ruta, "rb") as f:
        b = f.read()
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "no es PNG"
    i, idat, pal, trns = 8, [], None, None
    while i < len(b):
        n, tipo = struct.unpack(">I4s", b[i:i + 8])
        datos = b[i + 8:i + 8 + n]
        if tipo == b"IHDR":
            w, h, prof, color, _, _, entrel = struct.unpack(">IIBBBBB", datos)
        elif tipo == b"PLTE":
            pal = [tuple(datos[k:k + 3]) for k in range(0, len(datos), 3)]
        elif tipo == b"IDAT":
            idat.append(datos)
        elif tipo == b"IEND":
            break
        i += 12 + n
    assert prof == 8 and entrel == 0, f"PNG no soportado (profundidad {prof}, entrelazado {entrel})"
    canales = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    crudo = zlib.decompress(b"".join(idat))
    bpp, paso = canales, w * canales
    filas, prev = [], bytearray(paso)
    p = 0
    for _ in range(h):
        filtro = crudo[p]
        lin = bytearray(crudo[p + 1:p + 1 + paso])
        p += 1 + paso
        if filtro == 1:
            for k in range(bpp, paso):
                lin[k] = (lin[k] + lin[k - bpp]) & 255
        elif filtro == 2:
            for k in range(paso):
                lin[k] = (lin[k] + prev[k]) & 255
        elif filtro == 3:
            for k in range(paso):
                a = lin[k - bpp] if k >= bpp else 0
                lin[k] = (lin[k] + ((a + prev[k]) >> 1)) & 255
        elif filtro == 4:
            for k in range(paso):
                a = lin[k - bpp] if k >= bpp else 0
                c = prev[k - bpp] if k >= bpp else 0
                bb = prev[k]
                pa, pb, pc = abs(bb - c), abs(a - c), abs(a + bb - 2 * c)
                pr = a if pa <= pb and pa <= pc else (bb if pb <= pc else c)
                lin[k] = (lin[k] + pr) & 255
        filas.append(lin)
        prev = lin
    img = []
    for lin in filas:
        if color == 3:
            img.append([pal[v] for v in lin])
        elif color == 2:
            img.append([tuple(lin[k:k + 3]) for k in range(0, paso, 3)])
        elif color == 6:   # RGBA sobre blanco
            fila = []
            for k in range(0, paso, 4):
                r, g, bl, al = lin[k:k + 4]
                fila.append(tuple(round(c * al / 255 + 255 * (1 - al / 255)) for c in (r, g, bl)))
            img.append(fila)
        elif color == 0:
            img.append([(v, v, v) for v in lin])
        else:
            img.append([(lin[k],) * 3 for k in range(0, paso, 2)])
    return img


# ------------------------------------------------------------------ colores (medidos en las graficas del 25 nov 2026)
def es_relleno(p):      # durazno del area de Amazon (degradado), tambien con reticula encima
    r, g, b = p
    return r >= 240 and 195 <= g <= 248 and 160 <= b <= 238 and r - b >= 12 and g - b >= 3


def es_naranja(p):      # linea de Amazon
    r, g, b = p
    return r >= 220 and 120 <= g <= 200 and b <= 90


def es_morado(p):       # linea New, sola o encimada a la de Amazon (mezcla cafe-grisacea)
    r, g, b = p
    if b >= 180 and b - r >= 40 and b - g >= 40:
        return True
    return 110 <= r <= 190 and 90 <= g <= 170 and 100 <= b <= 200 and abs(r - b) <= 45 and r - g >= 5 and b - g >= 0


def es_gris_reticula(p):
    r, g, b = p
    return abs(r - g) <= 6 and abs(g - b) <= 6 and 200 <= r <= 240


def marco(img):
    """Caja del area de trazo: columnas y filas del borde gris oscuro."""
    h, w = len(img), len(img[0])
    def oscuro(p):
        return abs(p[0] - p[1]) <= 8 and abs(p[1] - p[2]) <= 8 and p[0] < 200
    cols = [x for x in range(w) if sum(1 for y in range(h) if oscuro(img[y][x])) > h * 0.7]
    filas = [y for y in range(h) if sum(1 for x in range(w) if oscuro(img[y][x])) > w * 0.6]
    return min(cols), max(cols), min(filas), max(filas)


def grid(ruta):
    img = leer_png(ruta)
    h, w = len(img), len(img[0])
    x0, x1, y0, y1 = marco(img)
    print("tamano", w, h, "marco x", x0, x1, "y", y0, y1)
    filas = [y for y in range(y0 + 1, y1) if sum(1 for x in range(x0 + 1, x1) if es_gris_reticula(img[y][x]))
             > (x1 - x0) * 0.25]
    cols = [x for x in range(x0 + 1, x1) if sum(1 for y in range(y0 + 1, y1) if es_gris_reticula(img[y][x])
                                                  or (abs(img[y][x][0] - img[y][x][2]) <= 6 and img[y][x][0] < 200))
            > (y1 - y0) * 0.25]
    print("filas_reticula", filas)
    print("cols_reticula", cols)
    cnt = collections.Counter(img[y][x] for y in range(y0 + 1, y1) for x in range(x0 + 1, x1))
    print("colores", cnt.most_common(40))


# ------------------------------------------------------------------ calibracion y lectura
def ajuste(pares):
    n = len(pares)
    mx = sum(p[0] for p in pares) / n
    my = sum(p[1] for p in pares) / n
    sxx = sum((p[0] - mx) ** 2 for p in pares)
    m = sum((p[0] - mx) * (p[1] - my) for p in pares) / sxx
    return m, my - m * mx


def cargar_calib(ruta_png):
    with open(os.path.splitext(ruta_png)[0] + ".calib.json", encoding="utf-8") as f:
        c = json.load(f)
    my, by = ajuste([(fila, precio) for fila, precio in c["y"]])
    base = date.fromisoformat(c["x"][0][1])
    mx, bx = ajuste([(col, (date.fromisoformat(d) - base).days) for col, d in c["x"]])
    fin = date.fromisoformat(c["fin"])
    return (lambda y: my * y + by), (lambda x: base + timedelta(days=math.floor(mx * x + bx))), abs(my), fin


def columnas(img):
    """Por columna: fila de la orilla del relleno de Amazon (o None) y fila de la linea New (o None)."""
    x0, x1, y0, y1 = marco(img)
    out = {}
    for x in range(x0 + 2, x1 - 1):
        col = [img[y][x] for y in range(y0 + 1, y1)]
        # relleno: desde abajo hacia arriba hasta el primer blanco. La linea New (morada) puede cruzar el relleno
        # por debajo del precio de Amazon: se atraviesa, y la orilla es el relleno mas alto antes del blanco.
        top = None
        k = len(col) - 1
        racha = 0
        while k >= 0 and not all(c >= 250 for c in col[k]):
            if es_relleno(col[k]):
                racha += 1
                top = k
            k -= 1
        if racha < 3:
            top = None
        amz = None
        if top is not None:
            # la linea va pegada arriba del relleno: las filas no blancas entre el relleno y el blanco (max 4);
            # su centro es el precio (medido: la fila central cae en la reticula del precio exacto)
            lin = []
            j = top - 1
            while j >= 0 and not all(c >= 250 for c in col[j]) and not es_relleno(col[j]):
                lin.append(j)
                j -= 1
            if len(lin) > 4:   # una linea vertical (cambio de precio) tapa la columna: no se lee
                amz = None
            else:
                amz = (min(lin) + max(lin)) / 2 + y0 + 1 if lin else top + y0 + 1 - 1.5
        naranja = [j for j in range(len(col)) if es_naranja(col[j])]
        morado = [j for j in range(len(col)) if es_morado(col[j])]
        nuevo = None
        if morado and max(morado) - min(morado) <= 3:
            nuevo = (min(morado) + max(morado)) / 2 + y0 + 1
        # New es de un tercero solo si queda claramente abajo de Amazon (mas de 3 px) o si Amazon falta
        tercero = nuevo if nuevo is not None and (amz is None or nuevo > amz + 3) else None
        out[x] = (amz, tercero, bool(naranja))
    return out


def serie_diaria(ruta):
    img = leer_png(ruta)
    a_precio, a_fecha, pxv, fin = cargar_calib(ruta)
    cols = columnas(img)
    dias = collections.OrderedDict()
    for x, (amz, nuevo, nar) in sorted(cols.items()):
        d = a_fecha(x)
        if d > fin:
            continue
        e = dias.setdefault(d, {"amz": [], "new": []})
        if amz is not None:
            e["amz"].append(a_precio(amz))
        if nuevo is not None:
            e["new"].append(a_precio(nuevo))
    serie = collections.OrderedDict()
    for d, e in dias.items():
        def moda(v):
            if not v:
                return None
            v = sorted(v)
            return v[len(v) // 2]
        serie[d] = (moda(e["amz"]), moda(e["new"]))
    return serie, pxv


def redondo(v):
    return None if v is None else int(round(v / 10.0) * 10)


def imprimir_serie(ruta):
    serie, pxv = serie_diaria(ruta)
    print("pesos_por_pixel", round(pxv, 1))
    prev = None
    for d, (a, n) in serie.items():
        t = (redondo(a), redondo(n))
        if t != prev:
            print(d, "amazon", t[0], "new", t[1])
            prev = t


# ------------------------------------------------------------------ CSV
CAMPOS = ["fecha", "producto", "variante", "tienda", "precio", "lista", "condicion", "banco", "tipo_banco",
          "vendedor", "garantia_mx", "fuente", "url", "id", "nota"]
URL = ("https://graph.keepa.com/pricehistory.png?asin={asin}&domain=com.mx&width=1600&height=600&amazon=1&new=1"
       "&bb=0&salesrank=0&used=0&fba=0&fbm=0&range={rango}")


def renglones(producto, variante, asin, png_largo, png_90=None):
    serie, pxv = serie_diaria(png_largo)
    rango = {d: os.path.basename(png_largo).split("_")[1].split(".")[0] for d in serie}
    pxvs = {d: pxv for d in serie}
    if png_90:
        s90, p90 = serie_diaria(png_90)
        for d, v in s90.items():
            serie[d] = v
            rango[d] = "90"
            pxvs[d] = p90
        serie = collections.OrderedDict(sorted(serie.items()))
    salida = []
    for quien, k in (("Amazon México", 0), ("tercero", 1)):
        prev, prev_d, ultimo = None, None, None
        dias = list(serie.items())
        for i, (d, v) in enumerate(dias):
            a, n = v
            if k == 0:
                p = a
            else:   # filtrado por columna en columnas() y otra vez por dia: New abajo de Amazon o Amazon sin existencia
                p = n if n is not None and (a is None or n < a - 3 * pxvs[d]) else None
            p = redondo(p)
            corte = prev_d is not None and (d - prev_d).days > 1
            if p is None:
                if prev is not None and ultimo is not None:
                    pass
                continue
            nota_extra = ""
            if prev is None or corte:
                nota_extra = "; inicio de tramo con existencia" if k == 0 else "; inicio de tramo"
            if prev is None or corte or abs(p - prev) > 2 * pxvs[d]:
                salida.append({"fecha": d.isoformat(), "producto": producto, "variante": variante,
                               "tienda": "Amazon", "precio": p, "lista": "", "condicion": "", "banco": "",
                               "tipo_banco": "ninguno", "vendedor": quien,
                               "garantia_mx": "si" if k == 0 else "no", "fuente": "keepa",
                               "url": URL.format(asin=asin, rango=rango[d]),
                               "id": f"{asin}:{d.isoformat()}" + ("" if k == 0 else ":tercero"),
                               "nota": f"leído de gráfica por píxeles, ± ${math.ceil(pxvs[d])}" + nota_extra})
                prev = p
            prev_d = d
            # fin de tramo: el dia siguiente no tiene dato
            sig = dias[i + 1] if i + 1 < len(dias) else None
            sig_p = None
            if sig:
                sa, sn = sig[1]
                sig_p = sa if k == 0 else (sn if sn is not None and (sa is None or sn < sa - 3 * pxvs[sig[0]]) else None)
            if sig is not None and sig_p is None and salida and salida[-1]["fecha"] != d.isoformat():
                salida.append(dict(salida[-1], fecha=d.isoformat(), precio=p,
                                   id=f"{asin}:{d.isoformat()}" + ("" if k == 0 else ":tercero"),
                                   url=URL.format(asin=asin, rango=rango[d]),
                                   nota=f"leído de gráfica por píxeles, ± ${math.ceil(pxvs[d])}; último día del tramo"))
            elif sig is not None and sig_p is None and salida:
                salida[-1]["nota"] += "; último día del tramo"
            if sig is not None and sig_p is None:
                prev = None
    salida.sort(key=lambda r: (r["fecha"], r["vendedor"] != "Amazon México"))
    return salida


# ASIN confirmados por el titulo que imprime la grafica (25 nov 2026)
ASINS = {
    "ps5": [("digital", "B0FTP523RH"), ("digital", "B0CLQ729TY")],
    "fold8": [("ultra-512", "B0H92G9GNW"), ("ultra-512", "B0H773PLWP"), ("normal-512", "B0H76XJTG2"),
              ("normal-512", "B0H773GGWG"), ("normal-512", "B0H779SQRZ")],
    "fold7": [("512", "B0FBTK7ZVB"), ("512", "B0FBT97262")],
    "fold6": [("512", "B0D74CWNN5"), ("512", "B0D74NRTQR")],
}


# Banco de prueba del pronostico (encargo C9a): familias con varias generaciones. familia -> [(producto, variante, asin)]
# Confirmados por el titulo de la grafica el 25 nov 2026. Quedaron fuera por no tener serie de Amazon o por
# no juntar dos generaciones: Flip (solo Flip5 tiene Amazon), Switch OLED (sin Amazon), Xbox Series X B0HLQ4R5QT
# (un mes de datos).
ASINS_BANCO = {
    "sultra": [("s23ultra", "256", "B0BQXJTDRK"), ("s23ultra", "256", "B0BQXHMF3Y"),
               ("s24ultra", "256", "B0CQ7RGF7D"),
               ("s25ultra", "256", "B0DNTX93TX"), ("s25ultra", "256", "B0FB2TLPSH")],
    "fold5": [("fold5", "512", "B0CBTG1624")],
    "xbox": [("xboxs", "unica", "B08H71MRV7"), ("xboxs", "unica", "B0DG6G4WST"), ("xboxx", "unica", "B0DFZMY6RL")],
}
# Sin precio de lista aproximado: Keepa empieza despues del lanzamiento y sus primeros 60 dias no son el lanzamiento
# (S23 Ultra Verde: $23,750 sostenido, pero el lanzamiento se ve en $31,000 pocos dias).
SIN_LISTA = {"B0BQXJTDRK"}


def _graficas(aqui, asin):
    largo = os.path.join(aqui, "keepa", f"{asin}_3650.png")
    corto = os.path.join(aqui, "keepa", f"{asin}_90.png")
    if not os.path.exists(os.path.splitext(corto)[0] + ".calib.json"):
        corto = None
    return largo, corto


def _escribir(ruta, filas):
    filas.sort(key=lambda r: (r["fecha"], r["producto"], r["variante"], r["id"]))
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, lineterminator="\n")
        w.writeheader()
        w.writerows(filas)
    print(os.path.basename(ruta), len(filas), "renglones")


def escribir_csvs():
    aqui = os.path.dirname(os.path.abspath(__file__))
    for producto, lista in ASINS.items():
        filas = []
        for variante, asin in lista:
            filas += renglones(producto, variante, asin, *_graficas(aqui, asin))
        _escribir(os.path.join(aqui, f"keepa_{producto}.csv"), filas)


def lista_aproximada(serie, dias=60, minimo_dias=7):
    """Precio mas alto que Amazon sostuvo (al menos minimo_dias en total) en sus primeros `dias` dias de serie."""
    amz = [(d, a) for d, (a, _) in serie.items() if a is not None]
    if not amz:
        return None, None
    d0 = amz[0][0]
    cuenta = collections.Counter(redondo(a) for d, a in amz if (d - d0).days < dias)
    # precios vecinos (misma linea leida con 1 px de diferencia) se juntan
    grupos = collections.Counter()
    for p, n in cuenta.items():
        clave = next((g for g in grupos if abs(g - p) <= 60), p)
        grupos[clave] += n
    sostenidos = [p for p, n in grupos.items() if n >= minimo_dias]
    return d0, (max(sostenidos) if sostenidos else None)


def escribir_banco():
    aqui = os.path.dirname(os.path.abspath(__file__))
    dest = os.path.join(aqui, "banco")
    os.makedirs(dest, exist_ok=True)
    listas = []
    for familia, lista in ASINS_BANCO.items():
        filas = []
        for producto, variante, asin in lista:
            largo, corto = _graficas(aqui, asin)
            filas += renglones(producto, variante, asin, largo, corto)
            serie, _ = serie_diaria(largo)
            d0, lp = lista_aproximada(serie)
            if lp and asin not in SIN_LISTA:
                listas.append({"producto": producto, "variante": variante, "desde": d0.isoformat(), "lista": lp,
                               "fuente_url": URL.format(asin=asin, rango="3650"),
                               "cita": "aproximada: precio de Amazon al lanzamiento leído de la gráfica"})
        _escribir(os.path.join(dest, f"keepa_{familia}.csv"), filas)
    # listas.csv: se conservan los renglones de otras fuentes y se reemplazan los de estas graficas
    ruta = os.path.join(dest, "listas.csv")
    previas = []
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8", newline="") as f:
            previas = [r for r in csv.DictReader(f)
                       if not r["fuente_url"].startswith("https://graph.keepa.com/")
                       or r["fuente_url"] not in {x["fuente_url"] for x in listas}]
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["producto", "variante", "desde", "lista", "fuente_url", "cita"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(previas + listas)
    print("listas.csv", len(previas), "previos +", len(listas), "de Keepa")


def main():
    modo = sys.argv[1]
    if modo == "csvs":
        escribir_csvs()
        return
    if modo == "banco":
        escribir_banco()
        return
    if modo == "grid":
        grid(sys.argv[2])
    elif modo == "serie":
        imprimir_serie(sys.argv[2])
    elif modo == "csv":
        producto, variante, asin, largo = sys.argv[2:6]
        corto = sys.argv[6] if len(sys.argv) > 6 else None
        w = csv.DictWriter(sys.stdout, fieldnames=CAMPOS, lineterminator="\n")
        for r in renglones(producto, variante, asin, largo, corto):
            w.writerow(r)


if __name__ == "__main__":
    main()
