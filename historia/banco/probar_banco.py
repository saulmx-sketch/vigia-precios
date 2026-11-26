#!/usr/bin/env python3
"""probar_banco.py: banco de prueba del pronostico de prospectiva.py con uno y dos anios parecidos.

Usa prospectiva.py tal como esta (no lo modifica): serie_diaria, nivel_en (dentro de pronostico), pronostico,
topes_retro (piso y topes calculados con datos de antes del dia), _nivel_aviso, s_de_errores y error_piso.
Lo unico copiado es el ciclo de avisos dia por dia de correr_caso() (metodo "calculado"), que en el modulo no se
puede llamar suelto porque va pegado a sus cinco casos fijos.

Uso (desde cualquier carpeta):
  py -X utf8 -B historia/banco/probar_banco.py
Escribe historia/banco/BANCO.md y historia/banco/banco_resultados.csv. Sin red.
"""
import csv
import datetime as dt
import os
import sys

sys.dont_write_bytecode = True
BANCO = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.dirname(BANCO)
REPO = os.path.dirname(HIST)
if REPO not in sys.path:
    sys.path.insert(0, REPO)
import prospectiva as P  # noqa: E402

D = dt.date
HOY = D(2026, 11, 25)
UN_DIA = P.UN_DIA
# Pisos de cordura para los productos del banco (prospectiva.py solo trae fold8, ps5, fold7 y fold6): un precio
# por debajo se descarta como lectura mala. Los puse yo, a la mitad o menos del minimo visto de cada familia.
PISOS_BANCO = {"fold5": 12000, "s23ultra": 8000, "s24ultra": 8000, "s25ultra": 8000, "xboxs": 2500, "xboxx": 5000}
PISO_CASO = {"fold": 12000, "sultra": 8000, "xbox": 2500}
FECHAS = ((11, 1), (11, 15), (11, 25))


def _a(nombre, corto, producto, factores, desfase):
    return {"nombre": nombre, "corto": corto, "producto": producto, "factores": factores, "desfase": desfase}


F512 = {"512": 1.0}
S256 = {"256": 1.0}
XU = {"unica": 1.0}
CASOS = [
    {"caso": "F", "titulo": "Fold7 512 GB, temporada 2025-26", "producto": "fold7", "factores": F512, "anio": 2025,
     "familia": "fold", "tipo": "dos años", "solo_keepa": False,
     "analogos": [_a("Fold6", "el Fold6", "fold6", P.FACTOR_FOLD6, 364), _a("Fold5", "el Fold5", "fold5", F512, 728)]},
    {"caso": "G", "titulo": "S25 Ultra 256 GB, temporada 2025-26", "producto": "s25ultra", "factores": S256, "anio": 2025,
     "familia": "sultra", "tipo": "dos años", "solo_keepa": False,
     "analogos": [_a("S24 Ultra", "el S24 Ultra", "s24ultra", S256, 364), _a("S23 Ultra", "el S23 Ultra", "s23ultra", S256, 728)]},
    {"caso": "H", "titulo": "S24 Ultra 256 GB, temporada 2024-25", "producto": "s24ultra", "factores": S256, "anio": 2024,
     "familia": "sultra", "tipo": "un año", "solo_keepa": False,
     "analogos": [_a("S23 Ultra", "el S23 Ultra", "s23ultra", S256, 364)]},
    {"caso": "I", "titulo": "Fold6 512 GB, temporada 2024-25", "producto": "fold6", "factores": F512, "anio": 2024,
     "familia": "fold", "tipo": "un año", "solo_keepa": False,
     "analogos": [_a("Fold5", "el Fold5", "fold5", F512, 364)]},
    {"caso": "J", "titulo": "Fold7 512 GB, temporada 2025-26, solo Amazon", "producto": "fold7", "factores": F512, "anio": 2025,
     "familia": "fold", "tipo": "dos años, misma fuente", "solo_keepa": True,
     "analogos": [_a("Fold6", "el Fold6", "fold6", P.FACTOR_FOLD6, 364), _a("Fold5", "el Fold5", "fold5", F512, 728)]},
    {"caso": "K", "titulo": "Xbox Series S", "producto": "xboxs", "factores": XU, "anio": None,
     "familia": "xbox", "tipo": "consola", "solo_keepa": False,
     "analogos": [_a("Series S un año antes", "la misma consola", "xboxs", XU, 364),
                  _a("Series S dos años antes", "la misma consola", "xboxs", XU, 728)]},
]


