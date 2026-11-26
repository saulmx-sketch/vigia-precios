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
import os
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
    paga = promo.get("paga_con_cupon")
    if pct and paga is None:   # E6/A5: Amazon suele topar el % en Terminos (no se lee): solo con "Paga $X" que cuadre
        return precio, _une("cupon de ficha de {:g} % sin restar: revisa el tope en la pagina".format(pct), extra)
    baja = round(pesos if pesos else precio * pct / 100.0, 2)
    final = round(precio - baja, 2)
    if paga is not None and abs(final - paga) > 1.0:
        return precio, _une("cupon en la ficha sin cifra clara", extra)
    if baja <= 0 or final < precio * TOPE_CUPON:
        return precio, _une("cupon de ficha de ${:,.0f} sin restar: quitaria mas de la mitad (lectura dudosa)"
                            .format(baja), extra)
    # E6b (N2): "sin cupón $N" se conserva tal cual: vigia.notas_compra lo convierte en "Sin cupones cuesta $N."
    return final, _une("ya restado un cupón de ${:,.0f} que se marca en la página del producto; sin cupón ${:,.0f}"
                       .format(baja, precio), extra)


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


def _vigencia(texto, renglon=True):
    """Fecha de un renglon: 'solo el 27 de noviembre' gana a un rango que el renglon arrastre de la pagina. Para la
    pagina entera (renglon=False) solo cuentan rangos y 'hasta el'."""
    rango_rx = r"\bdel? \d{1,2} (?:de \w+ )?al \d{1,2} de \w+(?: de \d{4})?|\bhasta el \d{1,2} de \w+(?: de \d{4})?"
    if not renglon:
        m = re.search(rango_rx, texto or "", re.I)
        return m.group(0) if m else ""
    m = re.search(r"\bsolo el \d{1,2} de [a-záéíóú]+(?: de \d{4})?", texto or "", re.I) or \
        re.search(rango_rx, texto or "", re.I) or re.search(r"\bel \d{1,2} de [a-záéíóú]+(?: de \d{4})?", texto or "", re.I)
    return m.group(0) if m else ""


# E6/A4: un renglon que nombra una categoria no es del telefono ni de la consola: no da cifra
CATEGORIA_AJENA = (r"\ben (?:moda|hogar|ropa|calzado|muebles|electrodom|pantallas|televis|l[ií]nea blanca|juguet|"
                   r"belleza|deportes|viajes|super)|seleccionad|excepto|pantallas|seg[uú]n el d[ií]a")


def _cifra_del_banco(banco, lit):
    """(valor, unidad) pegados al banco o a 'bonificacion de / descuento de'; (None, None) si no se ve inequivoca."""
    propios, ajenos = _listas_bancos()
    patron = dict(list(propios.items()) + list(ajenos.items())).get(banco, re.escape(banco))
    m = re.search(r"(\d{1,2}(?:[.,]\d+)?) ?% (?:de (?:descuento|bonificaci[oó]n) )?(?:adicional )?con "
                  r"(?:tus? |tarjetas? (?:de cr[eé]dito )?)?(?:" + patron + ")", lit, re.I)
    if m:
        return float(m.group(1).replace(",", ".")), "%"
    m = re.search(r"(?:bonificaci[oó]n|descuento) (?:de |del )?(\d{1,2}(?:[.,]\d+)?) ?%", lit, re.I)
    if m:
        return float(m.group(1).replace(",", ".")), "%"
    m = re.search(r"(?:bonificaci[oó]n|descuento) de \$\s?([\d,]+)", lit, re.I)
    if m:
        return _num(m.group(1)), "$"
    return None, None


def _fila_promo(banco, lit, url, vigencia="", solo=True):
    """Fila de descuento o bonificacion. Modo estricto (E6, A1-A4): solo lleva valor si el renglon nombra un solo
    banco, la cifra va pegada al banco o a 'bonificacion de / descuento de', y no nombra otra categoria. La fecha es la
    del renglon; si no trae ninguna, la de la pagina."""
    tipo = "bonificacion" if re.search(r"bonific|cashback", lit, re.I) else "descuento"
    tope = re.search(r"(?:tope|m[aá]ximo)(?: acumulado)?(?: a bonificar)?(?: por cliente)?(?: de)?(?: hasta)? \$\s?([\d,]+)",
                     lit, re.I)
    mini = re.search(r"(?:m[ií]nim[oa]s?(?: de compra)?|compras? (?:desde|mayores a|a partir de|de))(?: de)? \$\s?([\d,]+)",
                     lit, re.I)
    valor, unidad = _cifra_del_banco(banco, lit)
    if not solo or re.search(CATEGORIA_AJENA, lit, re.I):
        valor, unidad = None, None
    return _fila(banco, lit, tipo, url, valor=valor, unidad=unidad, tope=_num(tope.group(1)) if tope else None,
                 minimo=_num(mini.group(1)) if mini else None, vigencia=_vigencia(lit) or vigencia)


MSI_AMAZON = (r"(?:[Hh]asta )?(\d{1,2}(?:(?: y |, )\d{1,2})*) meses sin intereses (?:exclusivos )?con ([^,.]+?)"
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
    vig = _vigencia(plano, renglon=False)
    for m in re.finditer(r"[^.]{0,160}(?:bonificaci[oó]n|cashback|\d ?% de descuento con|descuento directo)[^.]{0,160}\.?",
                         plano, re.I):
        lit = m.group(0).strip()
        nombres = bancos_en(lit)
        for b in nombres:
            out.append(_fila_promo(b, lit, url, vig, solo=len(nombres) == 1))
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
    if not re.search(r"\bMSI\b|(?i:meses sin intereses)", t):   # E6/M11 (E11: "MSIE" de un reto ya no pasa): un reto antirrobots no borra las filas buenas
        raise RuntimeError("Samsung /offer/deals/ sin MSI ni meses sin intereses (%d bytes)" % len(t))
    return filas_msi_samsung(t) + filas_promo_banco(t, SAMSUNG_DEALS)


LECTORES_BANCOS = {"Amazon": lector_bancos_amazon, "Samsung": lector_bancos_samsung}   # Liverpool y BBVA: mas abajo
LECTORES_DE_BANCO = {"BBVA"}   # lectores del lado del banco: se leen aunque no sean una tienda de productos.json


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
        if tiendas is not None and tienda not in tiendas and tienda not in LECTORES_DE_BANCO:
            continue
        previo = por.get(tienda) if isinstance(por.get(tienda), dict) else {}
        if not _vencido(previo.get("leido"), ahora, HORAS_BANCOS * 60):
            continue
        try:
            filas = lector(pedir, hay_tiempo, dormir)
            if not filas and previo.get("filas"):   # E11: 200 con 0 filas (reto antirrobots grande) no borra lo bueno
                raise RuntimeError("respondio sin filas (antes %d): se conservan las anteriores" % len(previo["filas"]))
            for f in filas:
                f["vigente"] = vigente(f, ahora)
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
        if baja <= 0 or baja > precio * 0.5:   # E6/A2: una rebaja de mas de la mitad es lectura equivocada
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
        f["vigente"] = vigente(f, ahora)
        if banco == "PayPal":
            f["pide"] = "pide elegir PayPal al pagar"
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


# ------------------------------------------------------------------------------- vigencia de toda fila de banco
# Regla (25 nov 2026): una fila solo puede dar aviso o precio alterno si su fecha de fin se lee y no ha pasado. Las
# paginas de bancos conservan promociones vencidas (las de BBVA de ese dia eran todas del 13 al 17 de noviembre).
MESES_ES = {m: i + 1 for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                             "septiembre", "octubre", "noviembre", "diciembre"])}


def _dia(a, m, d):
    try:
        return dt.date(int(a), int(m), int(d))
    except Exception:
        return None


def rango(texto, ahora):
    """(inicio, fin) como fechas de un texto de vigencia; (None, None) si no se lee. Formas medidas: '13 al 17 de
    noviembre de 2026', 'del 26 al 30 de noviembre de 2026', '17 de noviembre' (año de `ahora`), 'hasta el 2 de
    diciembre', '2026-11-23 a 2026-12-02' (Costco), '17/11/26' (Liverpool)."""
    t = (texto or "").lower()
    hoy = ahora.astimezone(MX).date()
    iso = [_dia(*x) for x in re.findall(r"(\d{4})-(\d\d)-(\d\d)", t)]
    if any(iso):
        iso = [x for x in iso if x]
        return iso[0], iso[-1]
    m = re.search(r"\b(\d\d)/(\d\d)/(\d\d)\b", t)
    if m:
        d = _dia(2000 + int(m.group(3)), m.group(2), m.group(1))
        return d, d
    m = re.search(r"(\d{1,2})\s+(?:de\s+([a-z]+)\s+)?al\s+(\d{1,2})\s+de\s+([a-z]+)(?:\s+(?:de|del)\s+(\d{4}))?", t)
    if m and m.group(4) in MESES_ES:
        a = int(m.group(5)) if m.group(5) else hoy.year
        fin = _dia(a, MESES_ES[m.group(4)], m.group(3))
        return _dia(a, MESES_ES.get(m.group(2) or "", MESES_ES[m.group(4)]), m.group(1)), fin
    m = re.search(r"(hasta el\s+)?\b(\d{1,2})\s+de\s+([a-z]+)(?:\s+(?:de|del)\s+(\d{4}))?", t)
    if m and m.group(3) in MESES_ES:
        d = _dia(int(m.group(4)) if m.group(4) else hoy.year, MESES_ES[m.group(3)], m.group(2))
        return (None if m.group(1) else d), d
    return None, None


def vigente(fila, ahora):
    """True si la fecha de fin se lee y no ha pasado; False si ya paso o si su inicio legible aun no llega (E11);
    None si no se lee."""
    ini, fin = rango((fila or {}).get("vigencia"), ahora)
    if fin is None:
        return None
    hoy = ahora.astimezone(MX).date()
    if ini is not None and ini > hoy:
        return False
    return fin >= hoy


# ------------------------------------------------------------------------------- cuponeras (cosecha ampliada)
# Medidas el 25 nov 2026 con urllib (medicion\fuentes\INFORME.md): entregan codigos en la respuesta de un GET simple.
# El modulo de cupones de El Universal es el iframe publico de couponstudio. E6/M9: su llave `auth` ya no va escrita
# aqui: se lee en cada lectura de cuponeras del HTML publico de la pagina de El Universal; si no aparece, esas tres
# fuentes se saltan y las demas siguen.
EU_PAGINA = ("https://www.eluniversal.com.mx/descubre-y-compra/samsung-cupones-y-descuentos-15-off-exclusivo-en-"
             "galaxy-buds4-pro/")
