"""Vigia de precios: lee los feeds publicos de Promodescuentos y abre un aviso (issue de GitHub,
que llega por correo) cuando aparece una oferta nueva de un producto vigilado bajo su tope.

Sin dependencias: solo la biblioteca estandar de Python. Corre en GitHub Actions cada 15 minutos.
Uso local de prueba:  python vigia.py --seco [--todo]
"""
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------------------------
# Lo que se vigila. Para agregar un producto: copiar un renglon y cambiar nombre, patron y topes.
# El primero que coincida con el titulo gana, asi que lo mas especifico va arriba.
# ---------------------------------------------------------------------------------------------
PRODUCTOS = [
    {"nombre": "Fold8 Ultra", "patron": r"fold\s*-?\s*8\s*ultra|fold\s*ultra\s*8|zfold8\s*ultra",
     "ganga": 22000, "comprar": 24000, "tope": 26000, "piso": 12000},
    {"nombre": "Fold8", "patron": r"z?\s*fold\s*-?\s*8\b|zfold8",
     "ganga": 22000, "comprar": 24000, "tope": 26000, "piso": 12000},
]
# Palabras que delatan accesorios o equipos usados: esos no se avisan.
EXCLUIR = r"\b(funda|mica|protector|case|reacondicionad[oa]|seminuevo|usado|refurbished|open\s*box)\b"

BANCOS_PROPIOS = {"BBVA": r"bbva|bancomer", "Revolut": r"revolut", "PayPal": r"paypal"}
BANCOS_AJENOS = {"Banorte": r"banorte", "Santander": r"santander", "Banamex": r"banamex|citibanamex",
                 "HSBC": r"hsbc", "Scotiabank": r"scotiabank", "Amex": r"amex|american\s*express",
                 "Inbursa": r"inbursa", "Afirme": r"afirme", "BanCoppel": r"bancoppel",
                 "Mercado Pago": r"mercado\s*pago", "Nu": r"\bnu\b", "Hey Banco": r"hey\s*banco"}

FEEDS = [
    "https://www.promodescuentos.com/rss/grupo/smartphones-samsung",
    "https://www.promodescuentos.com/rss/grupo/samsung",
    "https://www.promodescuentos.com/rss/grupo/celulares",
    "https://www.promodescuentos.com/rss/tendencias",
    "https://www.promodescuentos.com/rss/todas",
]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 vigia-precios/1.0"
ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visto.json")
DIAS_FRESCO = 3          # una oferta mas vieja que esto no se avisa aunque sea nueva para el vigia
FALLOS_PARA_AVISAR = 8   # corridas seguidas sin poder leer ningun feed (8 x 15 min = 2 horas)
MX = dt.timezone(dt.timedelta(hours=-6))


def leer_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raiz = ET.fromstring(r.read())
    salida = []
    for it in raiz.iter("item"):
        d = {"titulo": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
             "guid": (it.findtext("guid") or it.findtext("link") or "").strip(),
             "fecha": it.findtext("pubDate") or "", "descripcion": it.findtext("description") or "",
             "tienda": "", "precio_txt": ""}
        for hijo in it:
            if hijo.tag.endswith("}merchant") or hijo.tag == "merchant":
                d["tienda"] = hijo.attrib.get("name", "")
                d["precio_txt"] = hijo.attrib.get("price", "")
        salida.append(d)
    return salida


def a_numero(txt):
    m = re.search(r"\$?\s*([\d][\d,\.]*)", txt or "")
    if not m:
        return None
    crudo = m.group(1).replace(",", "")
    try:
        return float(crudo)
    except ValueError:
        return None


def limpio(texto, tope=700):
    t = re.sub(r"<br\s*/?>", "\n", texto or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t).strip()
    return t[:tope]


def bancos(texto):
    propios = [n for n, p in BANCOS_PROPIOS.items() if re.search(p, texto, re.I)]
    ajenos = [n for n, p in BANCOS_AJENOS.items() if re.search(p, texto, re.I)]
    if propios:
        return "aplica con " + " o ".join(propios), True
    if ajenos:
        return "pide " + " o ".join(ajenos) + " (no la tienes)", False
    return "sin banco", True


def evaluar(item):
    """Devuelve None si no interesa, o un dict con el aviso armado."""
    titulo = item["titulo"]
    if re.search(EXCLUIR, titulo, re.I):
        return None
    prod = next((p for p in PRODUCTOS if re.search(p["patron"], titulo, re.I)), None)
    if not prod:
        return None
    precio = a_numero(item["precio_txt"])
    if precio is not None and (precio < prod["piso"] or precio > prod["tope"]):
        return None
    nota_banco, me_aplica = bancos(titulo + " " + limpio(item["descripcion"], 3000))
    if precio is None:
        nivel = "SIN PRECIO, REVISAR"
    elif not me_aplica:
        nivel = "SOLO CON OTRO BANCO"
    elif precio <= prod["ganga"]:
        nivel = "GANGA"
    elif precio <= prod["comprar"]:
        nivel = "COMPRAR"
    else:
        nivel = "MIRAR"
    precio_txt = "${:,.0f}".format(precio) if precio is not None else "sin precio"
    asunto = "{} {} en {} · {} · {}".format(prod["nombre"], precio_txt, item["tienda"] or "tienda", nivel, nota_banco)
    return {"asunto": asunto, "producto": prod, "precio": precio, "precio_txt": precio_txt, "nivel": nivel,
            "nota_banco": nota_banco, "item": item}


