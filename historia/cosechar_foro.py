"""Historial de ofertas del foro Promodescuentos para los productos de la linea A.

Uso:
    py -X utf8 historia\\cosechar_foro.py                 rehace los cuatro CSV (foro_fold8.csv, foro_fold7.csv, ...)
    py -X utf8 historia\\cosechar_foro.py --producto fold7  solo uno
    py -X utf8 historia\\cosechar_foro.py --sin-red         solo con lo que ya esta en el cache
    py -X utf8 historia\\cosechar_foro.py --salida <carpeta>  donde van los CSV (por omision, esta carpeta historia)

  Cualquier otro producto, sin tocar este archivo (sale <salida>\\foro_<producto-id>.csv):
    py -X utf8 historia\\cosechar_foro.py --producto-id ipad-air --consulta "ipad air" [--consulta "ipad air m4"]
        --patron "ipad\\s*air" --piso 9000 [--excluir "<regex>"] [--variante "m4-128-wifi=\\bm4\\b.*\\b128"]
        [--tope-paginas 12] [--tope-hilos 300] [--pausa 2] --salida <carpeta>
    --patron: lo que debe decir el titulo del hilo; --piso: precio minimo creible (abajo = accesorio o error);
    --excluir: titulos que se descartan; --variante id=regex (repetible): la primera que case en el titulo, o en la
    descripcion si el titulo no dice; si ninguna casa, 'sin-dato'. Sin --variante, todo va a 'sin-dato'.

Fuente: la busqueda del foro (https://www.promodescuentos.com/search?q=...&sortBy=new&time_frame=0&page=N, toda la historia). Cada hilo de la
lista viene como JSON en el atributo data-vue3 del componente ThreadMainListItemNormalizer (titulo, precio,
tienda, fecha de publicacion, expirado, temperatura, cupon). La descripcion completa solo esta en la pagina del
hilo (window.__INITIAL_STATE__.threadDetail.threadDetails.preparedHtmlDescription); se baja para los hilos que
pasan el filtro. Si el foro responde con su reto antirrobots (403 o "Verificando que no seas un bot"), el
programa se detiene, escribe lo que lleve y lo dice: no se rodea.

Veredictos de revision a mano: <salida>\\foro_revision.csv (id,veredicto,motivo); se copian en la columna
'revision' de cada CSV en cada corrida, asi que rehacer los CSV no los pierde.
"""
import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import vigia  # noqa: E402  (pedir, decodificar, BANCOS_PROPIOS, BANCOS_AJENOS, TIENDAS_FORO)

CACHE = os.path.join(os.path.dirname(os.path.dirname(AQUI)), "_cache_A", "foro")
PAUSA = 2.0
TOPE_PAGINAS = 60
# Sin este filtro la busqueda solo devuelve el ultimo año (time_frame=365 por omision; opciones 365, 30, 7 y 0 =
# "Todas", leidas del HTML de la busqueda el 25 nov 2026).
FILTRO_FECHA = "&time_frame=0"
TOPE_HILOS = 300
BASE = "https://www.promodescuentos.com"
COLUMNAS = ["fecha", "producto", "variante", "tienda", "precio", "lista", "condicion", "banco", "tipo_banco",
            "vendedor", "garantia_mx", "fuente", "url", "id", "nota", "revision"]