class Datos(object):
    """Observaciones de historia/*.csv, historia/banco/*.csv e historial_precios.csv, con series en cache.
    La clasificacion valida/no valida es la de Contexto.__init__ (prospectiva.py, clase Contexto)."""

    def __init__(self, solo_keepa=False):
        self.prods = P.leer_productos(os.path.join(REPO, P.CONFIG))
        obs = P.cargar_historia(HIST) + P.cargar_historia(BANCO)
        vig, _ = P.cargar_historial(os.path.join(REPO, P.HISTORIAL), self.prods)
        self.obs = vig + obs
        self.pisos = P._pisos(self.prods)
        self.pisos.update(PISOS_BANCO)
        self.validas, self.todas, self._series = {}, {}, {}
        for o in self.obs:
            if solo_keepa and o["fuente"] != "keepa":
                continue
            self.todas.setdefault(o["producto"], []).append(o)
            if o["fecha"] <= HOY.isoformat() and P.clasificar(o, self.pisos) == "valida":
                self.validas.setdefault(o["producto"], []).append(o)

    def serie(self, producto, factores):
        clave = (producto, tuple(sorted(factores.items())))
        if clave not in self._series:
            self._series[clave] = P.serie_diaria(self.validas.get(producto, []), factores, HOY, self.todas.get(producto, []))
        return self._series[clave]

    def analogos(self, specs):
        return [dict(a, serie=self.serie(a["producto"], a["factores"])) for a in specs]


def ciclo_avisos(serie, analogos, desde, hasta, s, sesgo, piso):
    """Copia del ciclo diario de correr_caso() de prospectiva.py (metodo "calculado"): cada dia se calculan los topes
    con datos de antes de ese dia (topes_retro) y hay aviso al entrar a un nivel o al bajar BAJA_MINIMA o mas."""
    estado, precio_av, avisos, niveles, sin_topes = None, None, [], {}, 0
    d = desde
    while d <= hasta:
        calc, sc, _ = P.topes_retro(serie, analogos, d, [], s, sesgo, piso)
        if calc is None:
            sin_topes += 1
        v = serie.get(d)
        lvl = P._nivel_aviso(v[0], calc, sc) if (v and calc) else None
        niveles[d] = lvl
        if v and calc:
            if lvl is None:
                estado, precio_av = None, None
            elif P.RANGO_NIVEL[lvl] > P.RANGO_NIVEL[estado] or (precio_av is not None and v[0] <= precio_av - P.BAJA_MINIMA):
                avisos.append({"fecha": d, "nivel": lvl, "precio": v[0], "tienda": v[1]["tienda"]})
                estado, precio_av = max(lvl, estado, key=lambda x: P.RANGO_NIVEL[x]), v[0]
        d += UN_DIA
    return avisos, niveles, sin_topes