CS = "https://api.couponstudio.com.br/cp/page/article/%d?auth={auth}&ctx=app"
FUENTES_EXTRA = [   # (nombre, url, tienda); intercaladas por sitio para que la pausa casi nunca haga esperar
    ("cupondescuento_samsung", "https://www.cupondescuento.com.mx/samsung.com", "Samsung"),
    ("eluniversal_cs_samsung", CS % 16, "Samsung"),
    ("cuponesmexico_samsung", "https://www.cuponesmexico.com.mx/tienda/samsung/", "Samsung"),
    ("cupondescuento_amazon", "https://www.cupondescuento.com.mx/amazon.com.mx", "Amazon"),
    ("eluniversal_cs_amazon", CS % 8, "Amazon"),
    ("cupondescuento_liverpool", "https://www.cupondescuento.com.mx/liverpool.com.mx", "Liverpool"),
    ("eluniversal_cs_liverpool", CS % 122, "Liverpool"),
    ("cupondescuento_costco", "https://www.cupondescuento.com.mx/costco.com.mx", "Costco"),
]
NO_ES_EXTRA = re.compile(r"^(SAMSUNG|GALAXY|ULTRA|FOLD\d?|FLIP\d?|MSI|IVA|MXN|BBVA|PAYPAL|SANTANDER|BANORTE|CUPON|CODIGO|"
                         r"DESCUENTO|GRATIS|ENVIO|SHEIN|AMAZON|LIVERPOOL|COSTCO|HERRAMIENTAS|"
                         # E6/M5: palabras comunes y marcas que siguen a "cupon/codigo" en mayusculas
                         r"EXCLUSIVOS?|VERIFICADOS?|NUEVOS?|ACTIVOS?|VALIDOS?|VIGENTES?|HONOR|REDMAGIC|BARRAS|OFERTAS?|"
                         r"PROMOCIONAL|ESPECIAL|HOY)$", re.I)
HORAS_CUPONERAS = 2      # E6/M4: las cuponeras se leen cada 2 horas, no en cada corrida
_COD = r"[A-Za-z0-9][A-Za-z0-9_\-]{3,21}"
PATRONES_EXTRA = [
    re.compile(r"(?i:cup[oó]n(?:es)?|c[oó]digo)(?:\s+(?i:de\s+descuento|promocional))?\s*[:\"“]?\s*([A-Z0-9][A-Z0-9_\-]{3,21})\b"),
    re.compile(r"(?i:cup[oó]n)\s*\d{1,2}\s*%\s*:\s*([A-Z0-9][A-Z0-9_\-]{3,21})\b"),       # Samsung: "Cupon 20%: PRO-20"
    re.compile(r"\d{1,2}\s*%\s+con\s+([A-Z][A-Z0-9]*[_\-][A-Z0-9_\-]{1,18})\b"),            # Samsung: "10% con HS26_ECO"
    re.compile(r"Añadido hace [\w ]{3,20}? (" + _COD + r") \1 "),                            # cupondescuento.com.mx
]
DATA_CODE = re.compile(r'data-code="(' + _COD + r')"')                                      # couponstudio y cuponesmexico
TOPE_CUPONERAS = 20.0    # E6/M3: era 25
MINIMO_CUPONERA = 5000   # respuesta de menos de esto (bytes): pagina de error o de robots, cuenta como fallo
DIAS_CODIGOS = 21
ULTIMA_COSECHA = {}      # informe de la ultima cosecha: leidas, segundos, fuentes, resumen (lo imprime vigia)


def codigos_en(texto):
    plano = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", texto or "")))
    salida = []
    for c in DATA_CODE.findall(texto or "") + [c for p in PATRONES_EXTRA for c in p.findall(plano)]:
        # letras obligatorias; o todo en mayusculas o con algun digito (asi caen palabras sueltas como "Copiar")
        if (re.search(r"[A-Za-z]", c) and (c.upper() == c or re.search(r"\d", c))
                and not NO_ES_EXTRA.match(c) and c.upper() not in [s.upper() for s in salida]):
            salida.append(c)
    return salida


def cosechar_extra(pedir, tope_s=TOPE_CUPONERAS, dormir=time.sleep, hay_tiempo=None, reloj=time.monotonic):
    """{tienda: {codigo: url}} de FUENTES_EXTRA. Un try por fuente; antes de cada peticion se revisa el tope total
    (tope_s) y, si quien llama da hay_tiempo, tambien ese; 1 s entre peticiones a un mismo sitio; una respuesta de
    menos de 5 KB cuenta como fallo. Deja su informe en ULTIMA_COSECHA."""
    inicio, salida, fuentes, ultimo, auth = reloj(), {}, {}, {}, None

    def sin_tiempo():
        return reloj() - inicio >= tope_s or (hay_tiempo is not None and not hay_tiempo())

    for nombre, url, tienda in FUENTES_EXTRA:
        if sin_tiempo():
            fuentes[nombre] = "sin tiempo"
            break
        if "{auth}" in url:
            if auth is None:   # llave del modulo de El Universal, leida de su pagina publica (una vez por lectura)
                try:
                    e, t = pedir(EU_PAGINA)
                    # en la pagina real el iframe trae un salto de linea entre "?" y "auth" (medido el 25 nov)
                    m = re.search(r"api\.couponstudio\.com\.br/cp/page/article/\d+\?\s*auth=([0-9a-f]{20,})", t or "")
                    auth = m.group(1) if (e == 200 and m) else ""
                except Exception:
                    auth = ""
                if sin_tiempo():
                    fuentes[nombre] = "sin tiempo"
                    break
            if not auth:
                fuentes[nombre] = "sin llave"
                continue
            url = url.replace("{auth}", auth)
        sitio = url.split("/")[2]
        if sitio in ultimo and reloj() - ultimo[sitio] < PAUSA:
            dormir(PAUSA - (reloj() - ultimo[sitio]))
        try:
            e, t = pedir(url)
            ultimo[sitio] = reloj()
            if e != 200 or len(t or "") < MINIMO_CUPONERA:
                raise RuntimeError("%s de %d bytes" % (e, len(t or "")))
            cods = codigos_en(t)
            for c in cods:
                salida.setdefault(tienda, {}).setdefault(c, url)
            fuentes[nombre] = len(cods)
        except Exception as ex:
            ultimo[sitio] = reloj()
            fuentes[nombre] = "falla: %s" % str(ex)[:60]
    leidas = sum(1 for v in fuentes.values() if isinstance(v, int))
    ULTIMA_COSECHA.clear()
    ULTIMA_COSECHA.update({"leidas": leidas, "segundos": round(reloj() - inicio, 1), "fuentes": fuentes,
                           "resumen": "%d de %d fuentes en %.1f s; codigos: %s" % (
                               leidas, len(FUENTES_EXTRA), reloj() - inicio,
                               ", ".join("%s %d" % (k, len(v)) for k, v in sorted(salida.items())) or "ninguno")})
    return salida


def cosechar_extra_si_toca(pedir, estado, ahora, **kw):
    """La cosecha de cuponeras cada 2 horas (hora en estado['promos']['cuponeras_leido']). Entre lecturas no pide
    nada y devuelve {}: sus codigos ya estan en cupones_cosechados. Sin reintento de pedir."""
    caja = estado.get("promos") if isinstance(estado.get("promos"), dict) else {}
    estado["promos"] = caja
    if not _vencido(caja.get("cuponeras_leido"), ahora, HORAS_CUPONERAS * 60):
        ULTIMA_COSECHA.clear()
        ULTIMA_COSECHA.update({"leidas": 0, "resumen": "no tocaba (cada %d h; ultima %s)" % (
            HORAS_CUPONERAS, caja.get("cuponeras_leido"))})
        return {}
    extra = cosechar_extra(_sin_reintento(pedir), **kw)
    if ULTIMA_COSECHA.get("leidas"):
        caja["cuponeras_leido"] = _hora(ahora)
    return extra


def guardar_codigos(estado, extra, ahora):
    """Codigos de tiendas sin carrito (todas menos Samsung) en estado['promos']['codigos'] = {tienda: {codigo:
    {'fuente', 'visto'}}}, podados a 21 dias. Solo dato: no se prueban en ningun carrito."""
    caja = estado.get("promos") if isinstance(estado.get("promos"), dict) else {}
    estado["promos"] = caja
    cods = caja.get("codigos") if isinstance(caja.get("codigos"), dict) else {}
    hoy = ahora.astimezone(MX).strftime("%Y-%m-%d")
    limite = (ahora.astimezone(MX) - dt.timedelta(days=DIAS_CODIGOS)).strftime("%Y-%m-%d")
    for tienda, d in (extra or {}).items():
        if tienda == "Samsung":
            continue
        t = cods.setdefault(tienda, {})
        for c, url in d.items():
            t[c] = {"fuente": url, "visto": hoy}
    caja["codigos"] = {t: {c: v for c, v in d.items() if isinstance(v, dict) and v.get("visto", "") >= limite}
                       for t, d in cods.items()}
    return caja["codigos"]


# ------------------------------------------------------------------------------- BBVA, del lado del banco
BBVA_BUENFIN = "https://www.bbva.mx/buen-fin"
BBVA_BLACKFRIDAY = "https://www.bbva.mx/black-friday.html"
TIENDA_DE_COMERCIO = {"Amazon": r"amazon", "Liverpool": r"liverpool", "Samsung": r"samsung", "Costco": r"costco",
                      "Gameplanet": r"game\s*planet"}


def _dinero(s):
    m = re.search(r"\$\s*([\d,]+)", s or "")
    return float(m.group(1).replace(",", "")) if m else None


def _tienda_de(comercio):
    return next((t for t, rx in TIENDA_DE_COMERCIO.items() if re.search(rx, comercio or "", re.I)), None)


def filas_bbva_buenfin(t, url=BBVA_BUENFIN):
    """Filas de bbva.mx/buen-fin: la lista de comercios (bonificacion sin cifra) y las ofertas exclusivas con
    porcentaje, tope por dia, minimo y codigo (una misma oracion de la pagina)."""
    plano = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", " ", t or "", flags=re.S))))
    vig = re.search(r"Válido del (\d+ al \d+ de \w+ de \d{4})", plano)
    vig = vig.group(1) if vig else ""
    filas = []
    for crudo in re.findall(r'\{"nombre_socio".*?\}', t or ""):
        try:
            s = json.loads(crudo)
        except Exception:
            continue
        f = _fila("BBVA", "bonificación al comprar a MSI (monto mínimo %s, %s). %s" % (s.get("monto"), s.get("meses"),
                  s.get("leyenda", "")), "bonificacion", url, minimo=_dinero(s.get("monto")),
                  meses=[int(x) for x in re.findall(r"\d{1,2}", s.get("meses") or "")], vigencia=vig)
        f.update({"comercio": s.get("nombre_socio") or "", "tienda": _tienda_de(s.get("nombre_socio"))})
        filas.append(f)
    exclusivas = re.search(r"Aplican solo el (\d+ de \w+)", plano)
    for m in re.finditer(r"(Mercado Libre|The Home Depot|Walmart\.com\.mx|Mercado Pago|Bodega Aurrera en línea|PayPal|"
                         r"sam's club|Amazon|Liverpool|Costco|Samsung) (Compra mínima[^.]{5,160}?|Cupón del \d+% de descuento"
                         r" al comprar \$[\d,]+ a [\d, y]+ MSI) \. El máximo a bonificar es de \$([\d,]+) por día y por cliente\."
                         r"(?:[^.]{0,120}?\.)?(?: Usa el código: ([A-Z0-9_]+))?", plano):
        texto, pct = m.group(2), re.search(r"(\d+)%", m.group(2))
        f = _fila("BBVA", (texto + (". Código " + m.group(4) if m.group(4) else "")).strip(),
                  "descuento" if texto.startswith("Cupón") else "bonificacion", url,
                  valor=float(pct.group(1)) if pct else None, unidad="%" if pct else None,
                  tope=_dinero("$" + m.group(3)), minimo=_dinero(texto),
                  meses=[int(n) for g in re.findall(r"a ((?:\d{1,2}(?:, | y )?)+) MSI", texto) for n in re.findall(r"\d{1,2}", g)],
                  vigencia=exclusivas.group(1) if exclusivas else vig)
        f.update({"comercio": m.group(1), "tienda": _tienda_de(m.group(1)), "codigo": m.group(4) or ""})
        filas.append(f)
    return filas