def cuerpo(av, dueno):
    it, p = av["item"], av["producto"]
    lineas = [
        "**{}**".format(it["titulo"]),
        "",
        "- Precio publicado: **{}** en {}".format(av["precio_txt"], it["tienda"] or "(tienda sin dato)"),
        "- Veredicto del vigia: **{}** ({})".format(av["nivel"], av["nota_banco"]),
        "- Tus topes para {}: ganga hasta ${:,}; comprar hasta ${:,}; mirar hasta ${:,}".format(
            p["nombre"], p["ganga"], p["comprar"], p["tope"]),
        "- Publicada: {}".format(it["fecha"]),
        "- Oferta: {}".format(it["link"]),
        "",
        "Lo que dice quien la publico:",
        "",
        "> " + limpio(it["descripcion"]).replace("\n", "\n> "),
        "",
        "Antes de pagar: abre la oferta y confirma que siga viva, que el precio no dependa de un banco que no tienes "
        "y que el vendedor sea Samsung o la tienda (no un tercero). El seguro Care+ se contrata aparte dentro de 30 dias.",
    ]
    if dueno:
        lineas += ["", "Aviso para @" + dueno]
    return "\n".join(lineas)


def abrir_issue(titulo, texto):
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and token):
        print("   (sin credenciales de GitHub: no se abre aviso)")
        return False
    datos = json.dumps({"title": titulo[:250], "body": texto}).encode("utf-8")
    req = urllib.request.Request("https://api.github.com/repos/%s/issues" % repo, data=datos, method="POST", headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vigia-precios/1.0", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print("   aviso abierto:", json.loads(r.read()).get("html_url"))
    return True


def es_fresco(fecha_txt, ahora):
    try:
        f = email.utils.parsedate_to_datetime(fecha_txt)
    except Exception:
        return True
    return (ahora - f) <= dt.timedelta(days=DIAS_FRESCO)


def main():
    seco = "--seco" in sys.argv
    todo = "--todo" in sys.argv
    prueba = os.environ.get("PRUEBA", "").lower() in ("1", "true", "yes")
    dueno = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    ahora = dt.datetime.now(dt.timezone.utc)

    try:
        with open(ESTADO, encoding="utf-8") as f:
            estado = json.load(f)
    except Exception:
        estado = {}
    antes = json.dumps(estado, sort_keys=True)
    vistos = estado.get("vistos", {})
    primera_vez = not estado.get("sembrado")

    items, leidos = {}, 0
    for url in FEEDS:
        try:
            lote = leer_feed(url)
            leidos += 1
            print("feed ok   %3d  %s" % (len(lote), url))
            for it in lote:
                items.setdefault(it["guid"], it)
        except Exception as e:
            print("feed FALLA      %s -> %s" % (url, e))

    if leidos == 0:
        estado["fallos"] = estado.get("fallos", 0) + 1
        print("ningun feed se pudo leer; fallos seguidos:", estado["fallos"])
        if estado["fallos"] >= FALLOS_PARA_AVISAR and not estado.get("ciego_avisado") and not seco:
            abrir_issue("El vigia de precios esta ciego: no puede leer el foro de ofertas",
                        "Lleva %d corridas seguidas sin poder leer ningun feed de Promodescuentos. Mientras siga asi no "
                        "avisara de ofertas.%s" % (estado["fallos"], ("\n\nAviso para @" + dueno) if dueno else ""))
            estado["ciego_avisado"] = True
    else:
        estado["fallos"] = 0
        estado["ciego_avisado"] = False

    avisos = []
    for guid, it in items.items():
        av = evaluar(it)
        if av and (todo or (guid not in vistos and es_fresco(it["fecha"], ahora))):
            avisos.append(av)
    print("ofertas vigiladas nuevas bajo tope:", len(avisos))

    if prueba:
        candidatos = [a for a in (evaluar(i) for i in items.values()) if a]
        if candidatos:
            av = candidatos[0]
            abrir_issue("PRUEBA del vigia (oferta ya vencida) · " + av["asunto"],
                        "Esto es una prueba para confirmar que el aviso te llega; la oferta de abajo ya vencio.\n\n"
                        + cuerpo(av, dueno))
        else:
            abrir_issue("PRUEBA del vigia: lee el foro pero hoy no hay ofertas vigiladas en el feed",
                        "Prueba de que el aviso llega. Feeds leidos: %d de %d.%s"
                        % (leidos, len(FEEDS), ("\n\nAviso para @" + dueno) if dueno else ""))

    for av in avisos:
        print(" *", av["asunto"], "|", av["item"]["link"])
        if seco:
            continue
        if not primera_vez:  # la primera corrida solo siembra: lo ya publicado no es noticia
            try:
                abrir_issue(av["asunto"], cuerpo(av, dueno))
            except Exception as e:
                print("   no se pudo abrir el aviso:", e)
                continue  # no se marca como visto: se reintenta en la siguiente corrida
        vistos[av["item"]["guid"]] = av["item"]["fecha"]

    if not seco:
        if leidos:
            estado["sembrado"] = True
        if len(vistos) > 600:  # solo guarda ofertas avisadas; aun asi, no dejar crecer sin fin
            vistos = dict(list(vistos.items())[-400:])
        estado["vistos"] = vistos
        if json.dumps(estado, sort_keys=True) != antes:  # solo se reescribe si algo cambio
            with open(ESTADO, "w", encoding="utf-8") as f:
                json.dump(estado, f, ensure_ascii=False, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