def evaluar(caso, t, datos, s, sesgo):
    serie = datos.serie(caso["producto"], caso["factores"])
    analogos = datos.analogos(caso["analogos"])
    piso_prod = PISO_CASO[caso["familia"]]
    pron = P.pronostico(serie, analogos, t, [], s, sesgo)   # solo mira datos de fecha <= t
    H = pron["horizonte"]
    p = pron["piso"]
    usados = [i["nombre"] for i in pron["analogos"] if i["usado"]]
    no_usados = ["%s: %s" % (i["nombre"], i["motivo"]) for i in pron["analogos"] if not i["usado"]]
    # minimo real despues del pronostico, como error_piso(): dias t < d <= horizonte
    reales = [(serie[d][0], d, serie[d][1]) for d in serie if t < d <= H]
    real = min(reales, key=lambda x: (x[0], x[1])) if reales else None
    r = {"caso": caso["caso"], "titulo": caso["titulo"], "tipo": caso["tipo"], "fecha": t, "horizonte": H,
         "previstos": [a["nombre"] for a in caso["analogos"]], "usados": usados, "no_usados": no_usados,
         "tipo_piso": p["tipo"] if p else None, "bajo": None, "central": None, "alto": None, "real": real,
         "error": None, "posicion": None, "topes_dia": None, "sc_dia": None, "nivel": pron["nivel"],
         "dias_con_dato": len(reales)}
    if p and p["tipo"] == "analogo":   # sin anio parecido el modulo no da piso (construir() lo anula)
        r.update(bajo=p["bajo"], central=p["precio"], alto=p["alto"])
        if real:
            r["error"] = (p["precio"] - real[0]) / real[0]
            r["posicion"] = "debajo" if real[0] < p["bajo"] else "encima" if real[0] > p["alto"] else "dentro"
    calc, sc, _ = P.topes_retro(serie, analogos, t + UN_DIA, [], s, sesgo, piso_prod)   # topes con datos hasta t
    r["topes_dia"], r["sc_dia"] = calc, sc
    avisos, niveles, sin_topes = ciclo_avisos(serie, analogos, t + UN_DIA, H, s, sesgo, piso_prod)
    r["avisos"], r["sin_topes"], r["dias"] = avisos, sin_topes, (H - t).days
    ganga = next((a for a in avisos if a["nivel"] == "ganga"), None)
    compra = next((a for a in avisos if a["nivel"] in ("comprar", "ganga")), None)
    pct = lambda a: ((a["precio"] - real[0]) / real[0]) if (a and real) else None
    r.update(ganga=ganga, ganga_pct=pct(ganga), compra=compra, compra_pct=pct(compra))
    if real:
        r["nivel_dia_min"] = niveles.get(real[1])
        r["aviso_dia_min"] = any(a["fecha"] == real[1] and a["nivel"] in ("comprar", "ganga") for a in avisos)
        r["cazo"] = any(a["fecha"] <= real[1] and a["precio"] <= real[0] * 1.05 for a in avisos if a["nivel"] in ("comprar", "ganga"))
    else:
        r["nivel_dia_min"], r["aviso_dia_min"], r["cazo"] = None, None, None
    return r


def temporada_k(datos, s, sesgo):
    """Temporada de la Xbox Series S (ya terminada) con mas datos que tenga anios anteriores utilizables: primero la
    que mas anios parecidos usa en sus tres fechas, y a igualdad la de mas dias con precio del 1 nov al 31 ene.
    (La de mas dias a secas es 2020-21, la del lanzamiento, que no tiene anios anteriores.)"""
    serie = datos.serie("xboxs", XU)
    analogos = datos.analogos(CASOS[-1]["analogos"])
    cuentas = []
    for y in range(2020, HOY.year):
        if D(y + 1, 1, 31) > HOY:
            continue
        n = sum(1 for d in serie if D(y, 11, 1) <= d <= D(y + 1, 1, 31))
        usos = sum(sum(1 for i in P.pronostico(serie, analogos, D(y, m, dd), [], s, sesgo)["analogos"] if i["usado"])
                   for m, dd in FECHAS)
        cuentas.append((usos, n, y))
    mejor = max(cuentas)
    return mejor[2], sorted(cuentas, key=lambda x: x[2])


def sensibilidad(datos, s, sesgo):
    """Piso del Fold8 Ultra 512 el 25 nov 2026 con distintos anios parecidos y desfases."""
    serie = datos.serie("fold8", {"ultra-512": 1.0})
    f7, f6 = P.ANALOGOS_FOLD
    f5 = _a("Fold5", "el Fold5", "fold5", F512, 1092)
    var = [("Base (lo que usa prospectiva.py: Fold7 a 364 días y Fold6 a 728)", [f7, f6]),
           ("(a) se añade el Fold5 a 1,092 días", [f7, f6, f5]),
           ("(b) se quita el Fold6", [f7]),
           ("(c) se quita el Fold7", [f6]),
           ("(d) desfases 7 días antes (357 y 721)", [dict(f7, desfase=357), dict(f6, desfase=721)]),
           ("(d) desfases 7 días después (371 y 735)", [dict(f7, desfase=371), dict(f6, desfase=735)])]
    salida = []
    for nombre, specs in var:
        pron = P.pronostico(serie, datos.analogos(specs), HOY, [], s, sesgo)
        p = pron["piso"]
        usados = [i["nombre"] for i in pron["analogos"] if i["usado"]]
        no = ["%s (%s)" % (i["nombre"], i["motivo"]) for i in pron["analogos"] if not i["usado"]]
        salida.append({"nombre": nombre, "bajo": p["bajo"] if p else None, "central": p["precio"] if p else None,
                       "alto": p["alto"] if p else None, "tipo": p["tipo"] if p else None, "usados": usados, "no": no,
                       "pisos": {i["nombre"]: i["piso"] for i in pron["analogos"] if i["usado"]}})
    return salida