def filas_bbva_blackfriday(t, url=BBVA_BLACKFRIDAY):
    """'Modo Black': pagar hasta marzo a 3 MSI, solo un dia, minimo $3,500, con inscripcion en la App BBVA. No baja el
    precio: solo da nota en las ofertas de $3,500 o mas ese mismo dia."""
    plano = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", " ", t or "", flags=re.S))))
    m = re.search(r"Disfruta del Modo Black en tu TDC para que estrenes hoy y pagues hasta (\w+ \d{4}) a (\d+) MSI", plano)
    if not m:
        return []
    d = re.search(r"Este (\d+ de \w+) aprovecha el Black Friday", plano)
    mi = re.search(r"Mínimo de compra \$([\d,]+)", plano)
    f = _fila("BBVA", "Modo Black: estrenas hoy y pagas hasta %s a %s MSI; inscripción en la App BBVA" % (m.group(1), m.group(2)),
              "msi", url, minimo=_dinero("$" + mi.group(1)) if mi else None, meses=[int(m.group(2))],
              vigencia=d.group(1) if d else "")
    f.update({"comercio": "cualquiera (compras a una exhibición)", "tienda": None, "modo_black": True,
              "hasta": m.group(1)})
    return [f]


def lector_bancos_bbva(pedir, hay_tiempo=None, dormir=time.sleep):
    """Filas de BBVA de sus dos paginas; cada fila lleva 'comercio' y 'tienda' del vigia (None si no es una tienda
    vigilada: se guarda pero no genera nada). Falla solo si las dos paginas fallan."""
    filas, errores = [], []
    for i, (url, fn) in enumerate(((BBVA_BUENFIN, filas_bbva_buenfin), (BBVA_BLACKFRIDAY, filas_bbva_blackfriday))):
        try:
            filas += fn(_pagina(pedir, url, 20000, hay_tiempo, dormir, primera=(i == 0)))
        except _SinTiempo:
            raise
        except Exception as ex:
            errores.append(str(ex)[:80])
    if len(errores) == 2:
        raise RuntimeError("; ".join(errores))
    return filas


# ------------------------------------------------------------------------------- Liverpool: PDF de /tienda/promotions
LV_PROMOS = "https://www.liverpool.com.mx/tienda/promotions"
BANCOS_PDF_LIVERPOOL = {"BBVA": r"bbva|bancomer", "PayPal": r"paypal", "Revolut": r"revolut"}   # solo los de Saul


def _flujo(t):
    """Datos de una pagina Next (App Router): el flujo self.__next_f.push."""
    salida = []
    for p in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', t or ""):
        try:
            salida.append(json.loads('"%s"' % p))
        except Exception:
            pass
    return "".join(salida)


def texto_pdf(b):
    """Texto de un PDF con flujos FlateDecode y cadenas Tj/TJ (biblioteca estandar; los TyC de Liverpool salen legibles)."""
    import zlib
    sal = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", b or b"", re.S):
        try:
            d = zlib.decompressobj().decompress(m.group(1))
        except Exception:
            continue
        if not re.search(rb"\bTf\b", d) or not re.search(rb"\bTj\b|\bTJ\b", d):
            continue
        for bloque in re.findall(rb"\bBT\b(.*?)\bET\b", d, re.S):
            partes = []
            for s in re.findall(rb"\((?:[^()\\]|\\.)*\)|\[[^\]]*\]\s*TJ|T\*|Td|TD", bloque):
                if s.startswith(b"("):
                    partes.append(s[1:-1])
                elif s.startswith(b"["):
                    partes.extend(x[1:-1] for x in re.findall(rb"\((?:[^()\\]|\\.)*\)", s))
                elif partes:
                    partes.append(b" ")
            t = b"".join(partes)
            t = re.sub(rb"\\(\d{3})", lambda x: bytes([int(x.group(1), 8) & 255]), t)
            t = re.sub(rb"\\(.)", lambda x: x.group(1), t)
            sal.append(t.decode("cp1252", "replace"))
    t = re.sub(r"\s+", " ", " ".join(sal))
    return re.sub(r"(?<=\d) (?=\d)", "", t)   # algunos PDF parten cifras ("1 7 de noviembre de 202 6")


def fila_pdf_liverpool(banco, titulo, tx, url):
    """Una fila por PDF. valor, tope y minimo solo si salen de una misma oracion inequivoca (un solo porcentaje y su
    tope o su minimo en esa oracion); si no, valor None y el literal (medido: las cifras sueltas del PDF mezclan reglas)."""
    m = re.search(r"(?:del\s+)?\d{1,2}\s+(?:de\s+\w+\s+)?al\s+\d{1,2}\s+de\s+\w+\s+(?:de|del)\s+\d{4}", tx, re.I)
    vig = m.group(0) if m else ""
    relevantes = [o.strip() for o in re.split(r"(?<=[.;])\s+|\s•\s", tx)
                  if re.search(r"bonificaci|% de descuento|descuento del", o, re.I)]
    for o in relevantes:
        pcts = set(re.findall(r"\b(\d{1,2})\s?%", o))
        tope = re.search(r"m[aá]xim[oa][^$.]{0,60}\$\s?([\d,]+)", o, re.I)
        mini = re.search(r"m[ií]nim[oa][^$.]{0,60}\$\s?([\d,]+)", o, re.I)
        if len(pcts) == 1 and (tope or mini) and len(bancos_en(o)) <= 1 and not re.search(CATEGORIA_AJENA, o, re.I):
            f = _fila(banco, o, "bonificacion" if re.search(r"bonific", o, re.I) else "descuento", url,
                      valor=float(pcts.pop()), unidad="%", tope=_num(tope.group(1)) if tope else None,
                      minimo=_num(mini.group(1)) if mini else None, vigencia=vig)
            break
    else:
        f = _fila(banco, (relevantes[0] if relevantes else titulo), "bonificacion", url, vigencia=vig)
    f["titulo"] = titulo
    return f


