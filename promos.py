#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""promos.py: cupones y formas de pago de las tiendas que lee el vigia (solo biblioteca estandar).

Pieza central: probar(carrito, codigos). Recibe un carrito de invitado de cualquier tienda (un adaptador con las
ordenes leer / poner / quitar, capturadas de la pagina real de esa tienda) y una lista de codigos leidos en alguna
fuente, y devuelve el total mas bajo que de verdad queda en el carrito. Reglas (cada una costo un reclamo de Saul):
  - cada codigo se prueba solo, con el carrito limpio: asi el resultado no depende del orden de la lista;
  - las combinaciones se arman desde el mejor y solo se conservan si bajan el total y todos siguen puestos;
  - al final se relee el carrito y se reporta lo que de verdad hay; nunca un total mayor que el de sin cupones;
  - ningun codigo se adivina ni se genera aqui: todos llegan de fuera, ya leidos en una fuente.

v1 (25 nov 2026, linea B), lo medido ese dia llevado a codigo:
  - Amazon: amazon_busqueda / amazon_ficha / amazon_precio restan el cupon de ficha que no pide nada (Prime, banco...).
  - Samsung: samsung_cerradas lee el precio de la tienda de estudiantes y de empleados como dato aparte.
  - Bancos de primera fuente: leer_bancos (Amazon /msi y /blackfriday, Samsung /offer/deals/), precio_con_fila, de_quien.
  - mejorar(...): una sola llamada desde vigia.main(); nunca lanza, nunca quita una oferta, nunca sube un precio.
  - sondeo(pedir): cada lectura nueva contra la red, para medirla desde la nube.