# ---------------------------------------------------------------- salida

def _m(x):
    return "-" if x is None else P._dinero(x)


def _pc(x):
    return "-" if x is None else "%+d %%" % int(round(x * 100))


def _f(d):
    return P._fecha_corta(d.isoformat(), HOY + dt.timedelta(days=9999))


POS = {"dentro": "dentro del rango", "debajo": "por debajo de lo más bajo esperado",
       "encima": "por encima de lo más alto esperado", None: "-"}


def escribir_csv(res, ruta):
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["caso", "titulo", "tipo", "fecha_pronostico", "horizonte", "anios_previstos", "anios_usados",
                    "anios_no_usados", "tipo_piso", "bajo", "central", "alto", "real_precio", "real_fecha", "real_tienda",
                    "error_central_pct", "posicion", "ganga", "comprar", "mirar", "sin_confirmar", "primer_ganga_fecha",
                    "primer_ganga_precio", "primer_ganga_pct", "primer_comprar_fecha", "primer_comprar_precio",
                    "primer_comprar_pct", "nivel_dia_minimo", "aviso_dia_minimo", "cazo", "avisos", "dias_sin_topes",
                    "dias_temporada", "dias_con_dato"])
        for r in res:
            real, tp = r["real"], r["topes_dia"]
            ent = lambda x: "" if x is None else P._entero(x)
            w.writerow([r["caso"], r["titulo"], r["tipo"], r["fecha"].isoformat(), r["horizonte"].isoformat(),
                        "+".join(r["previstos"]), "+".join(r["usados"]), " | ".join(r["no_usados"]), r["tipo_piso"] or "",
                        ent(r["bajo"]), ent(r["central"]), ent(r["alto"]),
                        ent(real[0]) if real else "", real[1].isoformat() if real else "", real[2]["tienda"] if real else "",
                        "" if r["error"] is None else round(r["error"] * 100, 1), r["posicion"] or "",
                        tp[0] if tp else "", tp[1] if tp else "", tp[2] if tp else "", r["sc_dia"] or "",
                        r["ganga"]["fecha"].isoformat() if r["ganga"] else "", ent(r["ganga"]["precio"]) if r["ganga"] else "",
                        "" if r["ganga_pct"] is None else round(r["ganga_pct"] * 100, 1),
                        r["compra"]["fecha"].isoformat() if r["compra"] else "", ent(r["compra"]["precio"]) if r["compra"] else "",
                        "" if r["compra_pct"] is None else round(r["compra_pct"] * 100, 1),
                        r["nivel_dia_min"] or "", "" if r["aviso_dia_min"] is None else ("si" if r["aviso_dia_min"] else "no"),
                        "" if r["cazo"] is None else ("si" if r["cazo"] else "no"), len(r["avisos"]), r["sin_topes"],
                        r["dias"], r["dias_con_dato"]])


def resumen(res):
    """Cifras para las frases de arriba de BANCO.md."""
    dos = [r for r in res if len(r["usados"]) >= 2 and r["error"] is not None]
    uno = [r for r in res if len(r["usados"]) == 1 and r["error"] is not None]
    todos = dos + uno
    c = {"dos": dos, "uno": uno, "todos": todos}
    for k, grupo in (("dos", dos), ("uno", uno), ("todos", todos)):
        c[k + "_dentro"] = sum(1 for r in grupo if r["posicion"] == "dentro")
        c[k + "_debajo"] = sum(1 for r in grupo if r["posicion"] == "debajo")
        c[k + "_encima"] = sum(1 for r in grupo if r["posicion"] == "encima")
        c[k + "_mae"] = (sum(abs(r["error"]) for r in grupo) / len(grupo)) if grupo else None
        c[k + "_altos"] = sum(1 for r in grupo if r["error"] > 0)
    c["por_fecha"] = {}
    for mes, dia in FECHAS:
        g = [r for r in todos if (r["fecha"].month, r["fecha"].day) == (mes, dia)]
        c["por_fecha"][(mes, dia)] = (sum(abs(r["error"]) for r in g) / len(g), len(g)) if g else (None, 0)
    return c