OTRAS_MARCAS = r"\b(pixel|razr|motorola|moto|honor|xiaomi|huawei|vivo|oppo|oneplus|one\s*plus|tecno|infinix|zte|nubia)\b"
PRODUCTOS = {
    "fold8": {"consultas": ["fold8", "fold 8", "z fold8", "fold ultra"],
              "patron": r"fold\s*-?\s*8|zfold8|fold\s*ultra\s*8|\bz\s*fold\s*ultra\b|galaxy\s+fold\s*ultra",
              "minimo": 12000},
    "fold7": {"consultas": ["fold7", "fold 7", "z fold7"],
              "patron": r"fold\s*-?\s*7(?!\d)|zfold7", "minimo": 12000},
    "fold6": {"consultas": ["fold6", "fold 6", "z fold6"],
              "patron": r"fold\s*-?\s*6(?!\d)|zfold6", "minimo": 12000},
    "ps5": {"consultas": ["ps5 digital", "playstation 5 digital", "consola ps5", "playstation 5 slim"],
            "patron": r"play\s*station\s*®?\s*5|\bps5\b", "minimo": 5500},
}
# Abreviatura de tienda a partir del nombre que da el foro (merchantName) o del dominio del enlace.
TIENDAS = [("Samsung", r"samsung"), ("Amazon", r"amazon"), ("Walmart", r"walmart"), ("Bodega Aurrera", r"bodega|aurrera"),
           ("Sam's", r"sam'?s\s*club|sams\.com|\bsams\b"), ("Liverpool", r"liverpool"), ("Suburbia", r"suburbia"),
           ("Costco", r"costco"), ("Elektra", r"elektra"), ("Coppel", r"coppel"), ("Mercado Libre", r"mercado\s*libre|mercadolibre"),
           ("Telcel", r"telcel"), ("AT&T", r"at&t|att\.com"), ("Sears", r"sears"), ("Palacio", r"palacio"),
           ("Gameplanet", r"game\s*planet"), ("Sanborns", r"sanborns"), ("Claro Shop", r"claro\s*shop|claroshop"),
           ("Office Depot", r"office\s*depot"), ("Best Buy", r"best\s*buy"), ("Chedraui", r"chedraui"),
           ("Soriana", r"soriana"), ("HEB", r"\bheb\b"), ("AliExpress", r"aliexpress"), ("Doto", r"\bdoto\b"),
           ("Cyberpuerta", r"cyberpuerta"), ("Sony", r"sony|playstation\.com"), ("Mixup", r"mixup"),
           ("Sanborns", r"sanborns")]
NO_GARANTIA = (r"importad[oa]|importaci[oó]n|internacional|global\s+version|versi[oó]n\s+global|reacondicionad[oa]|"
               r"refurbished|renewed|seminuevo|usad[oa]s?\b|open\s*box|vendedor\s+tercero|\btercero\b|"
               r"desde\s+(estados\s+unidos|china|usa)|amazon\s+global")


class Bloqueo(Exception):
    pass


# ------------------------------------------------------------------------------- red y cache
_ultima = [0.0]


def bajar(url, archivo, sin_red):
    """Devuelve el texto de la pagina, del cache si esta; None si no esta y no hay red."""
    ruta = os.path.join(CACHE, archivo)
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            return f.read()
    if sin_red:
        return None
    espera = PAUSA - (time.time() - _ultima[0])
    if espera > 0:
        time.sleep(espera)
    codigo, texto = vigia.pedir(url)
    _ultima[0] = time.time()
    if codigo == 403 or "Verificando que no seas un bot" in texto:
        raise Bloqueo("%s respondio %s (%d caracteres) a las %s" % (url, codigo, len(texto),
                                                                    dt.datetime.now().strftime("%H:%M:%S")))
    if codigo != 200:
        print("  aviso: %s respondio %s" % (url, codigo))
        return None
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto)
    return texto


def nombre_cache(consulta, pagina):
    return "busqtodas_%s_p%02d.html" % (re.sub(r"\W+", "-", consulta.strip().lower()), pagina)


# ------------------------------------------------------------------------------- lectura
def hilos_de_busqueda(texto):
    salida = []
    for b in re.findall(r"data-vue3='([^']*)'", texto):
        if '"ThreadMainListItemNormalizer"' not in html.unescape(b[:200]):
            continue
        try:
            d = json.loads(html.unescape(b))
        except ValueError:
            continue
        th = (d.get("props") or {}).get("thread")
        if th and th.get("threadId"):
            salida.append(th)
    return salida