def _bajar(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read()


def tarjetas_liverpool(t):
    """[(banco, titulo, url del PDF)] de la pagina de promociones, solo bancos de Saul."""
    f, dec, vistos, out = _flujo(t), json.JSONDecoder(), set(), []
    for m in re.finditer(r'\{"uid":"blt\w+","_version":\d+,"is_dir":false', f):
        try:
            o = dec.raw_decode(f, m.start())[0]
        except Exception:
            continue
        if o.get("content_type") != "application/pdf" or o.get("url") in vistos:
            continue
        vistos.add(o.get("url"))
        banco = next((b for b, rx in BANCOS_PDF_LIVERPOOL.items() if re.search(rx, o.get("title") or "", re.I)), None)
        if banco:
            out.append((banco, o.get("title") or "", o["url"]))
    return out


def lector_bancos_liverpool(pedir, hay_tiempo=None, dormir=time.sleep, bajar=None, tope=4):
    """Pagina /tienda/promotions y los PDF de BBVA, PayPal y Revolut (4 peticiones como maximo, 2 s entre ellas)."""
    t = _pagina(pedir, LV_PROMOS, 2000, hay_tiempo, dormir, primera=True)
    if len(_flujo(t)) < 1000:
        raise RuntimeError("Liverpool promociones sin datos de pagina (%d bytes)" % len(t))
    filas = []
    for banco, titulo, url in tarjetas_liverpool(t)[:tope - 1]:
        _esperar(hay_tiempo)
        dormir(2.0)
        try:
            e, b = (bajar or _bajar)(url)
            tx = texto_pdf(b) if e == 200 else ""
        except Exception:
            tx = ""
        filas.append(fila_pdf_liverpool(banco, titulo, tx, url) if len(tx) >= 80 else
                     _fila(banco, "%s (PDF sin texto legible)" % titulo, "bonificacion", url))
    return filas


LECTORES_BANCOS["Liverpool"] = lector_bancos_liverpool
LECTORES_BANCOS["BBVA"] = lector_bancos_bbva


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
# E11: dos revisiones hallaron redacciones comunes que daban avisos aparte falsos desde texto libre de bancos. En False,
# las filas de banco de texto (Amazon /blackfriday, PDF de Liverpool, paginas de BBVA) solo dan una nota con su literal;
# la ficha de Costco (dato estructurado) y la tienda de estudiantes siguen dando aviso. El modo estricto queda detras.
AVISOS_DE_BANCO_POR_TEXTO = False
PRESUPUESTO = 90.0       # segundos para todo mejorar() (E6/M3: era 150)
DIAS_AVISADO = 7          # un aviso aparte de hace mas de esto se olvida y puede volver a salir (E6/M10)
HORAS_CERRADA_AVISO = 2   # precio de tienda cerrada mas viejo que esto: solo nota "visto el ...", nunca aviso (E6/A6)
MARGEN_PETICION = 30.0    # una peticion nueva solo arranca si le quedan estos segundos (es el timeout de vigia.pedir)
AHORRO_APARTE = 3000      # con otro banco o con la tienda cerrada: aviso aparte desde esta rebaja
AHORRO_PROPIO = 500       # con un banco o medio propio: aviso aparte desde esta rebaja; por debajo, solo la nota
BAJA_APARTE = 300         # un aviso aparte ya mandado vuelve a salir solo si baja esto mas
NOTA_MAX = 200            # nota mas larga que esto tras los anadidos: se omiten primero los de meses (E11: 160 -> 200)
# Membresia Dorada de Costco, leido el 25 nov 2026 en https://www.costco.com.mx/membresias ("Individual Dorada $600")
COSTCO_MEMBRESIA = 600
# Textos de los avisos aparte (E6b, cola\2_lector_lego.md). Los avisos son issues publicos: no nombran los bancos de
# Saul ni su institucion.
CIERRE_PAGO = "No lo he probado: el descuento solo aparece en la última pantalla del pago."
CIERRE_PAGO_TARJETA = " La fuente no dice si vale con débito o solo con crédito."
CIERRE_CERRADA = "No lo he probado: no sé si tu correo institucional abre esa tienda ni si ahí valen los mismos cupones."


def _condicion(f):
    """'el descuento es de 10 % y como máximo $700, en compras de $6,000 o más' (lo que la fila trae)."""
    partes = []
    if f.get("valor") is not None:
        partes.append("el {} es de {}".format("descuento" if f.get("tipo") == "descuento" else "reembolso",
                                              "{:g} %".format(f["valor"]) if f.get("unidad") == "%" else "${:,.0f}".format(f["valor"])))
    if f.get("tope"):
        partes.append("como máximo ${:,.0f}".format(f["tope"]))
    txt = " y ".join(partes)
    if f.get("minimo"):
        txt += (", " if txt else "") + "en compras de ${:,.0f} o más".format(f["minimo"])
    return txt


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


def _aviso(ahora, estado, seco, abrir_issue, avisos, o, clave, nuevo, requisito, pide, literal, url, cierre=CIERRE_PAGO,
           hasta="", pasos="", otra_tienda=""):
    hechos = estado.setdefault("promos_avisado", {})
    prev = hechos.get(clave)
    antes = prev.get("precio") if isinstance(prev, dict) else prev   # formato viejo: solo el precio
    if antes is not None and nuevo > float(antes) - BAJA_APARTE:
        return
    precio = float(o["precio"])
    nombre = o.get("nombre") or o.get("var")
    if otra_tienda:   # E6b: el asunto se distingue a primera vista del aviso normal de precio
        asunto = "Otra tienda de Samsung: {} a ${:,.0f} en {}".format(nombre, nuevo, otra_tienda)
    else:
        asunto = "Otra forma de pago: {} a ${:,.0f} en {} con {}".format(nombre, nuevo, o.get("tienda"), requisito)
    lineas = [
        o.get("titulo") or nombre or "", "",
        "Por qué te llega: {}, {} bajaría ${:,.0f}. Es aparte del aviso de precio.".format(
            "en otra tienda de Samsung" if otra_tienda else "pagando de otra forma", nombre or "este producto",
            precio - nuevo),
        "- Precio normal en {} ahora: ${:,.0f}. Este es el que se compara con tu precio límite.".format(o.get("tienda"), precio),
        "- Con {}: ${:,.0f} (${:,.0f} menos).".format(requisito, nuevo, precio - nuevo),
        "- Qué pide: {}.".format(pide),
        "- Vale hasta: {}.".format(hasta or "la fuente no da fecha legible"),
        "- Qué harías tú: {}".format(pasos or "revisa el total en la última pantalla del pago, antes de confirmar."),
        "- Lo que dice la fuente, tal cual: \"{}\" ({})".format(literal, url)]
    if otra_tienda:
        lineas.append("- Liga de {}: {}".format(otra_tienda, url))
    lineas += ["- Liga de la oferta: {}".format(o.get("url") or ""), "", cierre]
    cuerpo = "\n".join(lineas)
    av = {"clave": clave, "asunto": asunto, "cuerpo": cuerpo, "precio": nuevo, "tienda": o.get("tienda"),
          "prod": o.get("prod"), "var": o.get("var"), "requisito": requisito}
    print("  + APARTE " + asunto)
    if seco:
        avisos.append(av)
        return
    if os.environ.get("SIN_AVISOS_PRECIO", "").lower() in ("1", "true", "yes"):   # vigia: pruebas del nucleo fallaron
        print("   (SIN_AVISOS_PRECIO=1: se habria mandado este aviso aparte; no se manda ni se marca)")
        return
    try:
        if abrir_issue(asunto, cuerpo) is False:   # sin credenciales: no salio, no se marca (como en vigia)
            return
        hechos[clave] = {"precio": nuevo, "fecha": ahora.astimezone(MX).strftime("%Y-%m-%d")}
        avisos.append(av)
    except Exception as ex:
        print("   promos: no se pudo abrir el aviso aparte:", str(ex)[:120])


def _sin_reintento(pedir):
    """E6/M2: el pedir de vigia reintenta tras un corte (30 + 2 + 30 s); dentro de promos no se reintenta."""
    try:
        import inspect
        par = inspect.signature(pedir).parameters
        extra = {}
        if "reintento" in par:
            extra["reintento"] = 0
        if "intentos" in par:   # E11: reintento=0 solo quitaba la pausa; intentos=1 quita la segunda apertura
            extra["intentos"] = 1
        if extra:
            return lambda url, *a, **k: pedir(url, *a, **dict(k, **extra))
    except (TypeError, ValueError):
        pass
    return pedir


def _olvidar_avisados(estado, ahora):
    """E6/M10: un aviso aparte de mas de 7 dias se olvida (y la lista no crece sin limite)."""
    hechos = estado.get("promos_avisado")
    if not isinstance(hechos, dict):
        return
    hoy = ahora.astimezone(MX).strftime("%Y-%m-%d")
    for k, v in list(hechos.items()):   # E11: formato viejo (solo el precio) pasa a {precio, fecha: hoy}
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            hechos[k] = {"precio": float(v), "fecha": hoy}
    limite = (ahora.astimezone(MX) - dt.timedelta(days=DIAS_AVISADO)).strftime("%Y-%m-%d")
    for k in [k for k, v in hechos.items() if isinstance(v, dict) and (v.get("fecha") or "") < limite]:
        del hechos[k]


def _mejorar(ofertas, estado, prods, ahora, seco, pedir, abrir_issue, hay_tiempo, dormir, avisos):
    pedir = _sin_reintento(pedir)
    _olvidar_avisados(estado, ahora)
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
            bueno = bool(precios or not errores)
            if not precios and previo.get("precios"):   # E11: 200 sin precio (reto) no borra los precios buenos
                bueno = False
                errores.append("0 precios (antes habia): se conservan los anteriores")
            caja["cerradas"] = {"leido": _hora(ahora), "variantes": skus, "error": "; ".join(errores)[:300],
                                "precios": precios if bueno else previo.get("precios", {}),
                                # E6/A6: hora del ultimo precio bueno, aparte de la del ultimo intento
                                "precios_leido": _hora(ahora) if bueno else previo.get("precios_leido", "")}
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
    # Modo Black de BBVA: nota corta solo el dia en que vale, en ofertas de su minimo o mas; nunca aviso
    hoy = ahora.astimezone(MX).date()
    for f in filas.get("BBVA", []):
        ini, fin = rango(f.get("vigencia"), ahora)
        if f.get("modo_black") and fin and (ini or fin) <= hoy <= fin:
            for o in ofertas:
                try:
                    if float(o["precio"]) >= float(f.get("minimo") or 0):
                        _anade(extras, o, "Modo Black BBVA hoy: pagas hasta %s a %s MSI (inscripcion en la App BBVA)"
                               % (f.get("hasta") or "marzo", (f.get("meses") or [3])[0]), meses=True)
                except Exception:
                    pass
    # Paso c: avisos aparte, para la mejor oferta de cada tienda y variante
    for (tienda, pid, vid), o in sorted(_mejores(ofertas).items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        if tienda == "Samsung" and "cupones sin probar" in (o.get("nota") or ""):
            continue   # el carrito fallo: el precio es el de pagina, no el mejor real; compararlo daria avisos falsos
        precio = float(o["precio"])
        propias = list(filas.get(tienda, [])) + [f for f in filas.get("BBVA", []) if f.get("tienda") == tienda]
        if tienda == "Costco":
            de_ficha = (prod_costco.get(_codigo_costco(o.get("url"))) or {}).get("filas") or []
            for f in de_ficha:
                if f.get("codigo") and f.get("valor") is None and vigente(f, ahora) is not False:
                    _anade(extras, o, "Costco anuncia otra promoción al pagar (%s) sin decir de cuánto" % f["codigo"])
            propias += de_ficha
        por_banco, anuncios = {}, []
        de_ficha_ids = {id(f) for f in (de_ficha if tienda == "Costco" else [])}
        for f in propias:
            if f.get("tipo") not in ("descuento", "bonificacion"):
                continue
            vig = vigente(f, ahora)   # solo lo vigente da precio alterno o aviso; lo vencido se descarta
            if not AVISOS_DE_BANCO_POR_TEXTO and id(f) not in de_ficha_ids:
                # E11: fila sacada de texto libre: solo nota con el literal, sin cifra calculada ni aviso aparte
                if vig is not False and f.get("banco"):
                    anuncios.append(f)
                continue
            if vig is None and de_quien(f.get("banco")) == "propio" and f.get("valor") is not None:
                _anade(extras, o, "%s tiene promoción aquí, pero no leí hasta cuándo vale: revísala" % f.get("banco"))
            if vig is not True:
                continue
            nuevo = precio_con_fila(precio, f)
            llave = (f.get("banco"), f.get("codigo") or "")
            if nuevo is not None and nuevo < precio - MARGEN and (llave not in por_banco or nuevo < por_banco[llave][0]):
                por_banco[llave] = (nuevo, f)
        anuncios.sort(key=lambda f: 0 if de_quien(f.get("banco")) == "propio" else 1)
        dichos = []
        for f in anuncios:
            lit = re.sub(r"\s+", " ", f.get("beneficio") or "").strip()
            lit = lit if len(lit) <= 90 else lit[:89].rstrip() + "…"
            texto = '%s anuncia promoción con %s: "%s" (sin probar; revísala en la página)' % (tienda, f["banco"], lit)
            if texto not in dichos and len(dichos) < 2:
                dichos.append(texto)
                _anade(extras, o, texto)
        for (banco, codigo), (nuevo, f) in sorted(por_banco.items()):
            quien = de_quien(banco)
            clave = "%s|%s|%s|%s" % (tienda, pid, vid, banco + ("|" + codigo if codigo else ""))
            fin = rango(f.get("vigencia"), ahora)[1]
            hasta = fin.isoformat() if fin else ""
            cond = _condicion(f)
            lit = f.get("beneficio", "")
            cierre = CIERRE_PAGO + ("" if banco == "PayPal" or re.search(r"cr[eé]dito|d[eé]bito", lit, re.I)
                                    else CIERRE_PAGO_TARJETA)
            if quien == "ajeno" and precio - nuevo >= AHORRO_APARTE:
                _aviso(ahora, estado, seco, abrir_issue, avisos, o, clave, nuevo, "tarjeta %s" % banco,
                       "pagar con una tarjeta %s%s" % (banco, ("; " + cond) if cond else ""),
                       lit, f.get("url", ""), cierre=cierre, hasta=hasta,
                       pasos="solo te sirve si alguien de confianza con tarjeta %s paga por ti, o si sacas una; te "
                             "aviso porque la diferencia pasa de ${:,.0f}.".format(AHORRO_APARTE) % banco)
            elif quien == "propio":
                if codigo and banco == "PayPal":
                    _anade(extras, o, "pagando con PayPal quedaría en ${:,.0f}, hasta {}; se aplica sola al elegir "
                                      "PayPal (no probado)".format(nuevo, hasta or "?"))
                    requisito = "PayPal"
                    pide = "elegir PayPal como forma de pago; la promoción (%s la llama %s) se aplica sola, no se teclea" % (
                        tienda, codigo)
                    pasos = "pon el teléfono en el carrito, elige PayPal y revisa que el total baje a ${:,.0f} antes de " \
                            "confirmar.".format(nuevo)
                    if tienda == "Costco":
                        pide += "; y ser socio de Costco (membresía Dorada ${:,.0f} al año)".format(COSTCO_MEMBRESIA)
                        pasos = ("entra con tu cuenta de Costco, " + pasos +
                                 " Si no eres socio, súmale ${:,.0f}.".format(COSTCO_MEMBRESIA))
                elif codigo:
                    _anade(extras, o, "pagando con {} y el código {} quedaría en ${:,.0f}, hasta {} (no probado: se ve "
                                      "al pagar)".format(banco, codigo, nuevo, hasta or "?"))
                    requisito = "tarjeta %s (código %s)" % (banco, codigo)
                    pide = "pagar con una tarjeta %s y teclear el código %s al pagar%s" % (banco, codigo,
                                                                                           ("; " + cond) if cond else "")
                    pasos = ("pon el teléfono en el carrito, elige la tarjeta %s, teclea el código %s y mira el total en "
                             "la última pantalla, antes de confirmar; si no baja a ${:,.0f}, no te conviene.".format(nuevo)
                             % (banco, codigo))
                else:
                    _anade(extras, o, "pagando con {} quedaría en ${:,.0f}, hasta {} (no probado: se ve al pagar)".format(
                        banco, nuevo, hasta or "?"))
                    requisito = "tarjeta %s" % banco
                    pide = "pagar con una tarjeta %s%s" % (banco, ("; " + cond) if cond else "")
                    pasos = ("pon el teléfono en el carrito, elige la tarjeta %s como forma de pago y mira el total en la "
                             "última pantalla, antes de confirmar; si no baja a ${:,.0f}, no te conviene.".format(nuevo)
                             % banco)
                if precio - nuevo >= AHORRO_PROPIO:
                    _aviso(ahora, estado, seco, abrir_issue, avisos, o, clave, nuevo, requisito, pide, lit,
                           f.get("url", ""), cierre=cierre, hasta=hasta, pasos=pasos)
        if tienda != "Samsung":
            continue
        cer = caja.get("cerradas") or {}
        variantes = cer.get("variantes") or {}
        for tc, nombre_tc in TIENDAS_CERRADAS.items():
            for sku, d in ((cer.get("precios") or {}).get(tc) or {}).items():
                if list(variantes.get(sku) or []) != [pid, vid]:
                    continue
                nuevo = float(d["precio"])
                viejo = _vencido(cer.get("precios_leido"), ahora, HORAS_CERRADA_AVISO * 60)
                cuenta = "pide cuenta con correo institucional" if tc == "estudiantes_mx" else "pide cuenta de empleado"
                if viejo and nuevo < precio - MARGEN:   # E6/A6: precio viejo (relectura fallida): nota, nunca aviso
                    _anade(extras, o, "en la {} ${:,.0f}, sin cupones ({}{})".format(
                        nombre_tc, nuevo, cuenta,
                        "; visto el %s" % cer["precios_leido"] if cer.get("precios_leido") else ""))
                elif viejo:
                    pass
                elif precio - nuevo >= AHORRO_APARTE:
                    corta = nombre_tc.replace(" de Samsung", "")   # "tienda de estudiantes"
                    cups = " y ".join(o.get("cupones") or [])
                    if tc == "estudiantes_mx":
                        pide = ("una cuenta en la tienda de estudiantes y docentes de Samsung, que se verifica con un "
                                "correo institucional (el tuyo podría servir; no está probado)")
                        pasos = "entra a la liga de la tienda de estudiantes, regístrate con tu correo institucional y " \
                                "mira si el precio es ${:,.0f}".format(nuevo)
                    else:
                        pide = "una cuenta en la tienda de empleados de Samsung, que pide ser empleado (no está probado)"
                        pasos = "si tienes acceso, entra a la liga de la tienda de empleados y mira si el precio es " \
                                "${:,.0f}".format(nuevo)
                    pasos += ("; luego prueba ahí los cupones %s, que en la tienda normal ya están restados." % cups
                              if cups else ".")
                    _aviso(ahora, estado, seco, abrir_issue, avisos, o, "%s|%s|%s|%s" % (tienda, pid, vid, tc), nuevo,
                           "la %s" % nombre_tc, pide,
                           "precio publicado en la ficha de esa tienda, sin cupones (no es una frase de la página: es "
                           "el precio que leí)", d.get("url", ""), cierre=CIERRE_CERRADA,
                           hasta="la página no da fecha; precio leído el %s" % (cer.get("precios_leido") or "?"),
                           pasos=pasos, otra_tienda="la " + corta)
                elif nuevo < precio - MARGEN:
                    _anade(extras, o, "en la {} ${:,.0f}, sin cupones ({})".format(nombre_tc, nuevo, cuenta))
    for o in costco:
        _anade(extras, o, "Costco pide ser socio: membresía Dorada ${:,.0f} al año si no lo eres".format(COSTCO_MEMBRESIA))
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
    pedir = _sin_reintento(pedir)
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

    def cuponera(e, t):
        c = codigos_en(t) if e == 200 else []
        return e == 200 and len(t or "") >= MINIMO_CUPONERA, "%d bytes, %d codigos de Samsung: %s" % (
            len(t or ""), len(c), ", ".join(c[:30]))

    def liverpool(e, t):
        tj = tarjetas_liverpool(t) if e == 200 else []
        return e == 200 and len(_flujo(t)) >= 1000, "%d bytes, %d tarjetas con PDF de BBVA, PayPal o Revolut: %s" % (
            len(t or ""), len(tj), "; ".join(x[1] for x in tj))

    try:   # llave del modulo de El Universal, de su pagina publica
        _, t_eu = pedir(EU_PAGINA)
        m_eu = re.search(r"api\.couponstudio\.com\.br/cp/page/article/\d+\?\s*auth=([0-9a-f]{20,})", t_eu or "")
        auth = m_eu.group(1) if m_eu else "sin-llave"
    except Exception:
        auth = "sin-llave"
    pasos = [(n, u.replace("{auth}", auth), cuponera) for n, u, tienda in FUENTES_EXTRA if tienda == "Samsung"] + [
             ("liverpool_promociones", LV_PROMOS, liverpool),
             ("samsung_deals", SAMSUNG_DEALS, samsung_pag), ("samsung_live", SAMSUNG_LIVE, samsung_pag),
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
# Recortes de medicion\fuentes\trazas\muestra_00/01/02 (cuponeras) y 05/06/07 (BBVA). En el de cupondescuento la linea
# "Añadido hace ..." se rehizo con el formato del informe (el recorte guardado no la alcanza).
CUPONDESCUENTO_SAM = (
    '<h3 class="font-medium"><strong class="font-bold uppercase"><span>20%</span> Descuento:</strong> Canjea este código '
    'promocional de Samsung para un descuento de <em>20%</em> <span class="font-bold underline">FOLD8_25CLUB</span></h3>'
    '<div>Vence en 3 semanas</div><div>Añadido hace 1 semana <span>FOLD8_25CLUB</span> <span>FOLD8_25CLUB</span></div>')
CS_SAM = (
    '<div class="cw-row"><strong class="cw-store-name">Cupón Samsung Ecosistema Galaxy: 20% en Productos Seleccionados'
    '</strong><button class="cw-btn" data-code="ECO_20" onclick="cwCopyCode(this)">Usar cupón</button></div><div class='
    '"cw-row"><strong class="cw-store-name">Código Samsung Monitor Odyssey OLED G7: 45% de Descuento</strong><button '
    'class="cw-btn" data-code="BFPLUS45">Usar cupón</button></div>')
CUPONESMEXICO_SAM = (
    '<div class="coupon-title"><a class="rc-title" href="https://www.cuponesmexico.com.mx/tienda/samsung/#coupon_2123" '
    'data-id="2123" data-code="GALAXYMP" data-title="Cupón Samsung: $600 de descuento con el cupón de Mercado pago">Cupón '
    'Samsung: $600 de descuento con el cupón de Mercado pago</a></div><span>Último uso: Esta semana</span>')
BBVA_BF_PAG = (
    '<script>const socios = [{"nombre_socio": "Amazon", "monto": "$5,000", "meses": "12 y 15 MSI", "leyenda": "Aplica en '
    'tienda en línea"}];</script><p>Válido del 13 al 17 de noviembre de 2026.</p><p>Aplican solo el 17 de noviembre.</p>'
    '<h3>Amazon</h3><div>Cupón del <b>10% de descuento</b> al comprar <b>$4,500 a 3, 6, 9, 12 y 15 MSI</b>. El máximo a '
    'bonificar es de <b>$500</b> por día y por cliente.<br />Usa el código:<b></b><a>BBVABF26</a></div>')
BBVA_BLACK_PAG = (
    '<h1>Disfruta del <em>Modo Black</em> en tu TDC para que estrenes hoy y pagues <em>hasta marzo 2027 a 3 MSI</em>'
    '</h1><div>Este 27 de noviembre aprovecha el <b>Black Friday</b> en compras a una exhibición.</div>'
    '<p>Mínimo de compra $3,500</p>')
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
    assert amazon_precio(f["precio"], f) == (34174.82, "ya restado un cupón de $220 que se marca en la página del producto; sin cupón $34,395")
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
    # E6, modo estricto (entradas de cola\1_scripts\demo_defectos.py, D1-D4)
    d1 = filas_promo_banco("<p>Black Friday del 26 al 30 de noviembre de 2026</p><p>Obtén 10% de descuento con Banorte "
                           "y 5% con HSBC en compras mínimas de $8,000.00.</p>")
    assert [(f["banco"], f["valor"]) for f in d1] == [("Banorte", None), ("HSBC", None)], d1     # dos bancos: sin cifra
    d2 = filas_promo_banco("<p>Vigente del 26 al 30 de noviembre de 2026</p><p>Hasta 70% en pantallas y 10% de "
                           "descuento con Banorte</p>")
    assert [(f["banco"], f["valor"]) for f in d2] == [("Banorte", None)], d2                     # otra categoria
    assert precio_con_fila(34174.82, {"tipo": "descuento", "valor": 70, "unidad": "%"}) is None  # mas de la mitad
    d3 = filas_promo_banco("<p>Del 26 al 30 de noviembre de 2026</p><p>En compras de $20,000 o más con HSBC recibe una "
                           "bonificación de $1,500.</p>")
    assert [(f["banco"], f["valor"], f["unidad"], f["minimo"]) for f in d3] == [("HSBC", 1500.0, "$", 20000.0)], d3
    d4 = filas_promo_banco("<h1>Black Friday del 26 al 30 de noviembre de 2026</h1><p>Solo el 27 de noviembre: 10% de "
                           "descuento con Banorte, descuento máximo de $5,000.</p><p>12% de descuento con Santander en "
                           "Moda y Hogar.</p>")
    a29 = dt.datetime(2026, 11, 29, 18, 0, tzinfo=dt.timezone.utc)
    assert [(f["banco"], f["valor"], vigente(f, a29)) for f in d4] == [("Banorte", 10.0, False), ("Santander", None, True)], \
        [(f["banco"], f["valor"], f["vigencia"]) for f in d4]                                   # fecha propia: vencida
    # E6/A5: cupon de ficha en % sin "Paga $X" no se resta
    fin, nota = amazon_precio(34394.83, amazon_ficha(FICHA_CUPON.replace("$220.01", "10%")))
    assert fin == 34394.83 and "cupon de ficha de 10 % sin restar: revisa el tope en la pagina" in nota, (fin, nota)
    b10 = amazon_busqueda(BUSQ_CUPON.replace("de descuento de $220.01", "de descuento de 10 %").replace("34,174.82", "30,955.35"))
    assert amazon_precio(34394.83, b10) == (30955.35, "ya restado un cupón de $3,439 que se marca en la página del producto; sin cupón $34,395"), \
        amazon_precio(34394.83, b10)                                                           # con "Paga" que cuadra si
    # E6/M11: pagina de Samsung sin MSI cuenta como fallo
    try:
        lector_bancos_samsung(_pedir_falso({"offer/deals": (200, "<html>reto</html>" + " " * 3000)}), dormir=lambda s: None)
        raise AssertionError("debia fallar sin MSI")
    except RuntimeError:
        pass

    # --- leer_bancos: cache de 6 horas por tienda; un fallo deja las filas anteriores
    ahora = dt.datetime(2026, 11, 27, 18, 0, tzinfo=dt.timezone.utc)        # 12:00 en Mexico
    relleno = " " * 20001
    paginas = {"/msi": (200, MSI_BANCOS + relleno), "/blackfriday": (200, BF_BANCO + relleno),
               "offer/deals": (200, DEALS_MSI + relleno)}
    llamadas, est = [], {}
    tiendas_ab = {"Amazon", "Samsung"}   # Liverpool no se pide; BBVA (del lado del banco) siempre, aqui sin respuesta
    leer_bancos(_pedir_falso(paginas, llamadas=llamadas), est, ahora, dormir=lambda s: None, tiendas=tiendas_ab)
    t = est["promos_bancos"]["tiendas"]
    assert len(llamadas) == 5 and len(t["Amazon"]["filas"]) == 6 and len(t["Samsung"]["filas"]) == 4, (llamadas, t)
    assert t["Amazon"]["leido"] == "2026-11-27 12:00" and t["Amazon"]["error"] == "" and "Liverpool" not in t
    assert "404" in t["BBVA"]["error"] and [f["vigente"] for f in t["Amazon"]["filas"][-2:]] == [True, True]
    leer_bancos(_pedir_falso(paginas, llamadas=llamadas), est, ahora + dt.timedelta(hours=5), dormir=lambda s: None,
                tiendas=tiendas_ab)
    assert len(llamadas) == 5                                                # menos de 6 horas: no relee
    leer_bancos(_pedir_falso({"/msi": (503, "")}, llamadas=llamadas), est, ahora + dt.timedelta(hours=7),
                dormir=lambda s: None, tiendas=tiendas_ab)
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

    # --- vigencia obligatoria de toda fila de banco
    BF = "del 26 al 30 de noviembre de 2026"
    d = dt.date
    assert rango("13 al 17 de noviembre de 2026", ahora) == (d(2026, 11, 13), d(2026, 11, 17))
    assert rango(BF, ahora) == (d(2026, 11, 26), d(2026, 11, 30))
    assert rango("17 de noviembre", ahora) == (d(2026, 11, 17), d(2026, 11, 17))       # sin año: el de `ahora`
    assert rango("hasta el 2 de diciembre", ahora) == (None, d(2026, 12, 2))
    assert rango("2026-11-23 a 2026-12-02", ahora) == (d(2026, 11, 23), d(2026, 12, 2))
    assert rango("17/11/26", ahora) == (d(2026, 11, 17), d(2026, 11, 17))
    assert rango("vigente", ahora) == (None, None) and rango("", ahora) == (None, None)
    assert [vigente({"vigencia": v}, ahora) for v in (BF, "13 al 17 de noviembre de 2026", "sin fecha")] == [True, False, None]

    # --- cuponeras (respuestas guardadas: recortes de medicion\fuentes\trazas\muestra_0*.txt, rellenos a 5 KB)
    for nombre in ("CUPONDESCUENTO_SAM", "CS_SAM", "CUPONESMEXICO_SAM", "BBVA_BF_PAG", "BBVA_BLACK_PAG"):
        assert len(globals()[nombre]) <= 600, (nombre, len(globals()[nombre]))
    rell = " " * MINIMO_CUPONERA
    pag_cup = {"cupondescuento.com.mx/samsung": (200, CUPONDESCUENTO_SAM + rell), "article/16?": (200, CS_SAM + rell),
               "cuponesmexico.com.mx/tienda/samsung": (200, CUPONESMEXICO_SAM + rell),
               "cupondescuento.com.mx/amazon": (200, "Añadido hace 6 días PRIMERA1126 PRIMERA1126 " + rell),
               "article/8?": (200, "corta"), "liverpool.com.mx": RuntimeError("sin red"),
               "eluniversal.com.mx/descubre": (200, '<iframe src="https://api.couponstudio.com.br/cp/page/article/16?\n'
                                                     'auth=0123456789abcdef0123456789abcdef&ctx=app"></iframe>')}
    llamadas, esperas = [], []
    ex = cosechar_extra(_pedir_falso(pag_cup, llamadas=llamadas), dormir=esperas.append, reloj=quieto)
    assert {"FOLD8_25CLUB", "ECO_20", "BFPLUS45", "GALAXYMP"} <= set(ex["Samsung"]), ex
    assert list(ex["Amazon"]) == ["PRIMERA1126"] and "Liverpool" not in ex and len(llamadas) == 9, (ex, llamadas)
    assert "auth=0123456789abcdef0123456789abcdef" in llamadas[2] and llamadas[1] == EU_PAGINA, llamadas   # llave leida
    assert ULTIMA_COSECHA["leidas"] == 4 and "falla" in str(ULTIMA_COSECHA["fuentes"]["eluniversal_cs_amazon"])
    assert esperas and all(0 < s <= PAUSA for s in esperas)                   # 1 s entre peticiones a un mismo sitio
    sin_eu = {k: v for k, v in pag_cup.items() if "eluniversal" not in k}
    cosechar_extra(_pedir_falso(sin_eu), dormir=lambda s: None, reloj=quieto)
    assert ULTIMA_COSECHA["fuentes"]["eluniversal_cs_samsung"] == "sin llave" and ULTIMA_COSECHA["leidas"] == 3
    reloj = _Reloj()
    ex = cosechar_extra(_pedir_falso(pag_cup, reloj, 10.0), dormir=lambda s: None, reloj=reloj)
    assert ULTIMA_COSECHA["fuentes"].get("eluniversal_cs_samsung") == "sin tiempo" and reloj.t == 20, ULTIMA_COSECHA
    # cada 2 horas: la segunda lectura a la hora no pide nada
    est_k, llamadas = {}, []
    cosechar_extra_si_toca(_pedir_falso(pag_cup, llamadas=llamadas), est_k, ahora, dormir=lambda s: None, reloj=quieto)
    n1 = len(llamadas)
    assert est_k["promos"]["cuponeras_leido"] == "2026-11-27 12:00" and n1 == 9, (est_k, n1)
    assert cosechar_extra_si_toca(_pedir_falso(pag_cup, llamadas=llamadas), est_k, ahora + dt.timedelta(hours=1)) == {}
    assert len(llamadas) == n1 and ULTIMA_COSECHA["leidas"] == 0
    cosechar_extra_si_toca(_pedir_falso(pag_cup, llamadas=llamadas), est_k, ahora + dt.timedelta(hours=2),
                           dormir=lambda s: None, reloj=quieto)
    assert len(llamadas) == 2 * n1
    # palabras comunes tras "cupon/codigo" no son codigos (E6/M5)
    assert codigos_en("<h3>CUPÓN EXCLUSIVO</h3><p>Código VERIFICADO</p><span>Cupones NUEVOS</span><b>Código ACTIVO</b>"
                      "<i>cupón HONOR</i> data-code=\"REDMAGIC\"") == [], codigos_en("CUPÓN EXCLUSIVO")
    est_g = {"promos": {"codigos": {"Amazon": {"VIEJO": {"fuente": "u", "visto": "2026-10-01"}}}}}
    g = guardar_codigos(est_g, {"Amazon": {"PRIMERA1126": "u1"}, "Samsung": {"BF1000": "u2"}}, ahora)
    assert g == {"Amazon": {"PRIMERA1126": {"fuente": "u1", "visto": "2026-11-27"}}}, g   # sin Samsung, poda 21 dias

    # --- BBVA del lado del banco (recortes de muestra_05/06/07)
    rel = " " * 20000
    pag_bbva = {"buen-fin": (200, BBVA_BF_PAG + rel), "black-friday": (200, BBVA_BLACK_PAG + rel)}
    fb = lector_bancos_bbva(_pedir_falso(pag_bbva), dormir=lambda s: None)
    cup = next(f for f in fb if f.get("codigo") == "BBVABF26")
    assert (cup["tienda"], cup["tipo"], cup["valor"], cup["unidad"], cup["tope"], cup["minimo"], cup["vigencia"]) == (
        "Amazon", "descuento", 10.0, "%", 500.0, 4500.0, "17 de noviembre"), cup
    assert any(f["comercio"] == "Amazon" and f["valor"] is None and f["vigencia"] == "13 al 17 de noviembre de 2026" for f in fb)
    negro = next(f for f in fb if f.get("modo_black"))
    assert (negro["minimo"], negro["meses"], negro["vigencia"], negro["tienda"]) == (3500.0, [3], "27 de noviembre", None)
    assert precio_con_fila(34174.82, cup) == 33674.82

    # --- Liverpool: pagina de promociones y PDF (sinteticos con la forma medida)
    import zlib
    activo = {"uid": "blt1", "_version": 3, "is_dir": False, "content_type": "application/pdf", "title": "BBVA - black friday",
              "url": "https://x/bbva.pdf"}
    otro = dict(activo, uid="blt2", title="Banamex - black friday", url="https://x/banamex.pdf")
    pag_lv = "self.__next_f.push([1,%s])" % json.dumps(json.dumps([activo, otro], separators=(",", ":")) + " " * 2500)
    pdf = (b"%PDF-1.4\n1 0 obj<<>>stream\n" + zlib.compress(
        b"BT /F1 9 Tf (Vigencia de la promocion: 27 al 30 de noviembre de 2026.) Tj ET "
        b"BT /F1 9 Tf (Obten 10% de bonificacion con tarjetas BBVA en compras minimas de $5,000, maximo a bonificar "
        b"$2,500 por cliente.) Tj ET BT /F1 9 Tf (Hasta 15% o 10% de bonificacion segun el dia.) Tj ET") + b"\nendstream")
    bajadas = []
    fl = lector_bancos_liverpool(_pedir_falso({"promotions": (200, pag_lv)}), dormir=lambda s: None,
                                 bajar=lambda u: (bajadas.append(u) or (200, pdf)))
    assert bajadas == ["https://x/bbva.pdf"] and len(fl) == 1, (bajadas, fl)      # Banamex no es de Saul: ni se baja
    assert (fl[0]["banco"], fl[0]["tipo"], fl[0]["valor"], fl[0]["tope"], fl[0]["minimo"], fl[0]["vigencia"]) == (
        "BBVA", "bonificacion", 10.0, 2500.0, 5000.0, "27 al 30 de noviembre de 2026"), fl[0]
    ambigua = fila_pdf_liverpool("BBVA", "t", "Vigencia: 27 al 30 de noviembre de 2026. Hasta 15% o 10% de "
                                 "bonificacion con compra minima de $5,000.", "u")
    assert ambigua["valor"] is None and ambigua["beneficio"].startswith("Hasta 15% o 10%"), ambigua   # dos cifras: sin valor

    # --- vigia: Amex ya no casa dentro de Banamex; codigos de /offer/cyber/
    v = _vigia(importar=True)
    assert v.bancos("Galaxy Z Fold8 con Banamex a 18 MSI") == ("pide Banamex (no la tienes)", False), v.bancos("Banamex")
    assert v.bancos("10% pagando con American Express") == ("pide Amex (no la tienes)", False)
    assert v.cupones_en_texto("Cupón 20%: PRO-20") == ["PRO-20"] and v.cupones_en_texto("10% con HS26_ECO") == ["HS26_ECO"]
    assert [v.cupones_en_texto(x) for x in ("cupón de descuento", "código postal 04510", "10% con BBVA")] == [[], [], []]

    # --- E11: un solo intento, regex sin retroceso, retos 200 que no borran, inicio futuro, formato viejo de avisados
    vistos_k = []
    _sin_reintento(lambda url, reintento=2.0, intentos=2: vistos_k.append((reintento, intentos)) or (200, ""))("u")
    _sin_reintento(lambda url: vistos_k.append("sin params") or (200, ""))("u")
    assert vistos_k == [(0, 1), "sin params"], vistos_k
    c0 = time.perf_counter()
    filas_msi_amazon("<p>Modelos " + ", ".join(["12"] * 200) + " y " + "1" * 60 + "x</p>")
    assert time.perf_counter() - c0 < 1.0, "MSI_AMAZON con retroceso"
    for reto in ("<script>var MSIE=1;</script>" + " " * 3000, "<p>msi</p>" + " " * 3000):
        try:
            lector_bancos_samsung(_pedir_falso({"offer/deals": (200, reto)}), dormir=lambda s: None)
            raise AssertionError("reto con MSIE paso")
        except RuntimeError:
            pass
    est_r = {}
    leer_bancos(_pedir_falso(paginas_r := {"/msi": (200, MSI_BANCOS + " " * 20001),
                                           "/blackfriday": (200, BF_BANCO + " " * 20001)}),
                est_r, dt.datetime(2026, 11, 27, 18, 0, tzinfo=dt.timezone.utc), dormir=lambda s: None, tiendas={"Amazon"})
    n_am = len(est_r["promos_bancos"]["tiendas"]["Amazon"]["filas"])
    leer_bancos(_pedir_falso({"amazon": (200, "<html>Just a moment</html>" + " " * 60000)}), est_r,
                dt.datetime(2026, 11, 28, 2, 0, tzinfo=dt.timezone.utc), dormir=lambda s: None, tiendas={"Amazon"})
    t_r = est_r["promos_bancos"]["tiendas"]["Amazon"]
    assert n_am == 6 and len(t_r["filas"]) == 6 and "sin filas" in t_r["error"], t_r   # reto 200 de 60 KB: se conservan
    a27 = dt.datetime(2026, 11, 29, 18, 0, tzinfo=dt.timezone.utc)
    assert vigente({"vigencia": "del 1 al 3 de diciembre de 2026"}, a27) is False                # aun no empieza
    assert vigente({"vigencia": "del 26 al 30 de noviembre de 2026"}, a27) is True
    est_v = {"promos_avisado": {"k": 30000}}
    _olvidar_avisados(est_v, a27)
    assert est_v["promos_avisado"]["k"] == {"precio": 30000.0, "fecha": "2026-11-29"}, est_v
    rel_c, cuelga = _Reloj(), []

    def pedir_cuelga(url, *a, **k):
        cuelga.append(url)
        rel_c.t += 30.0
        raise RuntimeError("sin respuesta")
    cosechar_extra(pedir_cuelga, dormir=lambda s: None, reloj=rel_c)
    assert rel_c.t <= TOPE_CUPONERAS + 30 and len(cuelga) == 1, (rel_c.t, cuelga)              # peor caso 50 s

    # --- mejorar con ofertas de mentira (sin imprimir sus + APARTE: ensucian el registro de la nube)
    # Camino viejo (avisos de banco por texto en True): se sigue probando entero; al final vuelve a False.
    globals()["AVISOS_DE_BANCO_POR_TEXTO"] = True
    v_mod = _vigia(importar=True)
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
                _fila("Banorte", "10% de descuento con Banorte", "descuento", "uf", valor=10, unidad="%", tope=5000, vigencia=BF),
                _fila("HSBC", "5% de descuento con HSBC", "descuento", "uf", valor=5, unidad="%", vigencia=BF),
                _fila("BBVA", "20% con BBVA", "descuento", "uf", valor=20, unidad="%", tope=1000, minimo=5000, vigencia=BF),
                _fila("BBVA", "15 meses sin intereses exclusivos con BBVA", "msi", "uf", meses=[15])]},
            "Samsung": {"leido": "2026-11-27 12:00", "error": "", "filas": []},
            "BBVA": {"leido": "2026-11-27 12:00", "error": "", "filas": []}}},
            "promos": {"cerradas": {"leido": "2026-11-27 11:30", "error": "", "precios_leido": "2026-11-27 11:30",
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
    assert len(mandados) == 3 and all(a.startswith("Otra ") for a in mandados), mandados
    assert "Otra forma de pago: Fold8 512 GB a $30,757 en Amazon con tarjeta Banorte" \
        in mandados, mandados
    banorte = next(a for a in av if a["requisito"] == "tarjeta Banorte")
    assert CIERRE_PAGO in banorte["cuerpo"] and "uf" in banorte["cuerpo"], banorte["cuerpo"]
    # E6b: texto publico sin los bancos de Saul ni su institucion, sin negritas; con vigencia, pasos y todos los datos
    for a in [banorte] + [x for x in av if x is not banorte]:
        assert "**" not in a["cuerpo"] and "los tuyos" not in a["cuerpo"] and "Colmex" not in a["cuerpo"], a["cuerpo"]
        assert all(s in a["cuerpo"] for s in ("Precio normal en", "Vale hasta:", "Qué harías tú:", "tal cual:",
                                               "Liga de la oferta:", "No lo he probado")), a["cuerpo"]
    assert "una tarjeta Banorte; el descuento es de 10 % y como máximo $5,000" in banorte["cuerpo"]
    assert "que no tienes" not in banorte["cuerpo"] and "Fold8 512 GB bajaría" in banorte["cuerpo"], banorte["cuerpo"]
    assert "Vale hasta: 2026-11-30." in banorte["cuerpo"] and "$3,417 menos" in banorte["cuerpo"], banorte["cuerpo"]
    assert "hasta 15 meses sin intereses con BBVA" in of[1]["nota"] and "hasta 15 meses" in of[2]["nota"], of
    assert "pagando con BBVA quedaría en $33,175, hasta 2026-11-30 (no probado: se ve al pagar)" in of[1]["nota"], of[1]["nota"]
    assert "HSBC" not in of[1]["nota"] and not any("HSBC" in a for a in mandados)     # $1,709 menos: no avisa
    assert "en la tienda de empleados de Samsung $33,500, sin cupones (pide cuenta de empleado)" in of[0]["nota"], of[0]["nota"]
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
    # presupuesto: cada peticion tarda 40 s en el reloj falso; con 90 s no pasan de 2 y nada queda a medias
    reloj, llamadas = _Reloj(), []
    of, base = ofertas_falsas(), ofertas_falsas()
    av = mejorar(of, {}, prods, ahora, False, _pedir_falso({"http": (200, relleno)}, reloj, 40.0, llamadas), abrir,
                 reloj=reloj, dormir=lambda s: None)
    assert len(llamadas) == 2 and reloj.t <= PRESUPUESTO and of == base, (llamadas, reloj.t)   # 90 s: 2 de 40 s
    # mejorar nunca lanza, aunque le lleguen basuras
    assert mejorar([{"precio": "x"}], {"promos_bancos": 5, "promos": 7}, [{"id": "p"}], ahora, True,
                   _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None) == []
    # abrir_issue que devuelve False (sin credenciales) y SIN_AVISOS_PRECIO=1: no se marca como mandado
    est = estado_fresco()
    av = mejorar(ofertas_falsas(), est, prods, ahora, False, _pedir_falso({}), lambda a, c: False, reloj=_Reloj(),
                 dormir=lambda s: None)
    assert av == [] and not est.get("promos_avisado"), (av, est.get("promos_avisado"))
    os.environ["SIN_AVISOS_PRECIO"] = "1"
    try:
        av = mejorar(ofertas_falsas(), est, prods, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(),
                     dormir=lambda s: None)
    finally:
        os.environ.pop("SIN_AVISOS_PRECIO", None)
    assert av == [] and not est.get("promos_avisado") and len(mandados) == 5, (av, mandados)
    # E6/A6: precio de tienda cerrada de hace mas de 2 h (relectura fallida): nota con su fecha, nunca aviso
    est = estado_fresco()
    est["promos"]["cerradas"]["precios_leido"] = "2026-11-26 09:00"
    of = ofertas_falsas()
    av = mejorar(of, est, prods, ahora, True, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert not any(a["clave"].endswith("_mx") for a in av), av
    assert "en la tienda de estudiantes de Samsung $30,999, sin cupones (pide cuenta con correo institucional; visto el 2026-11-26 09:00)" in of[0]["nota"]
    # E6/M10: un aviso aparte de hace mas de 7 dias se olvida y puede volver a salir; el nuevo guarda la fecha
    est = estado_fresco()
    est["promos_avisado"] = {"Amazon|fold8|normal-512|Banorte": {"precio": 30757.34, "fecha": "2026-11-10"},
                             "Amazon|fold8|normal-512|BBVA": {"precio": 33174.82, "fecha": "2026-11-26"}}
    av = mejorar(ofertas_falsas(), est, prods, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert sorted(a["clave"] for a in av) == ["Amazon|fold8|normal-512|Banorte", "Samsung|fold8|ultra-512|estudiantes_mx"], av
    assert est["promos_avisado"]["Amazon|fold8|normal-512|Banorte"] == {"precio": 30757.34, "fecha": "2026-11-27"}
    # E6/A7: la relectura de confirmacion de vigia resta el mismo cupon de ficha. Solo con el cupon prendido en vigia
    # (RESTAR_CUPON_FICHA_AMAZON; si vigia o la constante faltan, cuenta como prendido): linea C lo apago el 25 nov 2026
    if getattr(v_mod, "RESTAR_CUPON_FICHA_AMAZON", True):
        guardado = v_mod.pedir
        try:
            v_mod.pedir = lambda url, *a, **k: (200, '<span id="productTitle">Samsung Galaxy Z Fold8 512GB</span>' + FICHA_CUPON)
            fa = v_mod.ficha_amazon("https://www.amazon.com.mx/dp/B0H76XJTG2?th=1&psc=1")
            v_mod.pedir = lambda url, *a, **k: (200, '<span id="productTitle">Samsung Galaxy Z Fold8 512GB</span>' +
                                                FICHA_CUPON.replace("$220.01", "10%"))
            fp = v_mod.ficha_amazon("https://www.amazon.com.mx/dp/B0H76XJTG2?th=1&psc=1")
        finally:
            v_mod.pedir = guardado
        assert (fa["precio"], fp["precio"]) == (34174.82, 34394.83), (fa, fp)   # pesos se resta; % sin cuadre no
    else:
        print("cupon de ficha de Amazon apagado el 25 nov 2026: caso saltado", file=sys.__stdout__)
    # frase del aviso de tienda cerrada: no dice "se ve al pagar"
    av = mejorar(ofertas_falsas(), estado_fresco(), prods, ahora, True, _pedir_falso({}), abrir, reloj=_Reloj(),
                 dormir=lambda s: None)
    est_av = next(a for a in av if a["clave"].endswith("estudiantes_mx"))
    assert CIERRE_CERRADA in est_av["cuerpo"] and "se ve al pagar" not in est_av["cuerpo"], est_av["cuerpo"]
    assert all(CIERRE_PAGO in a["cuerpo"] for a in av if not a["clave"].endswith("_mx"))
    # carrito de Samsung caido (precio de pagina, cupones sin probar): no se compara con la tienda cerrada
    of = ofertas_falsas()
    of[0].update({"precio": 43999.0, "nota": "precio de pagina, cupones sin probar (The read operation timed out)",
                  "cupones": []})
    av = mejorar(of, estado_fresco(), prods, ahora, True, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert not any(a["tienda"] == "Samsung" for a in av) and "tienda de" not in of[0]["nota"], (av, of[0]["nota"])
    # banco propio: aviso aparte solo desde $500; por debajo, solo la nota
    est = estado_fresco()
    est["promos_bancos"]["tiendas"]["Amazon"]["filas"] = [_fila("BBVA", "1% con BBVA", "descuento", "uf", valor=1, unidad="%", vigencia=BF)]
    of = ofertas_falsas()
    av = mejorar(of, est, prods, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert not any("BBVA" in a["clave"] for a in av) and "pagando con BBVA quedaría en $33,833" in of[1]["nota"], (av, of[1]["nota"])
    est["promos_bancos"]["tiendas"]["Amazon"]["filas"] = [_fila("BBVA", "2% con BBVA", "descuento", "uf", valor=2, unidad="%", vigencia=BF)]
    av = mejorar(ofertas_falsas(), est, prods, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert [a["clave"] for a in av if "BBVA" in a["clave"]] == ["Amazon|fold8|normal-512|BBVA"], av   # $683.50 menos
    # nota larga: los meses sin intereses se omiten primero; el precio alterno se queda
    of = ofertas_falsas()
    of[1]["nota"] = "x" * 180
    mejorar(of, estado_fresco(), prods, ahora, True, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert "meses sin intereses" not in of[1]["nota"] and "pagando con BBVA quedaría en $33,175" in of[1]["nota"], of[1]["nota"]
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
    est, llamadas = {"promos_bancos": {"tiendas": {"BBVA": {"leido": "2026-11-27 12:00", "error": "", "filas": []}}}}, []
    prods_c =[{"id": "fold8", "tiendas": {"Costco": {}}}]
    av = mejorar(of_c, est, prods_c, ahora, False, _pedir_falso(pag_cc, llamadas=llamadas), abrir, reloj=_Reloj(),
                 dormir=lambda s: None)
    assert len(llamadas) == 3 and est["promos"]["costco"]["leido"] == "2026-11-27 12:00", (llamadas, est["promos"])
    assert [o["precio"] for o in of_c] == [o["precio"] for o in base_c]
    assert "pagando con PayPal quedaría en $31,999, hasta 2026-12-02; se aplica sola al elegir PayPal (no probado)" \
        in of_c[0]["nota"], of_c[0]["nota"]
    assert "Costco anuncia otra promoción al pagar (VS178) sin decir de cuánto" in of_c[0]["nota"] and "VS178" in of_c[2]["nota"]
    assert all("Costco pide ser socio: membresía Dorada $600 al año si no lo eres" in o["nota"] for o in of_c), of_c
    assert "PayPal" not in of_c[1]["nota"]                                    # no es la mejor de su variante
    assert [(a["clave"], a["requisito"]) for a in av] == [("Costco|fold8|normal-512|PayPal|PP177", "PayPal")], av
    assert "PP177" in av[0]["cuerpo"] and CIERRE_PAGO in av[0]["cuerpo"]
    mejorar(of_c, est, prods_c, ahora + dt.timedelta(minutes=30), False, _pedir_falso(pag_cc, llamadas=llamadas), abrir,
            reloj=_Reloj(), dormir=lambda s: None)
    assert len(llamadas) == 3                                                 # menos de 60 minutos: no relee
    # BBVA del Buen Fin (13-17 nov): el 25 nov esta vencida y no da nada; el 15 nov da el aviso de banco propio
    prods_a = [{"id": "fold8", "tiendas": {"Amazon": {}}}]
    # E11: el cupon dice "Aplican solo el 17 de noviembre": el 15 aun no empieza (antes se probaba el 15)
    for dia, espera in ((25, []), (15, []), (17, ["Amazon|fold8|normal-512|BBVA|BBVABF26"])):
        cuando = dt.datetime(2026, 11, dia, 18, 0, tzinfo=dt.timezone.utc)
        est = {"promos_bancos": {"tiendas": {t: {"leido": _hora(cuando), "error": "", "filas": f}
                                             for t, f in (("Amazon", []), ("BBVA", fb))}}}
        of = ofertas_falsas()
        av = mejorar(of, est, prods_a, cuando, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
        assert [a["clave"] for a in av] == espera, (dia, av)
        assert ("BBVA y el código" in of[1]["nota"]) == bool(espera) and "sin fecha" not in of[1]["nota"], (dia, of[1]["nota"])
    assert av[0]["precio"] == 33674.82 and "pagando con BBVA y el código BBVABF26 quedaría en $33,675, hasta 2026-11-17 (no " \
        "probado: se ve al pagar)" in of[1]["nota"], (av, of[1]["nota"])
    # Modo Black: nota solo el 27 de noviembre, en ofertas de $3,500 o mas; nunca aviso
    for cuando, sale in ((ahora, True), (ahora - dt.timedelta(days=1), False)):
        est = {"promos_bancos": {"tiendas": {t: {"leido": _hora(cuando), "error": "", "filas": f}
                                             for t, f in (("Amazon", []), ("BBVA", [negro]))}}}
        of = ofertas_falsas() + [{"prod": "fold8", "var": "normal", "nombre": "x", "tienda": "Amazon", "precio": 3000.0,
                                  "url": "u", "vendedor": "", "nota": "", "titulo": "t"}]
        av = mejorar(of, est, prods_a, cuando, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
        assert av == [] and ("Modo Black BBVA hoy" in of[1]["nota"]) == sale and "Modo Black" not in of[3]["nota"], of
    # fila de banco propio sin fecha legible: nota corta sin cifra, sin aviso
    est = {"promos_bancos": {"tiendas": {t: {"leido": "2026-11-27 12:00", "error": "", "filas": f} for t, f in (
        ("Amazon", [_fila("BBVA", "5% con BBVA", "descuento", "uf", valor=5, unidad="%")]), ("BBVA", []))}}}
    of = ofertas_falsas()
    av = mejorar(of, est, prods_a, ahora, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    assert av == [] and "BBVA tiene promoción aquí, pero no leí hasta cuándo vale: revísala" in of[1]["nota"], (av, of[1]["nota"])
    assert "quedaría" not in of[1]["nota"]
    # Liverpool: la fila del PDF de BBVA da aviso de banco propio
    of = [{"prod": "fold8", "var": "ultra-512", "nombre": "Fold8 Ultra 512 GB", "tienda": "Liverpool", "precio": 44999.0,
           "url": "ul", "vendedor": "Liverpool", "nota": "", "titulo": "t"}]
    est = {"promos_bancos": {"tiendas": {"Liverpool": {"leido": "2026-11-27 12:00", "error": "", "filas": fl}}}}
    av = mejorar(of, est, [{"id": "fold8", "tiendas": {"Liverpool": {}}}], ahora, False, _pedir_falso({}), abrir,
                 reloj=_Reloj(), dormir=lambda s: None)
    assert [(a["clave"], a["precio"]) for a in av] == [("Liverpool|fold8|ultra-512|BBVA", 42499.0)], av
    # E11: con AVISOS_DE_BANCO_POR_TEXTO en False (lo que corre), las redacciones de C1 y C3 no dan aviso ni cifra
    globals()["AVISOS_DE_BANCO_POR_TEXTO"] = False
    of = [dict(o) for o in of]
    of[0]["nota"] = ""
    av = mejorar(of, est, [{"id": "fold8", "tiendas": {"Liverpool": {}}}], ahora, False, _pedir_falso({}), abrir,
                 reloj=_Reloj(), dormir=lambda s: None)
    assert av == [] and 'Liverpool anuncia promoción con BBVA: "' in of[0]["nota"] and "quedaría" not in of[0]["nota"], \
        (av, of[0]["nota"])
    textos = ("<p>Black Friday del 26 al 30 de noviembre de 2026</p><p>Obtén una bonificación de 40% en accesorios con "
              "Banorte.</p><p>Recibe hasta $1,000 de bonificación con 15% de descuento con BBVA.</p><p>Válido el 27 y 28 "
              "de noviembre: 20% de descuento con HSBC.</p><p>Del 1 al 3 de diciembre de 2026: 25% de descuento con "
              "Santander.</p><p>Bonificación de 30% en audífonos pagando con HSBC.</p>")
    filas_t = filas_promo_banco(textos)
    est = {"promos_bancos": {"tiendas": {"Amazon": {"leido": "2026-11-29 12:00", "error": "", "filas": filas_t},
                                         "BBVA": {"leido": "2026-11-29 12:00", "error": "", "filas": []}}}}
    of = ofertas_falsas()
    av = mejorar(of, est, prods_a, a27, False, _pedir_falso({}), abrir, reloj=_Reloj(), dormir=lambda s: None)
    nota = of[1]["nota"]
    assert av == [] and "quedaría" not in nota and nota.count(" anuncia promoción con ") == 2, (av, nota)
    assert nota.index("con BBVA:") < nota.index("anuncia promoción con", nota.index("con BBVA:") + 1) and \
        "Santander" not in nota, nota                                   # propios primero; lo que no empieza, fuera
    assert all(len(x.split('"')[1]) <= 90 for x in nota.split("; ") if "anuncia" in x), nota
    silencio.__exit__(None, None, None)

    # --- vigia: cupones_candidatos deja fuera los codigos de tienda cerrada (EPP)
    est_c = {"cupones_cosechados": {"EPP10_NOV": "2026-11-25", "CART5_EPP": "2026-11-25", "BF1000": "2026-11-25",
                                    "EPPLIVESEM": "2026-11-24"}}
    cand = v.cupones_candidatos({"id": "x", "tiendas": {"Samsung": {"cupones": "no_existe_selftest.txt"}}}, est_c)
    assert cand == ["BF1000"], cand
    assert [bool(v.MARCA_TIENDA_CERRADA.search(c)) for c in ("CART5_EPP", "EPP10_NOV", "EPPLIVESEM", "PEPPER10")] == \
        [True, True, True, False]
    # E6/M1: lista fija primero aunque la cuponera de la ultima busqueda haya caido (origen duradero)
    cos = {"CUPX%02dA" % i: "2026-11-29" for i in range(40)}
    est_m = {"cupones_cosechados": dict(cos, BF1000="2026-11-29"), "cupones_ultima_busqueda": {"hallados": {}},
             "cupones_origen": dict({c: "cuponera" for c in cos}, BF1000="Samsung")}
    prod_m = {"id": "fold8", "tiendas": {"Samsung": {}}}
    fija = v.cupones_candidatos(prod_m, {})
    cand = v.cupones_candidatos(prod_m, est_m)
    assert cand[:len(fija)] == fija and all(c in cand for c in fija), (fija, cand)
    assert all(u in v.PAGINAS_CUPON_SAMSUNG for u in (SAMSUNG_DEALS, SAMSUNG_LIVE, SAMSUNG_CYBER))
    print("promos selftest OK: motor de cupones (1,440 ordenes, un solo resultado, orden aplicado), cupon de ficha de "
          "Amazon, bancos, tiendas cerradas, PayPal de Costco, mejorar (precios intactos, presupuesto, avisos aparte "
          "sin repetir, umbral propio $500, notas recortadas), vigencia de filas de banco, cuponeras, BBVA y Liverpool, "
          "sin EPP, Amex y codigos de /offer/cyber/")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--sondeo" in sys.argv:
        print(json.dumps(sondeo(), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