def escribir_md(res, sens, s, sesgo, k_info, ruta, notas_pixeles):
    c = resumen(res)
    L = []
    if c["dos"]:
        L.append("Con dos años parecidos hubo %d pronósticos que se pudieron comparar (casos %s). El precio más bajo real "
                 "cayó dentro del rango esperado en %d, por debajo de lo más bajo esperado en %d y por encima en %d. "
                 "El piso central se equivocó en promedio %d %% (sin importar el signo) y quedó por arriba del real en %d de %d."
                 % (len(c["dos"]), ", ".join(sorted(set(r["caso"] for r in c["dos"]))), c["dos_dentro"], c["dos_debajo"],
                    c["dos_encima"], int(round(c["dos_mae"] * 100)), c["dos_altos"], len(c["dos"])))
    else:
        L.append("Ningún caso llegó a tener dos años parecidos con datos suficientes: lo que se quería probar sigue sin prueba.")
    if c["uno"]:
        L.append("Con un solo año parecido hubo %d pronósticos comparables (casos %s): el real cayó dentro del rango en %d, "
                 "debajo en %d y encima en %d; error promedio del central %d %%, alto en %d de %d."
                 % (len(c["uno"]), ", ".join(sorted(set(r["caso"] for r in c["uno"]))), c["uno_dentro"], c["uno_debajo"],
                    c["uno_encima"], int(round(c["uno_mae"] * 100)), c["uno_altos"], len(c["uno"])))
    pf = c["por_fecha"]
    partes = ["%s: %s en %d pronósticos" % ("%d nov" % dia, "%d %%" % int(round(e * 100)) if e is not None else "-", n)
              for (mes, dia), (e, n) in sorted(pf.items())]
    L.append("Error promedio del central según la fecha del pronóstico (todos los casos comparables): %s." % "; ".join(partes))
    fj = {r["caso"]: r for r in res if (r["fecha"].month, r["fecha"].day) == (11, 15) and r["caso"] in ("F", "J")}
    if "F" in fj and "J" in fj and fj["F"]["real"] and fj["J"]["real"] and fj["F"]["error"] is not None and fj["J"]["error"] is not None:
        f_, j_ = fj["F"], fj["J"]
        L.append("El mismo pronóstico del Fold7 acierta o falla según qué precios cuenten como reales: con solo Amazon "
                 "(caso J) lo más barato fue %s y el central quedó %s; contando la tienda de Samsung con cupón (caso F) "
                 "lo más barato fue %s el %s y el central quedó %s%s." % (
                     _m(j_["real"][0]), _pc(j_["error"]), _m(f_["real"][0]), _f(f_["real"][1]), _pc(f_["error"]),
                     (", y ese día el vigía lo habría marcado \"sin confirmar\"" if f_["nivel_dia_min"] == "sin confirmar" else "")))
    L.append("")
    L.append("# Banco de prueba del pronóstico (%s)" % HOY.isoformat())
    L.append("")
    L.append("Qué se hizo: para cada caso se armó, con los precios de Amazon México leídos de las gráficas de Keepa "
             "(y, en los Fold, también los del foro y de la prensa ya reunidos), el pronóstico que prospectiva.py habría "
             "dado el 1, el 15 y el 25 de noviembre de esa temporada, usando solo lo que se sabía hasta ese día. El "
             "pronóstico dice qué tan bajo se espera que llegue el precio de ahí al 31 de enero: un valor central y un "
             "rango (de lo más bajo a lo más alto esperado). Se compara con lo más barato que de verdad hubo después. "
             "Un año parecido es el modelo anterior (o la misma consola un año antes) en las mismas fechas, puesto a la "
             "escala del precio de hoy; solo se usa si tiene datos en al menos 15 de los 30 días previos y en 20 días "
             "del tramo que se quiere pronosticar. Con esos mismos cálculos, rehechos cada día, se simularon los "
             "avisos de ganga y de comprar. Holgura usada (la del módulo hoy): %.2f; sesgo: %.2f." % (s, sesgo))
    L.append("")
    L.append("Temporada elegida para la Xbox Series S: la que más años parecidos pudo usar en sus tres fechas y, a "
             "igualdad, la de más días con precio del 1 nov al 31 ene. Por temporada (años parecidos usados en las tres "
             "fechas; días con precio): %s. Se usó %d-%02d."
             % ("; ".join("%d-%02d: %d; %d" % (y, (y + 1) % 100, u, n) for u, n, y in k_info[1]), k_info[0], (k_info[0] + 1) % 100))
    L.append("")
    L.append("Cómo leer la tabla: \"Topes calculados ese día\" son los del día del pronóstico; los avisos de las dos "
             "columnas siguientes salen de rehacer el cálculo cada día de la temporada con lo sabido el día anterior, así "
             "que puede haber avisos aunque el día del pronóstico no hubiera topes. \"Sin confirmar\" es el nivel que "
             "el vigía usa para un precio tan bajo que pide revisar quién vende.")
    L.append("")
    L.append("## Resultados por caso y fecha")
    L.append("")
    L.append("| Caso | Pronóstico del | Años parecidos usados | Piso esperado: bajo / central / alto | Lo más barato real después | "
             "Error del central | ¿Dónde cayó? | Topes calculados ese día (ganga / comprar / mirar) | Primer aviso de ganga | "
             "Primer aviso de comprar | ¿Avisó de comprar el día más barato? |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in res:
        real, tp = r["real"], r["topes_dia"]
        usados = ", ".join(r["usados"]) or "ninguno"
        if len(r["usados"]) < len(r["previstos"]):
            usados += " (%s quedó %s; se previeron %d)" % (
                r["caso"], "con un año" if len(r["usados"]) == 1 else "sin año parecido", len(r["previstos"]))
        piso = "%s / %s / %s" % (_m(r["bajo"]), _m(r["central"]), _m(r["alto"])) if r["central"] else "sin pronóstico"
        rl = "%s el %s (%s)" % (_m(real[0]), _f(real[1]), real[2]["tienda"]) if real else "sin precios"
        av = lambda a, pc: "%s el %s (%s sobre el real)" % (_m(a["precio"]), _f(a["fecha"]), _pc(pc)) if a else "no hubo"
        dia = ("sí" if r["aviso_dia_min"] else "no") if r["aviso_dia_min"] is not None else "-"
        if r["nivel_dia_min"] and not r["aviso_dia_min"]:
            dia += " (ese día ya estaba en %s)" % r["nivel_dia_min"]
        L.append("| %s %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["caso"], r["titulo"], _f(r["fecha"]), usados, piso, rl, _pc(r["error"]), POS[r["posicion"]],
            "%s / %s / %s" % tuple(_m(x) for x in tp) if tp else "sin topes", av(r["ganga"], r["ganga_pct"]),
            av(r["compra"], r["compra_pct"]), dia))
    L.append("")
    L.append("Años parecidos que no se pudieron usar, y por qué:")
    L.append("")
    vistos = set()
    for r in res:
        for n in r["no_usados"]:
            k = (r["caso"], r["fecha"], n)
            if k not in vistos:
                vistos.add(k)
                L.append("- %s, %s: %s" % (r["caso"], _f(r["fecha"]), n))
    if not vistos:
        L.append("- ninguno")
    L.append("")
    L.append("## Sensibilidad: el pronóstico de hoy del Fold8 Ultra 512 GB (25 nov 2026)")
    L.append("")
    base = sens[0]
    L.append("| Variante | Años parecidos usados | Bajo | Central | Alto | Cambio del central contra la base |")
    L.append("|---|---|---|---|---|---|")
    for x in sens:
        cambio = ("%+d (%s)" % (P._entero(x["central"] - base["central"]), _pc((x["central"] - base["central"]) / base["central"]))
                  if (x["central"] and base["central"]) else "-")
        us = ", ".join("%s (%s)" % (k, _m(v)) for k, v in x["pisos"].items()) or "ninguno"
        if x["no"]:
            us += "; sin usar: " + "; ".join(x["no"])
        L.append("| %s | %s | %s | %s | %s | %s |" % (x["nombre"], us, _m(x["bajo"]), _m(x["central"]), _m(x["alto"]), cambio))
    L.append("")
    L.append("Con un solo año parecido el rango ya no sale de dos años: lo más bajo esperado es el central entre "
             "(1 + sesgo) y lo más alto, el central por (1 + holgura).")
    L.append("")
    L.append("## Límites")
    L.append("")
    lim = [
        "Son %d casos (%d con dos años parecidos previstos) y %d pronósticos en total; las tres fechas de un mismo caso "
        "comparten la misma temporada, así que no son pruebas independientes, y F y J son el mismo pronóstico "
        "comparado contra dos versiones del precio real (con y sin la tienda de Samsung): con dos años parecidos hay, "
        "en el fondo, una sola temporada probada, la del Fold7 2025-26." % (
            len(CASOS), sum(1 for x in CASOS if len(x["analogos"]) >= 2), len(res)),
        "Las series de Amazon no traen los cupones de la tienda de Samsung ni sus precios con descuento: el precio más "
        "bajo que de verdad se pudo pagar puede haber sido menor que el registrado.",
        "Los precios salen de leer las gráficas por píxeles: %s, y las fechas ± 1 día." % notas_pixeles,
        "S23 Ultra Lavanda: Amazon lo tiene fijo en $31,000 desde febrero de 2025, con el modelo ya viejo (parece precio "
        "de catálogo que nadie compra). Aquí no pesa: el S23 solo se usa como año parecido de temporadas 2023-24.",
        "Xbox Series S: el mínimo de $4,540 (nov 2023) no se confirmó a ojo en la gráfica; desde 2022 Amazon aparece en "
        "días sueltos, así que esas temporadas se apoyan en pocos días.",
        "Los S23 Ultra se anuncian como \"Desbloqueado\"; se aceptaron porque los vende Amazon con formato de título de "
        "Samsung México y precio mexicano (duda 1 de C9a).",
        "Las listas de la Xbox Series S nueva ($7,300) y del S25 Ultra Jet Black ($32,990) son el arranque de esa ficha, no "
        "el lanzamiento del aparato (no se usan en este cálculo, que no depende de la lista).",
        "Los pisos de cordura de los productos nuevos (Fold5 $12,000, S Ultra $8,000, Xbox $2,500 y $5,000) los fijó "
        "esta prueba; un precio por debajo se tira como lectura mala.",
    ]
    for i, x in enumerate(lim, 1):
        L.append("%d. %s" % (i, x))
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    return "\n".join(L)