def detalle_de_hilo(texto):
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*\{", texto or "")
    if not m:
        return None
    try:
        estado, _ = json.JSONDecoder().raw_decode(texto, m.end() - 1)
    except ValueError:
        return None
    return estado.get("threadDetail")


def plano(texto):
    t = re.sub(r"<br\s*/?>|</p>|</li>", "\n", texto or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t]+", " ", t).strip()


# ------------------------------------------------------------------------------- clasificacion
def variante(producto, titulo, desc):
    cfg = PRODUCTOS.get(producto) or {}
    if cfg.get("generico"):
        for fuente in (titulo, desc):
            for vid, pat in cfg.get("variantes") or []:
                if re.search(pat, fuente or "", re.I):
                    return vid
        return "sin-dato"
    t = titulo.lower()
    if producto == "ps5":
        if re.search(r"\bpro\b", t):
            return "pro"
        if re.search(r"digital|sin\s+(lector|unidad)|ps5dig", t):
            return "digital"
        if re.search(r"\bdisco\b|est[aá]ndar|standard|con\s+(lector|unidad)|lector\s+de\s+disco", t):
            return "disco"
        return "sin-dato"
    cap = ""
    for fuente in (t, desc.lower()):
        hallazgos = []
        for nombre, pat in (("1tb", r"\b1\s*tb\b|1024\s*gb|\b1tb"), ("512", r"\b512\s*(gb)?\b"), ("256", r"\b256\s*(gb)?\b")):
            m = re.search(pat, fuente)
            if m:
                hallazgos.append((m.start(), nombre))
        if hallazgos:
            cap = min(hallazgos)[1]     # la primera capacidad que se nombra
            break
    if producto == "fold8":
        ultra = bool(re.search(r"ultra", t))
        if ultra and cap in ("512", "1tb"):
            return "ultra-" + cap
        if not ultra and cap == "512":
            return "normal-512"
        return "sin-dato"
    return cap or "sin-dato"


def tienda_de(th, titulo):
    nombre = ((th.get("merchant") or {}).get("merchantName") or "")
    host = th.get("linkHost") or ""
    prefijo = titulo.split(":", 1)[0] if ":" in titulo[:40] else ""
    for fuente in (nombre, host, prefijo):
        for corto, pat in TIENDAS:
            if fuente and re.search(pat, fuente, re.I):
                return corto
    return nombre or host or prefijo or "sin dato"


# Mismas expresiones que vigia.py, salvo Amex: alli 'amex' tambien casa dentro de "Banamex" (medido el 25 nov 2026).
# Se suman medios de pago que piden hilos del foro y no estan en vigia.py (todos ajenos para Saul).
BANCOS_AJENOS = dict(vigia.BANCOS_AJENOS, Amex=r"(?<!ban)amex|american\s*express",
                     Santander=r"santander|satander", Falabella=r"falabella", Klar=r"\bklar\b", Kueski=r"kueski",
                     DiDi=r"\bdidi\b", Mifel=r"mifel", Openbank=r"openbank", Invex=r"invex")
# Frases que dicen que el banco baja el precio *ademas* del publicado: no lo exigen.
BANCO_OPCIONAL = r"(puede[n]?|podr[ií]a[n]?)\s+bajar|baja[r]?\s+(a[uú]n\s+)?m[aá]s|sin\s+(tdc\s+|tarjeta\s+)?\w+\s*=|adicional"


def banco_de(titulo, desc):
    """Banco que exige el precio. En el titulo basta con nombrarlo; en la descripcion se pide que vaya con una
    frase de pago ('pagando con', 'con tarjeta', 'con <banco>') y que la frase no diga que es un descuento extra."""
    def hallar(texto, contexto):
        prop, ajen = [], []
        if contexto:
            frases = [f for f in re.split(r"(?<=[.!?])\s+|\n", texto) if not re.search(BANCO_OPCIONAL, f, re.I)]
            texto = "\n".join(frases)
        for dic, lista in ((vigia.BANCOS_PROPIOS, prop), (BANCOS_AJENOS, ajen)):
            for nombre, pat in dic.items():
                p = (r"(pagando|pago|pagar|con|tarjetas?|tdc|tdd|cr[eé]dito|d[eé]bito)\W+(\w+\W+){0,3}(" + pat + ")") if contexto else pat
                if re.search(p, texto, re.I):
                    lista.append(nombre)
        return prop, ajen
    prop, ajen = hallar(titulo, False)
    if not prop and not ajen:
        prop, ajen = hallar(desc, True)
    if prop:
        return " o ".join(prop), "propio"
    if ajen:
        return " o ".join(ajen), "ajeno"
    return "", "ninguno"


def condicion_de(th, titulo, desc):
    partes = []
    texto = titulo + "\n" + desc
    cupon = (th.get("voucherCode") or "").strip()
    if cupon:
        partes.append("cupón " + cupon)
    m = re.search(r"(\d{1,2})\s*(msi|meses\s+sin\s+intereses)", texto, re.I)
    if m:
        partes.append(m.group(0))
    if re.search(r"estudiante|\bepp\b", titulo, re.I):
        partes.append("tienda de estudiantes")
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+|\n", texto) if f.strip()]
    for clave in (r"bonificaci[oó]n|cashback|monedero|reembolso",
                  r"canje|trade[\s-]*in|entrega\s+tu\s+(equipo|celular)|programa\s+de\s+renovaci[oó]n",
                  r"con\s+plan|en\s+plan|plan\s+(telcel|at&t|max|libre|de\s+renta)|renta\s+mensual|amigo\s+kit"):
        for f in frases:
            if re.search(clave, f, re.I):
                f = re.sub(r"\s+", " ", f)
                partes.append(f if len(f) <= 90 else f[:90].rsplit(" ", 1)[0] + "…")
                break
    return "; ".join(partes)[:200]


def vendedor_de(texto):
    m = re.search(r"(vendido\s+(y\s+enviado\s+)?por|vende\s*:|vendedor\s*:)\s*([^\n.,;()]{2,60})", texto, re.I)
    if not m:
        return "sin dato"
    return re.split(r"\s+(pagando|con|en|a|y|desde|para)\b", m.group(3).strip(), maxsplit=1, flags=re.I)[0].strip(" “”\"'")


def garantia_de(tienda, titulo, desc, vendedor):
    texto = titulo + "\n" + desc
    if re.search(NO_GARANTIA, texto, re.I):
        return "no"
    if tienda in ("Mercado Libre", "AliExpress") and not re.search(r"tienda\s+oficial|samsung\s+store|sony\s+store", texto + " " + vendedor, re.I):
        return "no"
    return "sin dato"


def fecha_mx(ts):
    return dt.datetime.fromtimestamp(int(ts), vigia.MX).strftime("%Y-%m-%d")


def numero(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def formato(v):
    return ("%d" % v) if v == int(v) else ("%.2f" % v)


# ------------------------------------------------------------------------------- cosecha
def cosechar(producto, sin_red, salida, revision):
    cfg = PRODUCTOS[producto]
    vistos, bloqueo = {}, None
    try:
        for q in cfg["consultas"]:
            propios = set()
            for pag in range(1, cfg.get("tope_paginas", TOPE_PAGINAS) + 1):
                url = BASE + "/search?q=" + urllib.parse.quote(q) + "&sortBy=new" + FILTRO_FECHA + ("&page=%d" % pag if pag > 1 else "")
                texto = bajar(url, nombre_cache(q, pag), sin_red)
                if texto is None:
                    break
                hilos = hilos_de_busqueda(texto)
                nuevos = [h for h in hilos if h["threadId"] not in propios]
                if not nuevos:
                    break
                for h in nuevos:
                    propios.add(h["threadId"])
                    vistos.setdefault(h["threadId"], h)
            print("  %s | '%s': %d hilos" % (producto, q, len(propios)))
    except Bloqueo as e:
        bloqueo = str(e)
    # filtro por titulo y precio
    pasan = []
    for th in vistos.values():
        titulo = th.get("title") or ""
        precio = numero(th.get("price"))
        if not re.search(cfg["patron"], titulo, re.I) or precio is None or precio < cfg["minimo"]:
            continue
        if cfg.get("excluir") and re.search(cfg["excluir"], titulo, re.I):
            continue
        if cfg.get("generico"):
            pasan.append(th)
            continue
        if producto != "ps5" and (re.search(OTRAS_MARCAS, titulo, re.I) or not re.search(r"samsung|galaxy|\bz\s*fold|zfold", titulo + " " + ((th.get("merchant") or {}).get("merchantName") or ""), re.I)):
            continue
        pasan.append(th)
    pasan.sort(key=lambda h: -int(h.get("publishedAt") or 0))
    renglones = []
    for n, th in enumerate(pasan):
        tid = th["threadId"]
        detalle = None
        tope_hilos = cfg.get("tope_hilos", TOPE_HILOS)
        if n < tope_hilos and bloqueo is None:
            slug = th.get("titleSlug") or "hilo"
            try:
                detalle = detalle_de_hilo(bajar(BASE + "/ofertas/%s-%s" % (slug, tid), "hilo_%s.html" % tid, sin_red))
            except Bloqueo as e:
                bloqueo = str(e)
        elif n < tope_hilos:
            ruta = os.path.join(CACHE, "hilo_%s.html" % tid)
            if os.path.exists(ruta):
                detalle = detalle_de_hilo(open(ruta, encoding="utf-8").read())
        if detalle:
            desc = plano((detalle.get("threadDetails") or {}).get("preparedHtmlDescription") or "")
            url = detalle.get("canonicalUrl") or detalle.get("url")
            completa = True
        else:
            desc = plano(th.get("listCardDescription") or "")
            url = BASE + "/ofertas/%s-%s" % (th.get("titleSlug") or "hilo", tid)
            completa = False
        titulo = th.get("title") or ""
        precio = numero(th.get("price"))
        lista = numero(th.get("nextBestPrice"))
        tienda = tienda_de(th, titulo)
        banco, tipo = banco_de(titulo, desc)
        vendedor = vendedor_de(titulo + "\n" + desc)
        nota = "temperatura %s°; %s" % (formato(numero(th.get("temperature")) or 0),
                                        "expirado" if th.get("isExpired") else "vigente o sin marcar")
        if lista and lista > precio:
            nota += "; lista = precio anterior que declara quien publica"
        if not completa:
            nota += "; descripcion solo del listado (recortada)"
        renglones.append({
            "fecha": fecha_mx(th.get("publishedAt") or 0), "producto": producto,
            "variante": variante(producto, titulo, desc), "tienda": tienda, "precio": formato(precio),
            "lista": formato(lista) if lista and lista > precio else "", "condicion": condicion_de(th, titulo, desc),
            "banco": banco, "tipo_banco": tipo, "vendedor": vendedor,
            "garantia_mx": garantia_de(tienda, titulo, desc, vendedor), "fuente": "foro", "url": url, "id": tid,
            "nota": nota, "revision": revision.get(tid, ""),
        })
    renglones.sort(key=lambda r: (r["fecha"], r["id"]))
    ruta = os.path.join(salida, "foro_%s.csv" % producto)
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(renglones)
    print("  %s: %d hilos leidos, %d en el CSV -> %s" % (producto, len(vistos), len(renglones), ruta))
    return bloqueo


def leer_revision(salida):
    ruta = os.path.join(salida, "foro_revision.csv")
    if not os.path.exists(ruta):
        return {}
    with open(ruta, encoding="utf-8", newline="") as f:
        return {r["id"]: r["veredicto"] for r in csv.DictReader(f) if r.get("id")}


def main():
    global PAUSA
    ap = argparse.ArgumentParser()
    ap.add_argument("--producto", choices=sorted(PRODUCTOS))
    ap.add_argument("--sin-red", action="store_true")
    ap.add_argument("--salida", default=AQUI)
    # producto cualquiera, sin editar PRODUCTOS
    ap.add_argument("--producto-id")
    ap.add_argument("--consulta", action="append")
    ap.add_argument("--patron")
    ap.add_argument("--piso", type=float)
    ap.add_argument("--excluir")
    ap.add_argument("--variante", action="append", default=[])
    ap.add_argument("--tope-paginas", type=int, default=TOPE_PAGINAS)
    ap.add_argument("--tope-hilos", type=int, default=TOPE_HILOS)
    ap.add_argument("--pausa", type=float, default=PAUSA)
    a = ap.parse_args()
    PAUSA = max(a.pausa, 1.5)
    productos = [a.producto] if a.producto else ["fold8", "fold7", "fold6", "ps5"]
    if a.producto_id or a.consulta or a.patron:
        if not (a.producto_id and a.consulta and a.patron and a.piso is not None):
            ap.error("un producto nuevo pide --producto-id, --consulta, --patron y --piso")
        variantes = []
        for v in a.variante:
            if "=" not in v:
                ap.error("--variante va como id=regex: %r" % v)
            variantes.append(tuple(v.split("=", 1)))
        PRODUCTOS[a.producto_id] = {"consultas": a.consulta, "patron": a.patron, "minimo": a.piso,
                                    "excluir": a.excluir, "variantes": variantes, "generico": True,
                                    "tope_paginas": a.tope_paginas, "tope_hilos": a.tope_hilos}
        productos = [a.producto_id]
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(a.salida, exist_ok=True)
    revision = leer_revision(a.salida)
    inicio = time.time()
    for p in productos:
        bloqueo = cosechar(p, a.sin_red, a.salida, revision)
        if bloqueo:
            print("DETENIDO: el foro mostro su reto antirrobots: " + bloqueo)
            print("Lo ya bajado queda en el cache; los CSV escritos solo tienen lo leido hasta ahi.")
            sys.exit(2)
    print("tiempo: %.0f s" % (time.time() - inicio))


if __name__ == "__main__":
    main()