Pruebas: py promos.py --selftest        Sondeo: py promos.py --sondeo
"""
import contextlib
import datetime as dt
import html
import io
import itertools
import json
import re
import sys
import time

MARGEN = 1.0   # pesos: una diferencia menor que esto no cuenta como rebaja
MX = dt.timezone(dt.timedelta(hours=-6))


# ------------------------------------------------------------------------------- motor
def _leer(carrito):
    """(total, codigos puestos) releidos de la tienda. El adaptador da leer() o, si no, total() y puestos()."""
    if hasattr(carrito, "leer"):
        total, puestos = carrito.leer()
    else:
        total, puestos = carrito.total(), carrito.puestos()
    return float(total), list(puestos)


def _dejar_solo(carrito, conjunto):
    """Deja en el carrito exactamente ese conjunto (un codigo puede desplazar a otro al entrar)."""
    _, puestos = _leer(carrito)
    for c in puestos:
        if c not in conjunto:
            carrito.quitar(c)
    _, puestos = _leer(carrito)
    for c in conjunto:
        if c not in puestos:
            carrito.poner(c)
    return _leer(carrito)


def probar(carrito, codigos, segundos=60.0, margen=MARGEN, reloj=time.time):
    """Prueba los codigos en el carrito y devuelve un dict:
      base        total sin ningun codigo
      total       el mas bajo que quedo de verdad en el carrito (nunca mayor que base)
      cupones     codigos que dan ese total, en el orden en que quedaron aplicados (vacia si ninguno baja)
      solos       {codigo: total con ese codigo solo} de los que bajan
      probados    codigos que alcanzo a probar
      rechazados  codigos que la tienda no acepto o que no bajaron
      incompleto  True si el tope de tiempo corto la prueba (entonces puede faltar algun codigo)
      segundos    lo que tardo
    El tope de tiempo cubre las dos pasadas: la primera usa como mucho 60 % y la segunda el resto."""
    inicio = reloj()
    lista = []
    for c in codigos:
        if c and c not in lista:
            lista.append(c)
    base, puestos = _leer(carrito)
    for c in puestos:                       # un carrito nuevo puede traer algo puesto: la base es sin codigos
        carrito.quitar(c)
    if puestos:
        base, _ = _leer(carrito)
    solos, probados, rechazados, incompleto = {}, [], [], False
    # Pasada 1: cada codigo solo.
    for cod in lista:
        if reloj() - inicio > segundos * 0.6:
            incompleto = True
            break
        probados.append(cod)
        aceptado = carrito.poner(cod)
        total, puestos = _leer(carrito)
        if aceptado and cod in puestos and total < base - margen:
            solos[cod] = total
        else:
            rechazados.append(cod)
        for c in puestos:
            carrito.quitar(c)
    # Pasada 2: desde el mejor, se suma cada uno solo si todos quedan puestos y el total baja.
    mejor_total, mejor = base, []
    for cod in sorted(solos, key=lambda k: (solos[k], k)):
        if reloj() - inicio > segundos:
            incompleto = True
            break
        prueba = mejor + [cod]
        total, puestos = _dejar_solo(carrito, prueba)
        if all(c in puestos for c in prueba) and total < mejor_total - margen:
            mejor_total, mejor = total, prueba
    # Relectura: se reporta lo que de verdad queda.
    final, puestos = _dejar_solo(carrito, mejor)
    if abs(final - mejor_total) > margen or sorted(puestos) != sorted(mejor):
        mejor_total, mejor = final, puestos
    if mejor_total > base - margen:         # nada bajo (o el carrito quedo mas caro): vale el precio sin codigos
        mejor_total, mejor = base, []
    return {"base": base, "total": mejor_total, "cupones": list(mejor), "solos": solos, "probados": probados,
            "rechazados": rechazados, "incompleto": incompleto, "segundos": round(reloj() - inicio, 1)}


def nota_cupones(res):
    """Texto para la nota de la oferta: que codigo quedo aplicado, o que ninguno sirvio."""
    if res["cupones"]:
        partes = ["con cupones " + " + ".join(res["cupones"]), "sin cupon ${:,.0f}".format(res["base"])]
    elif res["probados"]:
        partes = ["ninguno de %d cupones probados baja el precio" % len(res["probados"])]
    else:
        partes = ["sin cupones que probar"]
    if res["incompleto"]:
        partes.append("prueba cortada por tiempo")
    return "; ".join(partes)


# ------------------------------------------------------------------------------- utilidades de texto
def _plano(s):
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s or "", flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _num(s):
    return float(s.replace(",", "").replace(" ", ""))


def _une(*partes):
    return "; ".join(p for p in partes if p)


def _vigia(importar=False):
    """El modulo vigia ya cargado (como programa o como modulo); si importar=True y no esta, lo importa."""
    for nombre in ("__main__", "vigia"):
        m = sys.modules.get(nombre)
        if m is not None and hasattr(m, "BANCOS_PROPIOS") and hasattr(m, "pedir"):
            return m
    if importar:
        try:
            import vigia
            return vigia
        except Exception:
            return None
    return None


# ------------------------------------------------------------------------------- Amazon: cupon de ficha
# Literal que vuelve condicionado un cupon: no se resta, va a la nota (medicion E2c, 25 nov 2026).
CONDICION = (r"\bprime\b|primera compra|primer pedido|cliente nuevo|nuevos clientes|planea y ahorra|suscri|"
             r"young adult|estudiante|con tarjeta|con tu tarjeta|kueski|quincenas|\bapp\b|aplicaci[oó]n|"
             r"\bbbva\b|bancomer|banamex|santander|banorte|hsbc|\bnu\b|\bamex\b|american express|mercado\s*pago|paypal")
TOPE_CUPON = 0.5   # un cupon que quitaria mas de la mitad del precio es error de lectura: no se resta


def _condiciones(literal):
    return sorted(set(m.group(0).lower() for m in re.finditer(CONDICION, literal or "", re.I)))


def _cupon_de(literal):
    """(pesos, porcentaje) de un literal de cupon: 'Aplica el cupon de $220.01' / '... de 10%'."""
    p = re.search(r"\$\s?([\d,]+(?:\.\d\d)?)", literal or "")
    q = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s?%", literal or "")
    return (_num(p.group(1)) if p else None), (float(q.group(1).replace(",", ".")) if q else None)


def _codigos_amazon(plano):
    """Codigos tecleables que la pagina anuncia ('Utiliza el cupon KUESKI30'), con sus condiciones."""
    out = []
    for m in re.finditer(r"(?i:cup[oó]n|c[oó]digo(?:\s+promocional)?)\s*:?\s*\"?([A-Z][A-Z0-9_\-]{3,20})\b", plano):
        cod = m.group(1)
        if not (re.search(r"\d", cod) or len(cod) >= 6) or cod in ("TERMINOS",):
            continue
        fin = plano.find(".", m.end())
        frase = plano[plano.rfind(".", 0, m.start()) + 1: fin + 1 if fin >= 0 else m.end() + 200].strip()
        if cod not in [c["codigo"] for c in out]:
            out.append({"codigo": cod, "literal": frase[:300], "condiciones": _condiciones(frase)})
    return out


def amazon_busqueda(bloque):
    """Cupon de un bloque de resultado de /s?k= (el mismo corte que vigia._leer_amazon). Devuelve dict:
    cupon_pesos, cupon_pct, paga_con_cupon ('Paga $X con cupon'), literal, condiciones, condicionado, codigos."""
    r = {"cupon_pesos": None, "cupon_pct": None, "paga_con_cupon": None, "literal": "", "condiciones": [],
         "condicionado": False, "codigos": []}
    c = re.search(r'data-component-type="s-coupon-component"(.*?)</span></span></span>', bloque or "", re.S)
    if c:
        lit = _plano(c.group(1).split(">", 1)[1] if ">" in c.group(1) else c.group(1))
        r["literal"] = lit
        paga = re.search(r"Paga \$([\d,]+\.\d\d)", lit)
        r["paga_con_cupon"] = _num(paga.group(1)) if paga else None
        d = re.search(r"cup[oó]n de descuento de ([^ ]+(?: %)?)", lit) or re.search(r"Ahorra ([\d.,]+ ?%)", lit)
        r["cupon_pesos"], r["cupon_pct"] = _cupon_de(d.group(1)) if d else (None, None)
        r["condiciones"] = _condiciones(lit)
        r["condicionado"] = bool(r["condiciones"])
    return r


def amazon_ficha(texto):
    """Cupon de la ficha /dp/ASIN sin sesion. Devuelve dict: precio (el 'precio a pagar' de la ficha; nunca el del
    usado), cupon_pesos, cupon_pct, literal, condiciones, condicionado, promo_id, codigos, usado (precio y vendedor de
    la oferta 'Usado' o de Amazon Remates de Almacen: solo informativo, NO cuenta como precio)."""
    texto = texto or ""
    r = {"precio": None, "cupon_pesos": None, "cupon_pct": None, "literal": "", "condiciones": [],
         "condicionado": False, "promo_id": "", "codigos": [], "usado": None}
    pr = re.search(r'apex-pricetopay-value.{0,800}?a-price-whole">([\d,]+).{0,200}?a-price-fraction">(\d\d)', texto, re.S)
    if pr:
        r["precio"] = _num(pr.group(1) + "." + pr.group(2))
    c = re.search(r'class="couponLabelText">\s*(.*?)<span class="a-declarative"', texto, re.S)
    if c:
        lit = _plano(c.group(1))
        cola = texto[c.end(): c.end() + 3000]
        det = re.search(r'id="importantDetails[^"]*"[^>]*>(.*?)</span>', cola, re.S)
        pid = re.search(r"/promotion/details/popup/([A-Z0-9]+)", cola)
        detalle = _plano(det.group(1)) if det else ""
        r["literal"] = lit + (" | " + detalle if detalle else "")
        r["cupon_pesos"], r["cupon_pct"] = _cupon_de(lit)
        r["condiciones"] = _condiciones(r["literal"])
        r["condicionado"] = bool(r["condiciones"])
        r["promo_id"] = pid.group(1) if pid else ""
    plano = _plano(texto)
    r["codigos"] = _codigos_amazon(plano)
    u = re.search(r"Usado: Como nuevo \$([\d,]+\.\d\d).{0,300}?Vendido por: ([^.]{3,60}?) (?:El empaque|Ver más|\$|Entrega)", plano)
    if u:
        r["usado"] = {"precio": _num(u.group(1)), "vendedor": u.group(2).strip()}
    return r


def amazon_precio(precio, promo):
    """(precio_final, nota). Resta solo un cupon en pesos o en % que el literal no condicione; lo condicionado va
    a la nota. Si la busqueda dice 'Paga $X con cupon', el final debe cuadrar con X a un peso. Nunca devuelve mas
    que el precio de entrada ni menos de la mitad (un cupon asi es error de lectura: se anota y no se resta)."""
    if precio is None or not promo:
        return precio, ""
    precio = float(precio)
    pesos, pct = promo.get("cupon_pesos"), promo.get("cupon_pct")
    cond = [c for c in promo.get("codigos") or [] if c.get("condiciones")]
    extra = "; ".join("codigo %s sin restar (pide %s)" % (c["codigo"], ", ".join(c["condiciones"])) for c in cond[:2])
    if not pesos and not pct:
        return precio, _une("cupon en la ficha sin cifra clara" if promo.get("literal") else "", extra)
    if pesos and pct:   # 'Ahorra 10 % hasta $500': no se sabe cual vale
        return precio, _une("cupon en la ficha sin cifra clara", extra)
    if promo.get("condicionado"):
        return precio, _une("cupon de ficha sin restar (pide %s)" % ", ".join(promo.get("condiciones") or []), extra)
    baja = round(pesos if pesos else precio * pct / 100.0, 2)
    final = round(precio - baja, 2)
    paga = promo.get("paga_con_cupon")
    if paga is not None and abs(final - paga) > 1.0:
        return precio, _une("cupon en la ficha sin cifra clara", extra)
    if baja <= 0 or final < precio * TOPE_CUPON:
        return precio, _une("cupon de ficha de ${:,.0f} sin restar: quitaria mas de la mitad (lectura dudosa)"
                            .format(baja), extra)
    return final, _une("con cupon de ficha ${:,.0f} (se recorta en la ficha); sin cupon ${:,.0f}".format(baja, precio),
                       extra)


# ------------------------------------------------------------------------------- bancos de primera fuente
# Copia de respaldo de vigia.BANCOS_PROPIOS / BANCOS_AJENOS (si vigia esta cargado, mandan las suyas).
BANCOS_PROPIOS = {"BBVA": r"bbva|bancomer", "Revolut": r"revolut", "PayPal": r"paypal"}
BANCOS_AJENOS = {"Banorte": r"banorte", "Santander": r"santander", "Banamex": r"banamex|citibanamex",
                 "HSBC": r"hsbc", "Scotiabank": r"scotiabank", "Amex": r"amex|american\s*express",
                 "Inbursa": r"inbursa", "Afirme": r"afirme", "BanCoppel": r"bancoppel",
                 "Mercado Pago": r"mercado\s*pago", "Nu": r"\bnu\b", "Hey Banco": r"hey\s*banco"}
NO_ES_BANCO = r"participantes|sin banco|sin nombre"
HORAS_BANCOS = 6
MINUTOS_CERRADAS = 60
PAUSA = 1.0
AMAZON_MSI = "https://www.amazon.com.mx/msi?ref_=footer_pay_ins"
AMAZON_BF = "https://www.amazon.com.mx/blackfriday"
SAMSUNG_DEALS = "https://www.samsung.com/mx/offer/deals/"
SAMSUNG_LIVE = "https://www.samsung.com/mx/offer/samsung-live/"
SAMSUNG_CYBER = "https://www.samsung.com/mx/offer/cyber"
TIENDAS_CERRADAS = {"estudiantes_mx": "tienda de estudiantes de Samsung", "empleados_mx": "tienda de empleados de Samsung"}
CERRADA_URL = "https://www.samsung.com/mx/multistore/%s/pd.%s/"


class _SinTiempo(Exception):
    """Se acabo el presupuesto de tiempo: lo que falte queda para la siguiente corrida."""


def _esperar(hay_tiempo):
    if hay_tiempo is not None and not hay_tiempo():
        raise _SinTiempo()


def _listas_bancos():
    v = _vigia()
    return (getattr(v, "BANCOS_PROPIOS", None) or BANCOS_PROPIOS), (getattr(v, "BANCOS_AJENOS", None) or BANCOS_AJENOS)


def _casa(patron, texto):
    # sin letra pegada por delante: 'amex' no casa dentro de 'Citibanamex'
    return re.search(r"(?<![a-záéíóúñ])(?:%s)" % patron, texto or "", re.I)


def bancos_en(texto):
    """Nombres de banco (los de vigia) que aparecen en el texto, propios primero."""
    propios, ajenos = _listas_bancos()
    return [n for n, p in list(propios.items()) + list(ajenos.items()) if _casa(p, texto)]


def de_quien(banco):
    """'propio' si es BBVA, Revolut o PayPal; 'ajeno' si es otro banco; '' si no es un banco ('tarjetas participantes')."""
    propios, _ = _listas_bancos()
    b = banco or ""
    if any(_casa(p, b) for p in propios.values()):
        return "propio"
    if not b.strip() or re.search(NO_ES_BANCO, b, re.I):
        return ""
    return "ajeno"


def _fila(banco, beneficio, tipo, url, valor=None, unidad=None, tope=None, minimo=None, meses=None, vigencia=""):
    return {"banco": banco, "beneficio": (beneficio or "")[:160], "tipo": tipo, "valor": valor, "unidad": unidad,
            "tope": tope, "minimo": minimo, "meses": list(meses or []), "vigencia": vigencia, "url": url}


def _plano_bancos(t):
    # sin centavos: '$1,000.00' partia la frase en el punto
    return re.sub(r"(\d)\.(\d\d)(?!\d)", r"\1", _plano(t))


def _vigencia(texto):
    m = re.search(r"\bdel? \d{1,2} (?:de \w+ )?al \d{1,2} de \w+(?: de \d{4})?|\bhasta el \d{1,2} de \w+(?: de \d{4})?",
                  texto or "", re.I)
    return m.group(0) if m else ""


def _fila_promo(banco, lit, url, vigencia=""):
    """Fila de descuento o bonificacion con su valor, tope y minimo leidos del literal."""
    tipo = "bonificacion" if re.search(r"bonific|cashback", lit, re.I) else "descuento"
    tope = re.search(r"(?:tope|m[aá]ximo)(?: acumulado)?(?: por cliente)?(?: de)?(?: hasta)? \$\s?([\d,]+)", lit, re.I)
    mini = re.search(r"(?:m[ií]nim[oa]s?|compras? (?:desde|mayores a|a partir de))(?: de)? \$\s?([\d,]+)", lit, re.I)
    ocupado = [m.span(1) for m in (tope, mini) if m]
    valor, unidad = None, None
    pct = re.search(r"(\d{1,2}(?:[.,]\d+)?) ?%", lit)
    if pct:
        valor, unidad = float(pct.group(1).replace(",", ".")), "%"
    else:
        for m in re.finditer(r"\$\s?([\d,]+)", lit):
            if m.span(1) not in ocupado:
                valor, unidad = _num(m.group(1)), "$"
                break
    return _fila(banco, lit, tipo, url, valor=valor, unidad=unidad, tope=_num(tope.group(1)) if tope else None,
                 minimo=_num(mini.group(1)) if mini else None, vigencia=_vigencia(lit) or vigencia)


MSI_AMAZON = (r"(?:[Hh]asta )?((?:\d{1,2}(?: y |, )?)+) meses sin intereses (?:exclusivos )?con ([^,.]+?)"
              r"(?=,| y hasta| en compras|\.|$)")
MSI_SAMSUNG = r"\b(?:hasta|a)\s+(\d{1,2})\s+(?:MSI|meses sin intereses)\b[^.!?|*•]{0,140}"


def filas_msi_amazon(t, url=AMAZON_MSI):
    plano, out, vistos = _plano_bancos(t), [], set()
    for m in re.finditer(MSI_AMAZON, plano):
        lit = m.group(0).strip(" ,.")
        if lit in vistos:
            continue
        vistos.add(lit)
        quien = m.group(2).strip()
        nombres = bancos_en(quien) or (["tarjetas participantes"] if "participantes" in quien.lower() else [])
        for b in nombres:
            out.append(_fila(b, lit, "msi", url, meses=[int(x) for x in re.findall(r"\d+", m.group(1))]))
    return out


def filas_promo_banco(t, url=AMAZON_BF):
    """Renglones de 'bonificacion' o '% de descuento con <banco>' de una pagina (solo los que nombran banco)."""
    plano, out = _plano_bancos(t), []
    vig = _vigencia(plano)
    for m in re.finditer(r"[^.]{0,160}(?:bonificaci[oó]n|cashback|\d ?% de descuento con|descuento directo)[^.]{0,160}\.?",
                         plano, re.I):
        lit = m.group(0).strip()
        for b in bancos_en(lit):
            out.append(_fila_promo(b, lit, url, vig))
    return out


def filas_msi_samsung(t, url=SAMSUNG_DEALS):
    plano, out, vistos = _plano_bancos(t), [], set()
    for m in re.finditer(MSI_SAMSUNG, plano, re.I):
        lit = m.group(0).strip(" ,")
        nombres = bancos_en(lit) or (["tarjetas participantes"] if "participantes" in lit.lower() else [])
        for b in nombres:
            if (b, m.group(1)) in vistos:
                continue
            vistos.add((b, m.group(1)))
            out.append(_fila(b, lit, "msi", url, meses=[int(m.group(1))]))
    return out


def _pagina(pedir, url, minimo, hay_tiempo, dormir, primera=False):
    _esperar(hay_tiempo)
    if not primera:
        dormir(PAUSA)
    e, t = pedir(url)
    if e != 200 or len(t or "") < minimo:
        raise RuntimeError("%s respondio %s de %d bytes" % (url, e, len(t or "")))
    return t


def lector_bancos_amazon(pedir, hay_tiempo=None, dormir=time.sleep):
    t = _pagina(pedir, AMAZON_MSI, 20000, hay_tiempo, dormir, primera=True)
    filas = filas_msi_amazon(t)
    t = _pagina(pedir, AMAZON_BF, 20000, hay_tiempo, dormir)   # el evento empieza el 26 nov; el 25 no traia renglones
    return filas + filas_promo_banco(t)


def lector_bancos_samsung(pedir, hay_tiempo=None, dormir=time.sleep):
    t = _pagina(pedir, SAMSUNG_DEALS, 2000, hay_tiempo, dormir, primera=True)
    return filas_msi_samsung(t) + filas_promo_banco(t, SAMSUNG_DEALS)


LECTORES_BANCOS = {"Amazon": lector_bancos_amazon, "Samsung": lector_bancos_samsung}   # sumar tiendas aqui


def _hora(ahora):
    return ahora.astimezone(MX).strftime("%Y-%m-%d %H:%M")


def _vencido(leido, ahora, minutos):
    try:
        t = dt.datetime.strptime(leido, "%Y-%m-%d %H:%M").replace(tzinfo=MX)
    except Exception:
        return True
    return (ahora - t).total_seconds() >= minutos * 60


def leer_bancos(pedir, estado, ahora, hay_tiempo=None, dormir=time.sleep, tiendas=None):
    """estado['promos_bancos'] = {'tiendas': {tienda: {'leido', 'filas', 'error'}}}. Cada tienda se relee si su
    lectura tiene 6 horas o mas; un fallo deja las filas anteriores y anota el error."""
    caja = estado.get("promos_bancos")
    if not isinstance(caja, dict) or not isinstance(caja.get("tiendas"), dict):
        caja = {"tiendas": {}}
        estado["promos_bancos"] = caja
    por = caja["tiendas"]
    for tienda, lector in LECTORES_BANCOS.items():
        if tiendas is not None and tienda not in tiendas:
            continue
        previo = por.get(tienda) if isinstance(por.get(tienda), dict) else {}
        if not _vencido(previo.get("leido"), ahora, HORAS_BANCOS * 60):
            continue
        try:
            filas = lector(pedir, hay_tiempo, dormir)
            por[tienda] = {"leido": _hora(ahora), "filas": filas, "error": ""}
        except _SinTiempo:
            break
        except Exception as ex:
            por[tienda] = {"leido": _hora(ahora), "filas": previo.get("filas") or [], "error": str(ex)[:160]}
    return por


def precio_con_fila(precio, fila):
    """Precio que quedaria con una fila de descuento o bonificacion (respeta minimo y tope); None si no aplica.
    Los meses sin intereses no bajan el precio."""
    try:
        if not fila or fila.get("tipo") not in ("descuento", "bonificacion") or precio is None:
            return None
        valor, unidad, precio = fila.get("valor"), fila.get("unidad"), float(precio)
        if valor is None or unidad not in ("%", "$"):
            return None
        if fila.get("minimo") and precio < float(fila["minimo"]):
            return None
        baja = precio * float(valor) / 100.0 if unidad == "%" else float(valor)
        if fila.get("tope"):
            baja = min(baja, float(fila["tope"]))
        if baja <= 0 or baja >= precio:
            return None
        return round(precio - baja, 2)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------- Samsung: tiendas cerradas
def precio_cerrada(t, sku):
    """{'precio', 'lista', 'sku_pagina'} de la ficha de una tienda cerrada, solo si la pagina es de ese mismo SKU
    (medido: el de 1 TB cae a la ficha del de 512; eso se descarta)."""
    m = re.search(r'"sku":"([^"]+)","offers":\{[^}]*"price":"(\d+(?:\.\d+)?)"', t or "")
    if not m or m.group(1) != sku or float(m.group(2)) <= 0:
        return None
    lista = re.search(r'newHybrisShopInfo = \{"price":"(\d+)"', t)
    return {"precio": float(m.group(2)), "lista": float(lista.group(1)) if lista else None, "sku_pagina": m.group(1)}


def samsung_cerradas(pedir, skus, hay_tiempo=None, dormir=time.sleep, tope=6):
    """{tienda: {sku: {'precio', 'lista', 'url'}}} de las tiendas de estudiantes y empleados. Tope de 6 peticiones
    y 1 s entre ellas. Devuelve (precios, errores, peticiones)."""
    precios, errores, n = {}, [], 0
    for tienda in TIENDAS_CERRADAS:
        for sku in skus:
            if n >= tope:
                return precios, errores, n
            _esperar(hay_tiempo)
            if n:
                dormir(PAUSA)
            n += 1
            url = CERRADA_URL % (tienda, sku)
            try:
                e, t = pedir(url)
            except Exception as ex:
                errores.append("%s %s: %s" % (tienda, sku, str(ex)[:60]))
                continue
            if e != 200:
                errores.append("%s %s: %s" % (tienda, sku, e))
                continue
            d = precio_cerrada(t, sku)
            if d:
                d["url"] = url
                precios.setdefault(tienda, {})[sku] = d
    return precios, errores, n


# ------------------------------------------------------------------------------- Costco: promociones de pago de la ficha
# Medido el 25 nov 2026: la ficha trae en potentialPromotions el codigo PP177 (allowedPaymentMethod "paypal") y la
# categoria ahorrosPaypal6000 "Ahorros Paypal de $6,000". El carrito de invitado rechaza el codigo porque pide tener
# elegida la forma de pago: se lee, no se prueba.
COSTCO_FICHA = "https://www.costco.com.mx/rest/v2/mexico/products/%s?fields=FULL&lang=es_MX&curr=MXN"
DIAS_PROMO_PAGO = 90   # una promocion de pago que empezo hace mas de esto no cuenta (PAYPAL100 es de 2021 y sigue listada)


def _fecha_utc(s):
    try:
        return dt.datetime.strptime((s or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def costco_promos(texto_ficha, ahora=None, url=""):
    """Filas (formato de bancos, mas 'codigo') de las promociones de pago de una ficha de Costco. La cifra solo sale
    de la propia ficha (categoria ahorrosPaypalNNNN) y va a la promocion PayPal mas reciente; las demas promociones de
    pago quedan con valor None (solo nota). Las vencidas contra `ahora` se descartan."""
    d = json.loads(texto_ficha) if isinstance(texto_ficha, str) else (texto_ficha or {})
    ahora = ahora or dt.datetime.now(dt.timezone.utc)
    monto = None
    for c in d.get("categories") or []:
        m = re.match(r"ahorrosPaypal(\d+)$", c.get("code") or "", re.I)
        if m:
            monto = (float(m.group(1)), c.get("name") or "Ahorros Paypal de $%s" % m.group(1))
            break
    vivas = []
    for p in d.get("potentialPromotions") or []:
        cod, medio = p.get("code") or "", p.get("allowedPaymentMethod") or ""
        if not cod or cod.startswith("ASOC-") or not (medio or p.get("paymentPromotionFlag")):
            continue
        ini, fin = _fecha_utc(p.get("startDate")), _fecha_utc(p.get("endDate"))
        if (fin and fin < ahora) or (ini and (ahora - ini).days > DIAS_PROMO_PAGO):
            continue
        vivas.append((ini or ahora, cod, medio, p))
    paypal = [v for v in vivas if v[2].lower() == "paypal"]
    con_cifra = max(paypal, key=lambda v: v[0])[1] if (paypal and monto) else None
    filas = []
    for ini, cod, medio, p in sorted(vivas, key=lambda v: v[1]):
        banco = "PayPal" if medio.lower() == "paypal" else (medio or "forma de pago sin nombre")
        vig = "%s a %s" % ((p.get("startDate") or "")[:10], (p.get("endDate") or "")[:10])
        if cod == con_cifra:
            f = _fila(banco, "%s (codigo %s, solo pagando con %s)" % (monto[1], cod, banco), "descuento", url,
                      valor=monto[0], unidad="$", vigencia=vig)
        else:
            f = _fila(banco, "promocion de pago %s sin cifra publica" % cod, "descuento", url, vigencia=vig)
        f["codigo"] = cod
        filas.append(f)
    return filas


def lector_costco(pedir, codigos, hay_tiempo=None, dormir=time.sleep, ahora=None, urls=None, tope=4):
    """Una peticion por codigo de producto (4 como maximo, 1 s entre ellas). Devuelve (productos, errores, incompleto)
    con productos = {codigo: {'filas': [...]}}."""
    productos, errores, incompleto = {}, [], False
    for i, cod in enumerate(list(codigos)[:tope]):
        if hay_tiempo is not None and not hay_tiempo():
            incompleto = True
            break
        if i:
            dormir(PAUSA)
        try:
            e, t = pedir(COSTCO_FICHA % cod)
            if e != 200:
                raise RuntimeError("respondio %s" % e)
            productos[cod] = {"filas": costco_promos(t, ahora, (urls or {}).get(cod) or "")}
        except Exception as ex:
            errores.append("%s: %s" % (cod, str(ex)[:60]))
    return productos, errores, incompleto


def _codigo_costco(url):
    m = re.search(r"/p/(\d+)", url or "")
    return m.group(1) if m else ""


def _skus_cerradas(prods):
    """Un SKU por producto y variante de Samsung: {sku: [prod, variante]}."""
    out, vistos = {}, set()
    for p in prods or []:
        for sku, info in ((p.get("tiendas") or {}).get("Samsung") or {}).get("skus", {}).items():
            if (p["id"], info[0]) not in vistos:
                vistos.add((p["id"], info[0]))
                out[sku] = [p["id"], info[0]]
    return out


# ------------------------------------------------------------------------------- mejorar (llamada unica de vigia)
PRESUPUESTO = 150.0       # segundos para todo mejorar()
MARGEN_PETICION = 30.0    # una peticion nueva solo arranca si le quedan estos segundos (es el timeout de vigia.pedir)
AHORRO_APARTE = 3000      # con otro banco o con la tienda cerrada: aviso aparte desde esta rebaja
AHORRO_PROPIO = 500       # con un banco o medio propio: aviso aparte desde esta rebaja; por debajo, solo la nota
BAJA_APARTE = 300         # un aviso aparte ya mandado vuelve a salir solo si baja esto mas
NOTA_MAX = 220            # nota mas larga que esto tras los anadidos: se omiten primero los de meses sin intereses
# Membresia Dorada de Costco, leido el 25 nov 2026 en https://www.costco.com.mx/membresias ("Individual Dorada $600")
COSTCO_MEMBRESIA = 600
CIERRE_PAGO = "No lo he probado: se ve al pagar."
CIERRE_CERRADA = ("No lo he probado: comprar ahí pide una cuenta de esa tienda (estudiantes y docentes se verifican "
                  "con su correo institucional).")


def _anade(extras, o, texto, meses=False):
    """Anota un anadido a la nota de la oferta; se compone al final (_componer) para poder recortar."""
    if texto:
        e = extras.setdefault(id(o), (o, [], []))
        lista = e[2] if meses else e[1]
        if texto not in lista:
            lista.append(texto)


def _componer(extras):
    for o, alta, meses in extras.values():
        base = o.get("nota") or ""
        alta, meses = [x for x in alta if x not in base], [x for x in meses if x not in base]
        nota = _une(base, *(alta + meses))
        if len(nota) > NOTA_MAX:   # lo que importa es el cupon y el precio alterno: los meses se omiten primero
            nota = _une(base, *alta)
        o["nota"] = nota


def _mejores(ofertas):
    m = {}
    for o in ofertas:
        try:
            p = float(o["precio"])
        except Exception:
            continue
        k = (o.get("tienda"), o.get("prod"), o.get("var"))
        if k not in m or p < float(m[k]["precio"]):
            m[k] = o
    return m


def _aviso(estado, seco, abrir_issue, avisos, o, clave, nuevo, requisito, pide, literal, url, cierre=CIERRE_PAGO):
    hechos = estado.setdefault("promos_avisado", {})
    if clave in hechos and nuevo > float(hechos[clave]) - BAJA_APARTE:
        return
    precio = float(o["precio"])
    asunto = "{} saldria en ${:,.0f} en {} con {} · aviso aparte (no cuenta para tu tope)".format(
        o.get("nombre") or o.get("var"), nuevo, o.get("tienda"), requisito)
    cuerpo = "\n".join([
        "**{}**".format(o.get("titulo") or o.get("nombre") or ""), "",
        "- Precio normal leído ahora en {}: **${:,.0f}** (este es el que cuenta para tu tope).".format(o.get("tienda"), precio),
        "- Con {}: **${:,.0f}**, ${:,.0f} menos.".format(requisito, nuevo, precio - nuevo),
        "- Qué pide: {}.".format(pide),
        "- Lo que dice la fuente: \"{}\" ({})".format(literal, url),
        "- Liga de la oferta: {}".format(o.get("url") or ""), "",
        cierre,
        "Este aviso va aparte del aviso de precio y no cuenta para tu tope."])
    av = {"clave": clave, "asunto": asunto, "cuerpo": cuerpo, "precio": nuevo, "tienda": o.get("tienda"),
          "prod": o.get("prod"), "var": o.get("var"), "requisito": requisito}
    print("  + APARTE " + asunto)
    if seco:
        avisos.append(av)
        return
    try:
        abrir_issue(asunto, cuerpo)
        hechos[clave] = nuevo
        avisos.append(av)
    except Exception as ex:
        print("   promos: no se pudo abrir el aviso aparte:", str(ex)[:120])


def _mejorar(ofertas, estado, prods, ahora, seco, pedir, abrir_issue, hay_tiempo, dormir, avisos):
    tiendas = set()
    for p in prods or []:
        tiendas.update((p.get("tiendas") or {}).keys())
    # Paso a: lecturas (cada una con su cache)
    try:
        leer_bancos(pedir, estado, ahora, hay_tiempo, dormir, tiendas)
    except Exception as ex:
        print("promos: bancos sin leer (%s)" % str(ex)[:100])
    caja = estado.get("promos") if isinstance(estado.get("promos"), dict) else {}
    estado["promos"] = caja
    skus = _skus_cerradas(prods)
    previo = caja.get("cerradas") if isinstance(caja.get("cerradas"), dict) else {}
    if skus and _vencido(previo.get("leido"), ahora, MINUTOS_CERRADAS):
        try:
            precios, errores, _ = samsung_cerradas(pedir, list(skus)[:3], hay_tiempo, dormir)
            caja["cerradas"] = {"leido": _hora(ahora), "variantes": skus, "error": "; ".join(errores)[:300],
                                "precios": precios if (precios or not errores) else previo.get("precios", {})}
        except _SinTiempo:
            pass
    # Costco: promociones de pago de la ficha de cada articulo ofrecido (las mejores de cada variante primero)
    costco = [o for o in ofertas if o.get("tienda") == "Costco"]
    orden, urls = [], {}
    for o in list(_mejores(costco).values()) + costco:
        c = _codigo_costco(o.get("url"))
        if c and c not in orden:
            orden.append(c)
            urls[c] = o.get("url") or ""
    previo = caja.get("costco") if isinstance(caja.get("costco"), dict) else {}
    if orden and _vencido(previo.get("leido"), ahora, MINUTOS_CERRADAS):
        leidos, errores, incompleto = lector_costco(pedir, orden, hay_tiempo, dormir, ahora, urls)
        productos = dict(previo.get("productos") or {}) if isinstance(previo.get("productos"), dict) else {}
        productos.update(leidos)   # un articulo que fallo conserva sus filas anteriores
        caja["costco"] = {"leido": previo.get("leido", "") if incompleto else _hora(ahora), "productos": productos,
                          "error": "; ".join(errores)[:300]}
    prod_costco = (caja.get("costco") or {}).get("productos") or {}
    filas = {t: (v.get("filas") or []) for t, v in (estado.get("promos_bancos") or {}).get("tiendas", {}).items()
             if isinstance(v, dict)}
    extras = {}
    # Paso b: nota de meses sin intereses con un banco propio (no cambia el precio)
    for o in ofertas:
        meses = {}
        for f in filas.get(o.get("tienda"), []):
            if f.get("tipo") == "msi" and f.get("meses") and de_quien(f.get("banco")) == "propio":
                meses[f["banco"]] = max(meses.get(f["banco"], 0), max(f["meses"]))
        for b in sorted(meses):
            _anade(extras, o, "hasta %d meses sin intereses con %s" % (meses[b], b), meses=True)
    # Paso c: avisos aparte, para la mejor oferta de cada tienda y variante
    for (tienda, pid, vid), o in sorted(_mejores(ofertas).items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        precio = float(o["precio"])
        propias = list(filas.get(tienda, []))
        if tienda == "Costco":
            propias += (prod_costco.get(_codigo_costco(o.get("url"))) or {}).get("filas") or []
        por_banco = {}
        for f in propias:
            if f.get("codigo") and f.get("valor") is None:
                _anade(extras, o, "hay promocion de pago %s sin cifra publica" % f["codigo"])
            nuevo = precio_con_fila(precio, f)
            llave = (f.get("banco"), f.get("codigo") or "")
            if nuevo is not None and nuevo < precio - MARGEN and (llave not in por_banco or nuevo < por_banco[llave][0]):
                por_banco[llave] = (nuevo, f)
        for (banco, codigo), (nuevo, f) in sorted(por_banco.items()):
            quien = de_quien(banco)
            clave = "%s|%s|%s|%s" % (tienda, pid, vid, banco + ("|" + codigo if codigo else ""))
            if quien == "ajeno" and precio - nuevo >= AHORRO_APARTE:
                _aviso(estado, seco, abrir_issue, avisos, o, clave, nuevo, "tarjeta %s" % banco,
                       "pagar con tarjeta %s, que no es de tus bancos (los tuyos: BBVA, Revolut, PayPal)" % banco,
                       f.get("beneficio", ""), f.get("url", ""))
            elif quien == "propio":
                if codigo:
                    _anade(extras, o, "con {} saldria en ${:,.0f} (codigo {}, hasta {}; no probado: pide elegir {} al "
                                      "pagar)".format(banco, nuevo, codigo, (f.get("vigencia") or "")[-10:], banco))
                    requisito, pide = "%s (codigo %s)" % (banco, codigo), (
                        "elegir %s como forma de pago al pagar; la promocion %s se aplica ahi" % (banco, codigo))
                else:
                    _anade(extras, o, "con {} saldria en ${:,.0f} (no probado: se ve al pagar)".format(banco, nuevo))
                    requisito, pide = "tu tarjeta %s" % banco, "pagar con tu tarjeta %s" % banco
                if precio - nuevo >= AHORRO_PROPIO:
                    _aviso(estado, seco, abrir_issue, avisos, o, clave, nuevo, requisito, pide,
                           f.get("beneficio", ""), f.get("url", ""))
        if tienda != "Samsung":
            continue
        cer = caja.get("cerradas") or {}
        variantes = cer.get("variantes") or {}
        for tc, nombre_tc in TIENDAS_CERRADAS.items():
            for sku, d in ((cer.get("precios") or {}).get(tc) or {}).items():
                if list(variantes.get(sku) or []) != [pid, vid]:
                    continue
                nuevo = float(d["precio"])
                if precio - nuevo >= AHORRO_APARTE:
                    _aviso(estado, seco, abrir_issue, avisos, o, "%s|%s|%s|%s" % (tienda, pid, vid, tc), nuevo,
                           "cuenta de la %s" % nombre_tc,
                           "una cuenta de la %s (se entra con el correo de tu cuenta Samsung)" % nombre_tc,
                           "precio de la ficha de esa tienda, sin cupones", d.get("url", ""), cierre=CIERRE_CERRADA)
                elif nuevo < precio - MARGEN:
                    _anade(extras, o, "{} ${:,.0f} sin cupones (pide cuenta)".format(nombre_tc, nuevo))
    for o in costco:
        _anade(extras, o, "membresia Dorada ${:,.0f} al año si no eres socio".format(COSTCO_MEMBRESIA))
    _componer(extras)


def mejorar(ofertas, estado, prods, ahora, seco, pedir, abrir_issue, reloj=time.monotonic, dormir=time.sleep,
            segundos=PRESUPUESTO):
    """Notas de meses, avisos aparte por banco y por tienda cerrada. Nunca lanza excepcion, nunca quita una oferta,
    nunca sube un precio (de hecho no cambia ninguno), y no pasa de `segundos` contados con `reloj`.
    Devuelve la lista de avisos aparte (dicts) mandados, o impresos en seco."""
    avisos = []
    try:
        copia = [dict(o) for o in ofertas]
        inicio = reloj()
    except Exception:
        return avisos
    hay_tiempo = lambda: reloj() - inicio + MARGEN_PETICION < segundos   # noqa: E731
    try:
        _mejorar(ofertas, estado, prods, ahora, seco, pedir, abrir_issue, hay_tiempo, dormir, avisos)
    except Exception as ex:
        print("promos: se omite el resto (%s)" % str(ex)[:120])
        try:
            for o, c in zip(ofertas, copia):
                o.clear()
                o.update(c)
        except Exception:
            pass
    try:   # candado final: ningun precio distinto del de entrada, ninguna oferta menos
        for o, c in zip(ofertas, copia):
            if o.get("precio") != c.get("precio"):
                o["precio"] = c.get("precio")
        if len(ofertas) < len(copia):
            ofertas[:] = copia
    except Exception:
        pass
    return avisos


# ------------------------------------------------------------------------------- sondeo desde la nube
def _cupones_pagina(t):
    v = _vigia(importar=True)
    if v is None or not hasattr(v, "cupones_en_texto"):
        return []
    plano = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")))
    salida = []
    for c in v.cupones_en_texto(plano) + v.cupones_en_texto(html.unescape(t or "")):
        if c not in salida:
            salida.append(c)
    return salida


def sondeo(pedir=None):
    """Corre contra la red cada lectura nueva y devuelve {paso: {'ok', 'http', 'segundos', 'dato'}}."""
    if pedir is None:
        pedir = _vigia(importar=True).pedir
    sku = "SM-F976BZKMLTM"

    def samsung_pag(e, t):
        return e == 200, "codigos %s; %d filas de meses" % (", ".join(_cupones_pagina(t)) or "ninguno", len(filas_msi_samsung(t)))

    def cerrada(e, t):
        d = precio_cerrada(t, sku)
        if d:
            return True, "precio ${:,.0f} (lista {})".format(d["precio"], d["lista"])
        m = re.search(r'"sku":"([^"]+)"', t or "")
        return False, "sin precio del mismo modelo (la pagina dice %s)" % (m.group(1) if m else "nada")

    def amazon_msi(e, t):
        f = filas_msi_amazon(t)
        return e == 200 and bool(f), "%d filas: %s" % (len(f), "; ".join("%s %s" % (x["banco"], x["meses"]) for x in f))

    def amazon_bf(e, t):
        f = filas_promo_banco(t)
        return e == 200 and len(t or "") > 20000, "%d bytes, %d renglones de banco%s" % (
            len(t or ""), len(f), (": " + "; ".join(x["beneficio"][:60] for x in f[:3])) if f else "")

    def costco(e, t):
        f = costco_promos(t) if e == 200 else []
        return e == 200, "%d promociones de pago: %s" % (len(f), "; ".join(
            "%s %s" % (x["codigo"], "${:,.0f}".format(x["valor"]) if x["valor"] else "sin cifra") for x in f))

    pasos = [("samsung_deals", SAMSUNG_DEALS, samsung_pag), ("samsung_live", SAMSUNG_LIVE, samsung_pag),
             ("samsung_cyber", SAMSUNG_CYBER, samsung_pag), ("costco_ficha_648324", COSTCO_FICHA % "648324", costco),
             ("cerrada_estudiantes", CERRADA_URL % ("estudiantes_mx", sku), cerrada),
             ("cerrada_empleados", CERRADA_URL % ("empleados_mx", sku), cerrada),
             ("amazon_msi", AMAZON_MSI, amazon_msi), ("amazon_blackfriday", AMAZON_BF, amazon_bf)]
    salida = {}
    for i, (nombre, url, resumen) in enumerate(pasos):
        if i:
            time.sleep(PAUSA)
        t0 = time.time()
        try:
            e, t = pedir(url)
            ok, dato = resumen(e, t)
            salida[nombre] = {"ok": bool(ok), "http": e, "segundos": round(time.time() - t0, 1), "dato": dato[:300]}
        except Exception as ex:
            salida[nombre] = {"ok": False, "http": None, "segundos": round(time.time() - t0, 1),
                              "dato": "error: %s" % str(ex)[:200]}
    return salida


# ------------------------------------------------------------------------------- pruebas sin red
class CarritoFalso:
    """Carrito de mentira para probar el motor. reglas = {codigo: (tipo, valor, grupo)}, tipo 'fijo', 'pct' o
    'sube'. Dos codigos del mismo grupo no conviven: el que entra desplaza al otro. Un codigo desconocido se
    rechaza y, con tira=True, ademas vacia el carrito (pasa en Samsung)."""

    def __init__(self, base, reglas, tira=False, paso=0.0):
        self.base, self.reglas, self.tira, self.paso = float(base), reglas, tira, paso
        self.aplicados, self.reloj_falso, self.llamadas = [], 0.0, 0

    def _tic(self):
        self.llamadas += 1
        self.reloj_falso += self.paso

    def reloj(self):
        return self.reloj_falso

    def leer(self):
        self._tic()
        total = self.base
        for c in sorted(self.aplicados):
            tipo, valor, _ = self.reglas[c]
            total += {"fijo": -valor, "pct": -self.base * valor / 100.0, "sube": valor}[tipo]
        return round(total, 2), list(self.aplicados)

    def poner(self, cod):
        self._tic()
        if cod not in self.reglas:
            if self.tira:
                self.aplicados = []
            return False
        grupo = self.reglas[cod][2]
        if grupo:
            self.aplicados = [c for c in self.aplicados if self.reglas[c][2] != grupo]
        if cod not in self.aplicados:
            self.aplicados.append(cod)
        return True

    def quitar(self, cod):
        self._tic()
        if cod in self.aplicados:
            self.aplicados.remove(cod)


class _CarritoSinLeer:
    """El mismo carrito falso pero con total() y puestos() separados, como lo entregan algunos adaptadores."""

    def __init__(self, falso):
        self.f = falso
        self.poner, self.quitar = falso.poner, falso.quitar

    def total(self):
        return self.f.leer()[0]

    def puestos(self):
        return list(self.f.aplicados)


# Recortes esenciales de medicion\amazon\muestras\ (paginas reales del 25 nov 2026), 600 caracteres o menos cada uno.
_APEX = ('<span class="a-price apex-pricetopay-value"><span class="a-price-whole">{}<span class="a-price-decimal">.'
         '</span></span><span class="a-price-fraction">{}</span></span>')
FICHA_CUPON = _APEX.format("34,394", "83") + (
    '<span id="couponTextpctch18" class="couponLabelText"> Aplica el cup&oacute;n de $220.01 <span class="a-declarative"'
    ' data-a-modal="{&quot;url&quot;:&quot;/promotion/details/popup/A29HLK1CVST1ZX&quot;}">T&eacute;rminos</span>'
    '</span><span id="importantDetailspctch18" class="a-color-secondary aok-hidden"> Oferta por tiempo limitado </span>')
FICHA_SIN = _APEX.format("38,171", "64")
FICHA_KUESKI = _APEX.format("12,499", "00") + (
    '<span class="a-size-base"> Al momento de pagar, selecciona "cambiar metodo de pago" y luego <b>Kueski Pay</b>. '
    'Utiliza el cupon <b>KUESKI30</b> para recibir hasta 30% OFF y 2 quincenas sin intereses en tu primera compra en '
    'quincenas.</span>')
FICHA_USADO = ('<div>Usado: Como nuevo $32,675.09 <span>Vendido por: Amazon Remates de Almacén</span> '
               '<span>Entrega GRATIS</span></div>')
BUSQ_CUPON = (
    '<span class="a-offscreen">$34,394.83</span><span data-component-type="s-coupon-component" class="rush-component">'
    '<span class="s-coupon-clipped aok-hidden"><span class="s-coupon-highlight-color">Paga $34,174.82</span> <span '
    'class="a-color-base">Se aplicó el cupón de descuento de $220.01</span></span><span class="s-coupon-unclipped">'
    '<span class="s-coupon-highlight-color">Paga $34,174.82</span> <span class="a-color-base"> con cupón</span>'
    '</span></span>')
BUSQ_SIN = '<span class="a-price" data-a-size="xl"><span class="a-offscreen">$12,499.00</span></span>'
MSI_BANCOS = ('<div class="text">Hasta<strong> 12 meses sin intereses </strong>con las tarjetas participantes, <br/>'
              '<strong>4 y 7 meses sin intereses exclusivos con Nu,</strong><br/><strong>15 meses sin intereses '
              'exclusivos con BBVA <br/></strong>y hasta <strong>18 y 24 meses sin intereses exclusivos con Banamex '
              '</strong>en compras elegibles.</div>')
# Sintetico (el 25 nov /blackfriday no traia renglones de banco): forma de la promo BBVA del Buen Fin, otro banco.
BF_BANCO = ('<div>Black Friday del 26 al 30 de noviembre de 2026</div><p>Obtén 10% de descuento con Banorte en compras '
            'mínimas de $8,000.00, descuento máximo de $2,500.00.</p><p>Bonificación de $1,500 con HSBC en compras '
            'desde $20,000.</p>')
DEALS_MSI = ('<li>• Meses Sin Intereses (MSI): hasta 24 MSI con tarjetas participantes como BBVA, Citibanamex, '
             'Banorte, HSBC y más</li><li>Obtén $1,000 con el cupón BF1000</li>')
# Recortes de medicion\costco\muestras\ficha_648324.json y ficha_648321.json (sin ASOC-*, LALITO10 ni KMA27).
COSTCO_648324 = (
    '{"code":"648324","categories":[{"code":"ahorrosPaypal6000","name":"Ahorros Paypal de $6,000"}],'
    '"potentialPromotions":[{"allowedPaymentMethod":"paypal","code":"PAYPAL100","endDate":"2027-01-01T05:59:00.000Z",'
    '"paymentPromotionFlag":false,"startDate":"2021-12-10T13:00:00.000Z"},{"code":"VS178","endDate":'
    '"2026-12-13T05:59:59.000Z","paymentPromotionFlag":true,"startDate":"2026-11-25T13:00:00.000Z"},'
    '{"allowedPaymentMethod":"paypal","code":"PP177","endDate":"2026-12-02T05:59:59.000Z","paymentPromotionFlag":false,'
    '"startDate":"2026-11-23T13:00:00.000Z"}]}')
COSTCO_648321 = (
    '{"code":"648321","categories":[{"code":"cos_1.3.1","name":"Celulares"}],"potentialPromotions":['
    '{"allowedPaymentMethod":"paypal","code":"PAYPAL100","endDate":"2027-01-01T05:59:00.000Z","paymentPromotionFlag":'
    'false,"startDate":"2021-12-10T13:00:00.000Z"},{"code":"KMA27","endDate":"2027-08-30T05:59:59.000Z",'
    '"paymentPromotionFlag":false,"startDate":"2026-09-28T13:00:00.000Z"},{"code":"VS178","endDate":'
    '"2026-12-13T05:59:59.000Z","paymentPromotionFlag":true,"startDate":"2026-11-25T13:00:00.000Z"}]}')
CERRADA_PD = ('<script type="application/ld+json">{"@type":"Product","sku":"%s","offers":{"@type":"Offer",'
              '"priceCurrency":"MXN","price":"%s"}}</script><script>var newHybrisShopInfo = {"price":"53499"}</script>')


class _Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _pedir_falso(paginas, reloj=None, paso=0.0, llamadas=None):
    """pedir de mentira: url -> (estado, texto) desde un dict (busca por pedazo de url); cuenta y avanza el reloj."""
    def pedir(url, *a, **k):
        if llamadas is not None:
            llamadas.append(url)
        if reloj is not None:
            reloj.t += paso
        for pedazo, resp in paginas.items():
            if pedazo in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return 404, ""
    return pedir


def selftest():
    reglas = {"FOLD8_20CLUB": ("pct", 20, "club"), "BFLIVESEM": ("pct", 10, "club"), "BF1000": ("fijo", 1000, ""),
              "LIVESEM": ("sube", 4000, ""), "CENTAVO": ("fijo", 0.5, "")}
    codigos = ["LIVESEM", "MUERTO", "BFLIVESEM", "BF1000", "FOLD8_20CLUB", "CENTAVO"]
    esperado = round(43999 - 43999 * 0.20 - 1000, 2)
    quieto = lambda: 0.0   # noqa: E731  reloj falso: el resultado no depende de la velocidad de la maquina
    vistos = set()
    for tira in (False, True):
        for orden in itertools.permutations(codigos):
            car = CarritoFalso(43999, reglas, tira=tira)
            r = probar(car, list(orden), reloj=quieto)
            vistos.add((r["total"], tuple(r["cupones"])))
            assert r["base"] == 43999 and not r["incompleto"], r
            assert sorted(car.aplicados) == sorted(r["cupones"]), (car.aplicados, r)   # el carrito quedo como se reporta
    assert vistos == {(esperado, ("FOLD8_20CLUB", "BF1000"))}, vistos           # 1,440 ordenes, un solo resultado
    assert set(r["solos"]) == {"FOLD8_20CLUB", "BFLIVESEM", "BF1000"} and "LIVESEM" in r["rechazados"]
    # sin codigos, y con uno que solo encarece: queda el precio base y el carrito limpio
    assert probar(CarritoFalso(100, reglas), [], reloj=quieto)["total"] == 100
    car = CarritoFalso(43999, reglas)
    r = probar(car, ["LIVESEM", "MUERTO"], reloj=quieto)
    assert (r["total"], r["cupones"], car.aplicados) == (43999, [], []), r
    assert "ninguno de 2" in nota_cupones(r)
    # un carrito que llega con un codigo ya puesto: la base se mide sin el
    car = CarritoFalso(43999, reglas)
    car.aplicados = ["LIVESEM"]
    r = probar(car, ["BF1000"], reloj=quieto)
    assert (r["base"], r["total"], r["cupones"]) == (43999, 42999, ["BF1000"]), r
    # tope de tiempo: corta, lo dice, y nunca reporta mas que la base
    car = CarritoFalso(43999, reglas, paso=1.0)
    r = probar(car, codigos, segundos=10, reloj=car.reloj)
    assert r["incompleto"] and r["total"] <= r["base"] and len(r["probados"]) < len(codigos), r
    assert "cortada por tiempo" in nota_cupones(r)
    # adaptador con total() y puestos() separados; cupones en el orden en que quedaron aplicados
    r = probar(_CarritoSinLeer(CarritoFalso(43999, reglas)), codigos, reloj=quieto)
    assert (r["total"], r["cupones"]) == (esperado, ["FOLD8_20CLUB", "BF1000"]), r
    assert nota_cupones(r) == "con cupones FOLD8_20CLUB + BF1000; sin cupon $43,999", nota_cupones(r)

    # --- Amazon: cupon de ficha y de busqueda (recortes reales)
    for nombre in ("FICHA_CUPON", "FICHA_SIN", "FICHA_KUESKI", "FICHA_USADO", "BUSQ_CUPON", "BUSQ_SIN", "MSI_BANCOS",
                   "BF_BANCO", "DEALS_MSI"):
        assert len(globals()[nombre]) <= 600, (nombre, len(globals()[nombre]))
    f = amazon_ficha(FICHA_CUPON)
    assert (f["precio"], f["cupon_pesos"], f["condicionado"], f["promo_id"]) == (34394.83, 220.01, False, "A29HLK1CVST1ZX"), f
    assert amazon_precio(f["precio"], f) == (34174.82, "con cupon de ficha $220 (se recorta en la ficha); sin cupon $34,395")
    f = amazon_ficha(FICHA_SIN)
    assert amazon_precio(f["precio"], f) == (38171.64, ""), amazon_precio(f["precio"], f)
    b = amazon_busqueda(BUSQ_CUPON)
    assert (b["cupon_pesos"], b["paga_con_cupon"], b["condicionado"]) == (220.01, 34174.82, False), b
    assert amazon_precio(34394.83, b)[0] == 34174.82
    b = amazon_busqueda(BUSQ_SIN)
    assert (b["cupon_pesos"], b["paga_con_cupon"]) == (None, None) and amazon_precio(12499.0, b) == (12499.0, "")
    f = amazon_ficha(FICHA_KUESKI)                                   # KUESKI30: condicionado, no se resta
    fin, nota = amazon_precio(f["precio"], f)
    assert fin == 12499.0 and "KUESKI30 sin restar" in nota and "primera compra" in nota, (fin, nota)
    prime = FICHA_CUPON.replace("cup&oacute;n de $220.01", "cup&oacute;n de $500.00 exclusivo para miembros Prime")
    fin, nota = amazon_precio(34394.83, amazon_ficha(prime))
    assert fin == 34394.83 and "sin restar (pide prime)" in nota, nota
    fin, nota = amazon_precio(34394.83, amazon_busqueda(BUSQ_CUPON.replace("Paga $34,174.82", "Paga $34,000.00")))
    assert (fin, nota) == (34394.83, "cupon en la ficha sin cifra clara"), nota       # no cuadra con 'Paga'
    fin, nota = amazon_precio(34394.83, amazon_ficha(FICHA_CUPON.replace("$220.01", "$20,000.00")))
    assert fin == 34394.83 and "mas de la mitad" in nota, nota                       # cupon absurdo
    f = amazon_ficha(FICHA_USADO)                                    # el usado de Remates nunca es precio
    assert f["precio"] is None and f["usado"]["vendedor"] == "Amazon Remates de Almacén", f
    assert amazon_precio(f["precio"], f) == (None, "")

    # --- bancos
    filas = filas_msi_amazon(MSI_BANCOS)
    assert [(x["banco"], x["meses"]) for x in filas] == [("tarjetas participantes", [12]), ("Nu", [4, 7]),
                                                         ("BBVA", [15]), ("Banamex", [18, 24])], filas
    bf = filas_promo_banco(BF_BANCO)
    assert [(x["banco"], x["tipo"], x["valor"], x["unidad"], x["tope"], x["minimo"]) for x in bf] == [
        ("Banorte", "descuento", 10.0, "%", 2500.0, 8000.0), ("HSBC", "bonificacion", 1500.0, "$", None, 20000.0)], bf
    assert bf[0]["vigencia"] == "del 26 al 30 de noviembre de 2026", bf[0]
    sam = filas_msi_samsung(DEALS_MSI)
    assert [(x["banco"], x["meses"]) for x in sam] == [("BBVA", [24]), ("Banorte", [24]), ("Banamex", [24]),
                                                       ("HSBC", [24])], sam
    assert all(set(x) == {"banco", "beneficio", "tipo", "valor", "unidad", "tope", "minimo", "meses", "vigencia", "url"}
               and len(x["beneficio"]) <= 160 for x in filas + bf + sam)
    foro = {"tipo": "descuento", "valor": 20, "unidad": "%", "tope": 1000, "minimo": 5000}   # BBVABF20 del Buen Fin
    assert precio_con_fila(34174.82, foro) == 33174.82, precio_con_fila(34174.82, foro)
    assert precio_con_fila(4999, foro) is None                                # no llega al minimo
    assert precio_con_fila(4000, dict(foro, minimo=None, tope=None)) == 3200.0
    assert precio_con_fila(34174.82, {"tipo": "msi", "meses": [15]}) is None  # los meses no bajan el precio
    assert [de_quien(x) for x in ("BBVA", "Revolut", "PayPal", "Banorte", "Citibanamex", "tarjetas participantes", "")] == \
        ["propio", "propio", "propio", "ajeno", "ajeno", "", ""]
    assert bancos_en("PAYPAL Citibanamex") == ["PayPal", "Banamex"]          # 'amex' no casa dentro de Citibanamex

    # --- leer_bancos: cache de 6 horas por tienda; un fallo deja las filas anteriores
    ahora = dt.datetime(2026, 11, 27, 18, 0, tzinfo=dt.timezone.utc)        # 12:00 en Mexico
    relleno = " " * 20001
    paginas = {"/msi": (200, MSI_BANCOS + relleno), "/blackfriday": (200, BF_BANCO + relleno),
               "offer/deals": (200, DEALS_MSI + relleno)}
    llamadas, est = [], {}
    leer_bancos(_pedir_falso(paginas, llamadas=llamadas), est, ahora, dormir=lambda s: None)
    t = est["promos_bancos"]["tiendas"]
    assert len(llamadas) == 3 and len(t["Amazon"]["filas"]) == 6 and len(t["Samsung"]["filas"]) == 4, (llamadas, t)
    assert t["Amazon"]["leido"] == "2026-11-27 12:00" and t["Amazon"]["error"] == ""
    leer_bancos(_pedir_falso(paginas, llamadas=llamadas), est, ahora + dt.timedelta(hours=5), dormir=lambda s: None)
    assert len(llamadas) == 3                                                # menos de 6 horas: no relee
    leer_bancos(_pedir_falso({"/msi": (503, "")}, llamadas=llamadas), est, ahora + dt.timedelta(hours=7),
                dormir=lambda s: None)
    assert len(t["Amazon"]["filas"]) == 6 and "503" in t["Amazon"]["error"], t["Amazon"]

    # --- tiendas cerradas: solo el mismo SKU; tope de 6 peticiones
    pag_c = {"pd.SM-F976BZKMLTM": (200, CERRADA_PD % ("SM-F976BZKMLTM", "35999")),
             "pd.SM-F976BZKVLTM": (200, CERRADA_PD % ("SM-F976BZKMLTM", "35999")),     # el 1 TB cae al de 512
             "pd.SM-F971BZKMLTM": (200, "<html>pagina generica</html>")}
    llamadas = []
    pr, err, n = samsung_cerradas(_pedir_falso(pag_c, llamadas=llamadas),
                                  ["SM-F976BZKMLTM", "SM-F976BZKVLTM", "SM-F971BZKMLTM", "SM-X"], dormir=lambda s: None)
    assert n == 6 and len(llamadas) == 6, llamadas
    assert sorted(pr) == ["empleados_mx", "estudiantes_mx"] and list(pr["estudiantes_mx"]) == ["SM-F976BZKMLTM"], pr
    assert pr["estudiantes_mx"]["SM-F976BZKMLTM"]["precio"] == 35999.0

    # --- Costco: promociones de pago de la ficha (recortes reales de las fichas 648324 y 648321)
    for nombre in ("COSTCO_648324", "COSTCO_648321"):
        assert len(globals()[nombre]) <= 600, (nombre, len(globals()[nombre]))
    cf = costco_promos(COSTCO_648324, ahora, "uc")
    assert [(x["banco"], x["codigo"], x["valor"], x["unidad"], x["vigencia"]) for x in cf] == [
        ("PayPal", "PP177", 6000.0, "$", "2026-11-23 a 2026-12-02"),
        ("forma de pago sin nombre", "VS178", None, None, "2026-11-25 a 2026-12-13")], cf   # PAYPAL100 (2021) fuera
    assert cf[0]["beneficio"].startswith("Ahorros Paypal de $6,000") and cf[0]["url"] == "uc" and len(cf[0]["beneficio"]) <= 160
    assert precio_con_fila(37999, cf[0]) == 31999.0
    assert [x["codigo"] for x in costco_promos(COSTCO_648321, ahora)] == ["VS178"]          # sin PayPal
    assert [x["codigo"] for x in costco_promos(COSTCO_648324, ahora + dt.timedelta(days=6))] == ["VS178"]   # PP177 vencida
    llamadas = []
    pc, err, inc = lector_costco(_pedir_falso({"/648324?": (200, COSTCO_648324), "/648321?": (200, COSTCO_648321)},
                                              llamadas=llamadas), ["648324", "648321", "648325", "648326", "648999"],
                                 dormir=lambda s: None, ahora=ahora)
    assert len(llamadas) == 4 and sorted(pc) == ["648321", "648324"] and len(err) == 2 and not inc, (llamadas, pc, err)
    assert _codigo_costco("https://www.costco.com.mx/Electronicos/Celulares/Samsung-Galaxy-Z-Fold-8-512GB-Grafito/p/648324") == "648324"

    # --- mejorar con ofertas de mentira (sin imprimir sus + APARTE: ensucian el registro de la nube)
    silencio = contextlib.redirect_stdout(io.StringIO())
    silencio.__enter__()
    prods = [{"id": "fold8", "tiendas": {"Amazon": {}, "Samsung": {"skus": {
        "SM-F976BZKMLTM": ["ultra-512", "Grafito", "u"], "SM-F976BZVMLTM": ["ultra-512", "Violeta", "u"],
        "SM-F976BZKVLTM": ["ultra-1tb", "Grafito", "u"]}}}}]

    def ofertas_falsas():
        return [{"prod": "fold8", "var": "ultra-512", "nombre": "Fold8 Ultra 512 GB", "tienda": "Samsung", "precio": 34199.2,
                 "url": "us", "vendedor": "Samsung", "nota": "con cupones FOLD8_20CLUB + BF1000", "titulo": "t",
                 "cupones": ["FOLD8_20CLUB", "BF1000"], "vigencia": "2026-12-01"},
                {"prod": "fold8", "var": "normal-512", "nombre": "Fold8 512 GB", "tienda": "Amazon", "precio": 34174.82,
                 "url": "ua", "vendedor": "Amazon México", "nota": "con cupon de ficha $220", "titulo": "t"},
                {"prod": "fold8", "var": "normal-512", "nombre": "Fold8 512 GB", "tienda": "Amazon", "precio": 36000.0,
                 "url": "ub", "vendedor": "Amazon México", "nota": "", "titulo": "t"}]

    def estado_fresco():
        return {"promos_bancos": {"tiendas": {
            "Amazon": {"leido": "2026-11-27 12:00", "error": "", "filas": [
                _fila("Banorte", "10% de descuento con Banorte", "descuento", "uf", valor=10, unidad="%", tope=5000),
                _fila("HSBC", "5% de descuento con HSBC", "descuento", "uf", valor=5, unidad="%"),
                _fila("BBVA", "20% con BBVA", "descuento", "uf", valor=20, unidad="%", tope=1000, minimo=5000),
                _fila("BBVA", "15 meses sin intereses exclusivos con BBVA", "msi", "uf", meses=[15])]},
            "Samsung": {"leido": "2026-11-27 12:00", "error": "", "filas": []}}},
            "promos": {"cerradas": {"leido": "2026-11-27 11:30", "error": "",
                                    "variantes": {"SM-F976BZKMLTM": ["fold8", "ultra-512"]},
                                    "precios": {"estudiantes_mx": {"SM-F976BZKMLTM": {"precio": 30999.0, "url": "ue"}},
                                                "empleados_mx": {"SM-F976BZKMLTM": {"precio": 33500.0, "url": "um"}}}}}}

    mandados, llamadas = [], []

    def abrir(asunto, cuerpo):
        mandados.append(asunto)
        return True

    of, est = ofertas_falsas(), estado_fresco()
    av = mejorar(of, est, prods, ahora, False, _pedir_falso({}, llamadas=llamadas), abrir, reloj=_Reloj(),
                 dormir=lambda s: None)
    assert llamadas == [], llamadas                                          # todo en cache: ninguna peticion
    assert [o["precio"] for o in of] == [34199.2, 34174.82, 36000.0] and len(of) == 3   # no cambia precios ni quita
    assert sorted(a["clave"] for a in av) == ["Amazon|fold8|normal-512|BBVA", "Amazon|fold8|normal-512|Banorte",
                                              "Samsung|fold8|ultra-512|estudiantes_mx"], av
    assert len(mandados) == 3 and all("aviso aparte (no cuenta para tu tope)" in a for a in mandados), mandados
    assert "Fold8 512 GB saldria en $30,757 en Amazon con tarjeta Banorte · aviso aparte (no cuenta para tu tope)" \
        in mandados, mandados
    banorte = next(a for a in av if a["requisito"] == "tarjeta Banorte")
    assert "No lo he probado: se ve al pagar." in banorte["cuerpo"] and "uf" in banorte["cuerpo"]
    assert "hasta 15 meses sin intereses con BBVA" in of[1]["nota"] and "hasta 15 meses" in of[2]["nota"], of
    assert "con BBVA saldria en $33,175 (no probado: se ve al pagar)" in of[1]["nota"], of[1]["nota"]
    assert "HSBC" not in of[1]["nota"] and not any("HSBC" in a for a in mandados)     # $1,709 menos: no avisa
    assert "tienda de empleados de Samsung $33,500 sin cupones (pide cuenta)" in of[0]["nota"], of[0]["nota"]
    assert "cupones" not in of[1] and "vigencia" not in of[1] and of[0]["cupones"] == ["FOLD8_20CLUB", "BF1000"]
    # no repite; vuelve solo si baja $300 mas
    av = mejorar(of, est, prods, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert av == [] and len(mandados) == 3, av
    of[1]["precio"] = 33774.82
    av = mejorar(of, est, prods, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert sorted(a["clave"] for a in av) == ["Amazon|fold8|normal-512|BBVA", "Amazon|fold8|normal-512|Banorte"], av
    # en seco: se imprime, no se manda ni se guarda
    est = estado_fresco()
    av = mejorar(ofertas_falsas(), est, prods, ahora, True, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert len(av) == 3 and len(mandados) == 5 and not est.get("promos_avisado"), (av, est.get("promos_avisado"))
    # con todas las lecturas fallando, las ofertas quedan identicas
    for falla in (RuntimeError("sin red"), (503, "")):
        of, base = ofertas_falsas(), ofertas_falsas()
        est = {}
        pag = {"http": falla}
        av = mejorar(of, est, prods, ahora, False, _pedir_falso(pag), abrir, reloj=_Reloj(), dormir=lambda s: None)
        assert of == base and av == [], (of, av)
        assert est["promos_bancos"]["tiendas"]["Amazon"]["error"], est
    # presupuesto: cada peticion tarda 40 s en el reloj falso; con 150 s no pasan de 3 y nada queda a medias
    reloj, llamadas = _Reloj(), []
    of, base = ofertas_falsas(), ofertas_falsas()
    av = mejorar(of, {}, prods, ahora, False, _pedir_falso({"http": (200, relleno)}, reloj, 40.0, llamadas), abrir,
                 reloj=reloj, dormir=lambda s: None)
    assert len(llamadas) == 3 and reloj.t <= PRESUPUESTO and of == base, (llamadas, reloj.t)
    # mejorar nunca lanza, aunque le lleguen basuras
    assert mejorar([{"precio": "x"}], {"promos_bancos": 5, "promos": 7}, [{"id": "p"}], ahora, True,
                   _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None) == []
    # frase del aviso de tienda cerrada: no dice "se ve al pagar"
    av = mejorar(ofertas_falsas(), estado_fresco(), prods, ahora, True, _pedir_falso({}), abrir, reloj=_Reloj(),
                 dormir=lambda s: None)
    est_av = next(a for a in av if a["clave"].endswith("estudiantes_mx"))
    assert CIERRE_CERRADA in est_av["cuerpo"] and "se ve al pagar" not in est_av["cuerpo"], est_av["cuerpo"]
    assert all(CIERRE_PAGO in a["cuerpo"] for a in av if not a["clave"].endswith("_mx"))
    # banco propio: aviso aparte solo desde $500; por debajo, solo la nota
    est = estado_fresco()
    est["promos_bancos"]["tiendas"]["Amazon"]["filas"] = [_fila("BBVA", "1% con BBVA", "descuento", "uf", valor=1, unidad="%")]
    of = ofertas_falsas()
    av = mejorar(of, est, prods, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert not any("BBVA" in a["clave"] for a in av) and "con BBVA saldria en $33,833" in of[1]["nota"], (av, of[1]["nota"])
    est["promos_bancos"]["tiendas"]["Amazon"]["filas"] = [_fila("BBVA", "2% con BBVA", "descuento", "uf", valor=2, unidad="%")]
    av = mejorar(ofertas_falsas(), est, prods, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert [a["clave"] for a in av if "BBVA" in a["clave"]] == ["Amazon|fold8|normal-512|BBVA"], av   # $683.50 menos
    # nota larga: los meses sin intereses se omiten primero; el precio alterno se queda
    of = ofertas_falsas()
    of[1]["nota"] = "x" * 180
    mejorar(of, estado_fresco(), prods, ahora, True, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert "meses sin intereses" not in of[1]["nota"] and "con BBVA saldria en $33,175" in of[1]["nota"], of[1]["nota"]
    assert "hasta 15 meses sin intereses con BBVA" in of[2]["nota"] and len(of[2]["nota"]) <= NOTA_MAX
    # Costco: nota y aviso aparte de PayPal en la mejor oferta; membresia en toda oferta de Costco
    url_c = "https://www.costco.com.mx/Electronicos/Celulares/Samsung-Galaxy-Z-Fold-8-512GB-Grafito/p/%s"
    of_c = [{"prod": "fold8", "var": "normal-512", "nombre": "Fold8 512 GB", "tienda": "Costco", "precio": 37999.0,
             "url": url_c % "648324", "vendedor": "Costco (pide membresia)", "nota": "descuento de tienda $7,000 hasta 2026-11-30",
             "titulo": "t"},
            {"prod": "fold8", "var": "normal-512", "nombre": "Fold8 512 GB", "tienda": "Costco", "precio": 38999.0,
             "url": url_c % "648325", "vendedor": "Costco (pide membresia)", "nota": "", "titulo": "t"},
            {"prod": "fold8", "var": "ultra-512", "nombre": "Fold8 Ultra 512 GB", "tienda": "Costco", "precio": 41999.0,
             "url": url_c % "648321", "vendedor": "Costco (pide membresia)", "nota": "", "titulo": "t"}]
    base_c = [dict(o) for o in of_c]
    pag_cc = {"/648324?": (200, COSTCO_648324), "/648325?": (200, COSTCO_648321), "/648321?": (200, COSTCO_648321)}
    est, llamadas = {}, []
    prods_c = [{"id": "fold8", "tiendas": {"Costco": {}}}]
    av = mejorar(of_c, est, prods_c, ahora, False, _pedir_falso(pag_cc, llamadas=llamadas), abrir, reloj=_Reloj(),
                 dormir=lambda s: None)
    assert len(llamadas) == 3 and est["promos"]["costco"]["leido"] == "2026-11-27 12:00", (llamadas, est["promos"])
    assert [o["precio"] for o in of_c] == [o["precio"] for o in base_c]
    assert "con PayPal saldria en $31,999 (codigo PP177, hasta 2026-12-02; no probado: pide elegir PayPal al pagar)" \
        in of_c[0]["nota"], of_c[0]["nota"]
    assert "hay promocion de pago VS178 sin cifra publica" in of_c[0]["nota"] and "VS178" in of_c[2]["nota"]
    assert all("membresia Dorada $600 al año si no eres socio" in o["nota"] for o in of_c), of_c
    assert "PayPal" not in of_c[1]["nota"]                                    # no es la mejor de su variante
    assert [(a["clave"], a["requisito"]) for a in av] == [("Costco|fold8|normal-512|PayPal|PP177", "PayPal (codigo PP177)")], av
    assert "PP177" in av[0]["cuerpo"] and CIERRE_PAGO in av[0]["cuerpo"]
    mejorar(of_c, est, prods_c, ahora + dt.timedelta(minutes=30), False, _pedir_falso(pag_cc, llamadas=llamadas), abrir,
            reloj=_Reloj(), dormir=lambda s: None)
    assert len(llamadas) == 3                                                 # menos de 60 minutos: no relee
    silencio.__exit__(None, None, None)

    # --- vigia: cupones_candidatos deja fuera los codigos de tienda cerrada (EPP)
    v = _vigia(importar=True)
    est_c = {"cupones_cosechados": {"EPP10_NOV": "2026-11-25", "CART5_EPP": "2026-11-25", "BF1000": "2026-11-25",
                                    "EPPLIVESEM": "2026-11-24"}}
    cand = v.cupones_candidatos({"id": "x", "tiendas": {"Samsung": {"cupones": "no_existe_selftest.txt"}}}, est_c)
    assert cand == ["BF1000"], cand
    assert v.MARCA_TIENDA_CERRADA == "EPP"
    assert all(u in v.PAGINAS_CUPON_SAMSUNG for u in (SAMSUNG_DEALS, SAMSUNG_LIVE, SAMSUNG_CYBER))
    print("promos selftest OK: motor de cupones (1,440 ordenes, un solo resultado, orden aplicado), cupon de ficha de "
          "Amazon, bancos, tiendas cerradas, PayPal de Costco, mejorar (precios intactos, presupuesto, avisos aparte "
          "sin repetir, umbral propio $500, notas recortadas), sin EPP")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--sondeo" in sys.argv:
        print(json.dumps(sondeo(), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