def notas_pixeles(datos):
    """Rango de los '± $N' que traen las notas de los renglones de Keepa del banco, por producto."""
    import re
    rangos = {}
    for o in datos.obs:
        if o["fuente"] == "keepa" and o["producto"] in PISOS_BANCO:
            m = re.search(r"±\s*\$\s*([\d,]+)", o.get("nota") or "")
            if m:
                v = int(m.group(1).replace(",", ""))
                a, b = rangos.get(o["producto"], (v, v))
                rangos[o["producto"]] = (min(a, v), max(b, v))
    return ", ".join("%s ± $%d a $%d" % (k, a, b) for k, (a, b) in sorted(rangos.items())) or "sin dato"


def main():
    ctx = P.Contexto(REPO, HOY)   # solo para la holgura y el sesgo que hoy usa construir()
    s, sesgo, s_motivo = P.s_de_errores([P.error_piso(ctx, c) for c in P._casos(ctx.hoy) if c["caso"] in ("A", "B")])
    datos = Datos()
    datos_k = Datos(solo_keepa=True)
    k_anio, k_cuentas = temporada_k(datos, s, sesgo)
    res = []
    for caso in CASOS:
        dd = datos_k if caso["solo_keepa"] else datos
        anio = caso["anio"] if caso["anio"] is not None else k_anio
        if caso["caso"] == "K":
            caso["titulo"] = "Xbox Series S, temporada %d-%02d" % (anio, (anio + 1) % 100)
        for mes, dia in FECHAS:
            res.append(evaluar(caso, D(anio, mes, dia), dd, s, sesgo))
    sens = sensibilidad(datos, s, sesgo)
    escribir_csv(res, os.path.join(BANCO, "banco_resultados.csv"))
    md = escribir_md(res, sens, s, sesgo, (k_anio, k_cuentas), os.path.join(BANCO, "BANCO.md"), notas_pixeles(datos))
    print("holgura s = %.3f, sesgo = %.3f (%s)" % (s, sesgo, s_motivo))
    print("observaciones: %d (solo Amazon: %d)" % (len(datos.obs), sum(len(v) for v in datos_k.todas.values())))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
