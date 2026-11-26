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

Pruebas: py promos.py --selftest
"""
import itertools
import sys
import time

MARGEN = 1.0   # pesos: una diferencia menor que esto no cuenta como rebaja


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
      cupones     codigos que dan ese total (lista ordenada; vacia si ninguno baja)
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
    return {"base": base, "total": mejor_total, "cupones": sorted(mejor), "solos": solos, "probados": probados,
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


def selftest():
    reglas = {"FOLD8_20CLUB": ("pct", 20, "club"), "BFLIVESEM": ("pct", 10, "club"), "BF1000": ("fijo", 1000, ""),
              "LIVESEM": ("sube", 4000, ""), "CENTAVO": ("fijo", 0.5, "")}
    codigos = ["LIVESEM", "MUERTO", "BFLIVESEM", "BF1000", "FOLD8_20CLUB", "CENTAVO"]
    esperado = round(43999 - 43999 * 0.20 - 1000, 2)
    vistos = set()
    for tira in (False, True):
        for orden in itertools.permutations(codigos):
            car = CarritoFalso(43999, reglas, tira=tira)
            r = probar(car, list(orden))
            vistos.add((r["total"], tuple(r["cupones"])))
            assert r["base"] == 43999 and not r["incompleto"], r
            assert sorted(car.aplicados) == r["cupones"], (car.aplicados, r)      # el carrito quedo como se reporta
    assert vistos == {(esperado, ("BF1000", "FOLD8_20CLUB"))}, vistos              # 1,440 ordenes, un solo resultado
    assert set(r["solos"]) == {"FOLD8_20CLUB", "BFLIVESEM", "BF1000"} and "LIVESEM" in r["rechazados"]
    # sin codigos, y con uno que solo encarece: queda el precio base y el carrito limpio
    assert probar(CarritoFalso(100, reglas), [])["total"] == 100
    car = CarritoFalso(43999, reglas)
    r = probar(car, ["LIVESEM", "MUERTO"])
    assert (r["total"], r["cupones"], car.aplicados) == (43999, [], []), r
    assert "ninguno de 2" in nota_cupones(r)
    # un carrito que llega con un codigo ya puesto: la base se mide sin el
    car = CarritoFalso(43999, reglas)
    car.aplicados = ["LIVESEM"]
    r = probar(car, ["BF1000"])
    assert (r["base"], r["total"], r["cupones"]) == (43999, 42999, ["BF1000"]), r
    # tope de tiempo: corta, lo dice, y nunca reporta mas que la base
    car = CarritoFalso(43999, reglas, paso=1.0)
    r = probar(car, codigos, segundos=10, reloj=car.reloj)
    assert r["incompleto"] and r["total"] <= r["base"] and len(r["probados"]) < len(codigos), r
    assert "cortada por tiempo" in nota_cupones(r)
    # adaptador con total() y puestos() separados
    r = probar(_CarritoSinLeer(CarritoFalso(43999, reglas)), codigos)
    assert (r["total"], r["cupones"]) == (esperado, ["BF1000", "FOLD8_20CLUB"]), r
    assert nota_cupones(r) == "con cupones BF1000 + FOLD8_20CLUB; sin cupon $43,999"
    print("promos selftest OK: motor de cupones (1,440 ordenes, un solo resultado; tope de tiempo; carrito releido)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
