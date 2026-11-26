#!/usr/bin/env python3
"""prospectiva.py: cifras de referencia de precios por variante, topes calculados y la razon de cada aviso.

Fase 1: cargador del historial, estadisticas por variante (minimo, lo normal, percentiles, banco ajeno).
Fase 2: pronostico por el modelo anterior ("lo que paso con el modelo anterior en las mismas fechas, aplicado
al nivel de precio de este anio"), topes calculados, prueba hacia atras y recalculo a prueba de todo.
Solo biblioteca estandar, Python 3.8 o mayor, sin red. Lo usa vigia.py: cargar_json() y razon() nunca lanzan
excepcion.

Uso:
  py -X utf8 prospectiva.py --selftest
  py -X utf8 prospectiva.py --generar
  py -X utf8 prospectiva.py --recalcular
  py -X utf8 prospectiva.py --retroprueba [--csv ruta]
  py -X utf8 prospectiva.py --veredicto fold8 ultra-512 32000 [--fecha 2026-11-25]
  py -X utf8 prospectiva.py --rastro fold8 ultra-512
"""
import csv
import datetime as dt
import glob
import json
import math
import os
import re
import sys
import tempfile
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
HISTORIA = "historia"
HISTORIAL = "historial_precios.csv"
CONFIG = "productos.json"
SALIDA = "prospectiva.json"
RETRO_MD = "RETROPRUEBA.md"
MX = dt.timezone(dt.timedelta(hours=-6))
UN_DIA = dt.timedelta(days=1)

VERSION = 1
VIGENCIA_DIAS = 30      # prospectiva.json caduca a los 30 dias de generado
VENTANA_DIAS = 30       # "ultimos 30 dias" = hoy y los 29 anteriores
MIN_DIAS_TIPICO = 5     # con menos dias con dato no se dice que es "lo normal" (ni hay nivel)
DIAS_CICLO = 120        # minimo_ciclo = minimo valido de los ultimos 120 dias
MAX_RAZON = 320
IGUAL_MARGEN = 100      # diferencia menor que esto cuenta como "iguala"
TIPICO_MARGEN = 0.02    # "lo normal" solo se menciona si difiere mas de 2 %
S_DEFECTO = 0.09        # holgura entre topes mientras no haya prueba hacia atras
S_MIN, S_MAX = 0.05, 0.12
SESGO_MAX = 0.15
SALTO_MAX = 0.15        # --recalcular no reemplaza si un tope se mueve mas que esto
BAJA_MINIMA = 300       # la regla del vigia: ya avisado, vuelve a avisar si baja al menos esto
MAX_AVISOS = 12

COLUMNAS = ["fecha", "producto", "variante", "tienda", "precio", "lista", "condicion", "banco", "tipo_banco",
            "vendedor", "garantia_mx", "fuente", "url", "id", "nota"]
COLUMNAS_HISTORIAL = ["fecha", "tienda", "producto", "precio", "vendedor", "como", "liga"]
COLUMNAS_CALENDARIO = ["evento", "tipo", "inicio", "fin", "producto", "fuente_url", "cita"]
COLUMNAS_LISTAS = ["producto", "variante", "desde", "lista", "fuente_url", "cita"]
PISOS_ANTECESOR = {"fold7": 12000, "fold6": 12000}
EVENTOS_RESPALDO = [
    {"evento": "Buen Fin 2026", "tipo": "temporada", "inicio": "2026-11-13", "fin": "2026-11-17", "producto": "",
     "fuente_url": "", "cita": "contexto comun de la linea A, 25 nov 2026", "rastro": "respaldo"},
    {"evento": "Black Friday 2026", "tipo": "temporada", "inicio": "2026-11-27", "fin": "2026-11-30", "producto": "",
     "fuente_url": "", "cita": "contexto comun de la linea A, 25 nov 2026", "rastro": "respaldo"},
]
CANJE = re.compile(r"canje|a\s+cambio|trade[\s-]*in|plan\s+de\s+telefon|plan\s+tarifario|\bcon\s+plan\b", re.I)
NO_REVERIFICADO = re.compile(r"no\s+reverificad", re.I)
# Condiciones de acceso que no se tienen por omision (linea A, fase 2d): el precio va a la serie aparte, con banco ajeno
# con bordes de palabra: "desempleados", "renueva cuenta", "superestudiante" o "businessweek" no son condicion de acceso
ACCESO = re.compile(r"(?<![a-záéíóúñ])(?:estudiantes?|epp|business|nueva\s+cuenta|primera\s+compra|emplead[oa]s?|meli\s*(?:\+|plus))"
                    r"(?![a-záéíóúñ])", re.I)


def condicion_de_acceso(o):
    """El texto de la condicion de acceso que casa en condicion, tienda o nota, o None. En los hilos del foro
    tambien se mira el titulo, que viaja en la direccion del hilo ("...desde-la-app-nueva-cuenta-mas-paypal-1111739")."""
    url = str(o.get("url") or "")
    titulo = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ") if "promodescuentos.com/" in url else ""
    m = ACCESO.search(" | ".join([str(o.get(k) or "") for k in ("condicion", "tienda", "nota")] + [titulo]))
    return m.group(0) if m else None
ULTIMO_TRAMO = re.compile(r"[uú]ltimo\s+d[ií]a\s+del\s+tramo", re.I)
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                "noviembre", "diciembre"]
METODO = ("Precios con garantia en Mexico que se pueden pagar sin canje y sin bancos ajenos (lecturas del vigia y "
          "historia/). El piso esperado repite lo que hizo el modelo anterior (o la misma consola el anio pasado) en "
          "las mismas fechas, a la escala del precio de hoy; los topes salen de ese piso y del minimo de los "
          "ultimos 120 dias.")

# Listas de lanzamiento para pasar los precios de 1 TB a su equivalente de 512 GB (historia/listas.csv
# renglones 2-3 para el Fold6 y 6-7 para el Fold7).
FACTOR_FOLD7 = {"512": 1.0, "1tb": 48999.0 / 56499.0}
FACTOR_FOLD6 = {"512": 1.0, "1tb": 45999.0 / 51999.0}
ANALOGOS_FOLD = [{"nombre": "Fold7", "corto": "el Fold7", "producto": "fold7", "factores": FACTOR_FOLD7, "desfase": 364},
                 {"nombre": "Fold6", "corto": "el Fold6", "producto": "fold6", "factores": FACTOR_FOLD6, "desfase": 728}]
ANALOGOS_PS5 = [{"nombre": "PS5 Digital un año antes", "corto": "la misma consola", "producto": "ps5",
                 "factores": {"digital": 1.0}, "desfase": 364},
                {"nombre": "PS5 Digital dos años antes", "corto": "la misma consola", "producto": "ps5",
                 "factores": {"digital": 1.0}, "desfase": 728}]
PROPIAS = {"fold8|ultra-512": ANALOGOS_FOLD, "fold8|normal-512": ANALOGOS_FOLD, "ps5|digital": ANALOGOS_PS5}
HERMANAS = {"fold8|ultra-1tb": "fold8|ultra-512", "fold8|ultra": "fold8|ultra-512",
            "fold8|normal": "fold8|normal-512", "fold8|normal-1tb": "fold8|normal-512"}
QUIEN = {"fold8": "modelo_anterior", "ps5": "misma_consola"}
LISTA_LANZAMIENTO_FOLD7_512 = 48999   # historia/listas.csv renglon 6


# ---------------------------------------------------------------- utilidades

def _aviso(msg):
    try:
        sys.stderr.write("prospectiva: %s\n" % msg)
    except Exception:
        pass


def _hoy(hoy=None):
    if hoy is None:
        return dt.datetime.now(MX).date()
    if isinstance(hoy, dt.datetime):
        return hoy.astimezone(MX).date() if hoy.tzinfo else hoy.date()
    if isinstance(hoy, dt.date):
        return hoy
    return dt.date.fromisoformat(str(hoy).strip()[:10])


_FECHAS = {}


def _d(txt):
    v = _FECHAS.get(txt)
    if v is None:
        v = _FECHAS[txt] = dt.date.fromisoformat(txt)
    return v


def _precio_txt(txt):
    t = str(txt).strip().replace("$", "").replace(",", "")
    if not t:
        return None
    v = float(t)
    if math.isnan(v) or math.isinf(v) or v <= 0:
        raise ValueError("precio no valido: %r" % txt)
    return v


def _fecha_txt(txt):
    t = str(txt).strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):   # Python 3.11+ acepta "20261121" o "2026-W48-1"; aqui no
        raise ValueError("fecha no AAAA-MM-DD: %r" % txt)
    dt.date.fromisoformat(t)
    return t


def _numero(x):
    """Numero finito y positivo, o None (acepta texto numerico; rechaza booleanos)."""
    try:
        if x is None or isinstance(x, bool):
            return None
        v = float(x) if isinstance(x, (int, float)) else _precio_txt(x)
        if v is None or math.isnan(v) or math.isinf(v) or v <= 0:
            return None
        return v
    except Exception:
        return None


def _num_json(x):
    x = float(x)
    return int(x) if x.is_integer() else round(x, 2)


def _entero(x):
    return int(round(float(x)))


def _floor100(x):
    return int(math.floor(float(x) / 100.0 + 1e-9)) * 100


def _dinero(x):
    return "${:,}".format(int(round(float(x))))


def _fecha_corta(txt, hoy):
    try:
        f = dt.date.fromisoformat(str(txt).strip()[:10])
    except Exception:
        return ""
    s = "%d %s" % (f.day, MESES[f.month - 1])
    return s if f.year == hoy.year else "%s %d" % (s, f.year)


def _leer_csv(ruta, nombre):
    """(encabezado, [(renglon, fila)]); (None, []) si no se puede abrir. Un archivo cortado entrega lo leido."""
    filas, enc = [], None
    try:
        with open(ruta, encoding="utf-8-sig", newline="") as f:
            lector = csv.reader(f)
            fin_ant = 0
            try:
                for fila in lector:
                    inicio, fin_ant = fin_ant + 1, lector.line_num
                    if enc is None:
                        enc = [c.strip() for c in fila]
                        continue
                    if not any(c.strip() for c in fila):
                        continue
                    filas.append((inicio, fila))
            except (csv.Error, UnicodeDecodeError, ValueError) as e:
                _aviso("%s: lectura cortada despues del renglon %d (%s)" % (nombre, fin_ant, e))
    except (OSError, UnicodeDecodeError) as e:
        _aviso("%s: no se pudo abrir (%s)" % (nombre, e))
        return None, []
    return enc, filas


def leer_productos(ruta):
    with open(ruta, encoding="utf-8") as f:
        prods = json.load(f)["productos"]
    for p in prods:
        for campo in ("id", "nombre", "piso", "fracciones", "variantes"):
            if campo not in p:
                raise ValueError("productos.json: al producto %s le falta '%s'" % (p.get("id", "?"), campo))
        for v in p["variantes"]:
            if "id" not in v or "nombre" not in v or "lista" not in v:
                raise ValueError("productos.json: variante incompleta en %s" % p["id"])
    return prods


def topes_fracciones(prod, var):
    """Los topes a mano de hoy: fracciones de la lista, redondeadas a centenas (igual que vigia.py)."""
    return [int(round(var["lista"] * f / 100.0)) * 100 for f in prod["fracciones"]]


def topes_vigia(prod, var):
    """Los mismos topes que vigia.topes() da sin prospectiva.json: (terna, origen)."""
    fijos = (prod.get("topes_fijos") or {}).get(var["id"])
    if fijos:
        return [int(x) for x in fijos], "topes_fijos"
    return topes_fracciones(prod, var), "fracciones"


def _sin_capacidad(var):
    return "sin dato" in str(var.get("nombre", "")).lower()


# ---------------------------------------------------------------- carga de observaciones

def cargar_historia(carpeta):
    """Observaciones de historia/*.csv con el esquema comun; ignora archivos con otro encabezado."""
    obs = []
    for ruta in sorted(glob.glob(os.path.join(carpeta, "*.csv"))):
        nombre = os.path.basename(ruta)
        archivo = "historia/" + nombre
        enc, filas = _leer_csv(ruta, archivo)
        if enc is None:
            continue
        if enc == COLUMNAS:
            con_rev = False
        elif enc == COLUMNAS + ["revision"]:
            con_rev = True
        else:
            continue
        for renglon, fila in filas:
            if len(fila) != len(enc):
                _aviso("%s renglon %d saltado (%d columnas, se esperaban %d)" % (archivo, renglon, len(fila), len(enc)))
                continue
            try:
                d = dict(zip(enc, [c.strip() for c in fila]))
                o = {k: d.get(k, "") for k in COLUMNAS}
                o["fecha"] = _fecha_txt(d["fecha"])
                o["precio"] = _precio_txt(d["precio"])
                if o["precio"] is None:
                    raise ValueError("precio vacio")
                try:
                    o["lista"] = _precio_txt(d["lista"])
                except ValueError:
                    o["lista"] = None
                o["producto"] = o["producto"].lower()
                o["variante"] = o["variante"].lower()
                o["revision"] = d.get("revision", "") if con_rev else None
            except Exception as e:
                _aviso("%s renglon %d saltado (%s)" % (archivo, renglon, e))
                continue
            o["archivo"], o["renglon"] = archivo, renglon
            o["rastro"] = "%s:%s" % (archivo, o["id"] or "renglon-%d" % renglon)
            obs.append(o)
    return obs


def cargar_historial(ruta, prods):
    """Lecturas del vigia (historial_precios.csv) traducidas al esquema comun. Devuelve (obs, sin_traducir)."""
    nombres = {}
    for p in prods:
        for v in p["variantes"]:
            nombres[v["nombre"]] = (p["id"], v["id"])
    obs, sin_traducir = [], {}
    if not os.path.exists(ruta):
        return obs, sin_traducir
    archivo = os.path.basename(ruta)
    enc, filas = _leer_csv(ruta, archivo)
    if enc is None:
        return obs, sin_traducir
    if enc[:len(COLUMNAS_HISTORIAL)] != COLUMNAS_HISTORIAL:
        _aviso("%s: encabezado inesperado, no se usa" % archivo)
        return obs, sin_traducir
    for renglon, fila in filas:
        if len(fila) < len(COLUMNAS_HISTORIAL):
            _aviso("%s renglon %d saltado (columnas de menos)" % (archivo, renglon))
            continue
        d = dict(zip(COLUMNAS_HISTORIAL, [c.strip() for c in fila]))
        par = nombres.get(d["producto"])
        if not par:
            sin_traducir[d["producto"]] = sin_traducir.get(d["producto"], 0) + 1
            continue
        try:
            fecha = _fecha_txt(d["fecha"])
            precio = _precio_txt(d["precio"])
            if precio is None:
                raise ValueError("precio vacio")
        except Exception as e:
            _aviso("%s renglon %d saltado (%s)" % (archivo, renglon, e))
            continue
        ident = "%s|%s|%s" % (d["fecha"], d["tienda"], par[1])
        obs.append({"fecha": fecha, "producto": par[0], "variante": par[1], "tienda": d["tienda"], "precio": precio,
                    "lista": None, "condicion": d["como"], "banco": "", "tipo_banco": "ninguno",
                    "vendedor": d["vendedor"] or "sin dato", "garantia_mx": "si", "fuente": "propio",
                    "url": d["liga"], "id": ident, "nota": "", "revision": None,
                    "archivo": archivo, "renglon": renglon, "rastro": "%s:%s" % (archivo, ident)})
    return obs, sin_traducir


def cargar_calendario(carpeta):
    """Ventanas de eventos de historia/calendario.csv, o las dos de respaldo. Devuelve (eventos, origen)."""
    ruta = os.path.join(carpeta, "calendario.csv")
    eventos = []
    if os.path.exists(ruta):
        enc, filas = _leer_csv(ruta, "historia/calendario.csv")
        if enc == COLUMNAS_CALENDARIO:
            for renglon, fila in filas:
                if len(fila) != len(enc):
                    continue
                d = dict(zip(enc, [c.strip() for c in fila]))
                try:
                    d["inicio"] = _fecha_txt(d["inicio"])
                    d["fin"] = _fecha_txt(d["fin"]) if d["fin"] else ""   # sin fin = fecha suelta (un anuncio)
                except Exception:
                    _aviso("historia/calendario.csv renglon %d saltado (fecha)" % renglon)
                    continue
                d["rastro"] = "historia/calendario.csv renglon %d" % renglon
                eventos.append(d)
    if eventos:
        return eventos, "historia/calendario.csv"
    return [dict(e) for e in EVENTOS_RESPALDO], "respaldo"


def cargar_listas(carpeta, prods):
    """Precios de lista de historia/listas.csv, o los de productos.json. Devuelve (listas, origen)."""
    ruta = os.path.join(carpeta, "listas.csv")
    listas = []
    if os.path.exists(ruta):
        enc, filas = _leer_csv(ruta, "historia/listas.csv")
        if enc == COLUMNAS_LISTAS:
            for renglon, fila in filas:
                if len(fila) != len(enc):
                    continue
                d = dict(zip(enc, [c.strip() for c in fila]))
                try:
                    d["lista"] = _precio_txt(d["lista"])
                except Exception:
                    continue
                d["rastro"] = "historia/listas.csv renglon %d" % renglon
                listas.append(d)
    if listas:
        return listas, "historia/listas.csv"
    return [{"producto": p["id"], "variante": v["id"], "desde": "", "lista": v["lista"], "fuente_url": "",
             "cita": "", "rastro": "productos.json"} for p in prods for v in p["variantes"]], "productos.json"


def lista_vigente(listas, producto, variante, t, defecto=None):
    """La lista con 'desde' mas reciente que no pase de t (los renglones sin fecha no cuentan)."""
    mejor = None
    for l in listas:
        if l.get("producto") != producto or l.get("variante") != variante or not l.get("desde"):
            continue
        if l["desde"] <= t.isoformat() and (mejor is None or l["desde"] >= mejor["desde"]):
            mejor = l
    return (mejor["lista"], mejor["rastro"]) if mejor else (defecto, "productos.json")


# ---------------------------------------------------------------- validez y estadisticas de la fase 1

def clasificar(o, pisos):
    """'valida', 'ajeno' (falla solo por el banco) o el motivo del descarte."""
    fuente = str(o.get("fuente", "")).lower()
    gar = str(o.get("garantia_mx", "")).lower().replace("í", "i")
    rev = o.get("revision")
    revl = (rev or "").strip().lower()
    piso = pisos.get(o.get("producto"))
    if piso is None:
        return "producto_desconocido"
    if NO_REVERIFICADO.search(o.get("nota") or ""):                                                            # linea A
        return "no_reverificado"
    if not (gar == "si" or (gar == "sin dato" and fuente in ("foro", "prensa") and revl == "ok")):           # (a)
        return "sin_garantia" if gar == "no" else "garantia_sin_confirmar"
    if CANJE.search(o.get("condicion") or ""):                                                                 # (c)
        return "canje_o_plan"
    if rev is not None and not (revl == "ok" or (revl == "" and fuente in ("propio", "keepa"))):              # (d)
        return "revision_no_ok" if revl else "sin_revision"
    if o["precio"] < piso:                                                                                     # (e)
        return "bajo_piso"
    if condicion_de_acceso(o):                                                                                 # fase 2d
        return "condicion_de_acceso"
    tb = str(o.get("tipo_banco", "")).strip().lower()                                                          # (b)
    if tb in ("ninguno", "propio"):
        return "valida"
    if tb == "ajeno":
        return "ajeno"
    return "banco_sin_dato"


def _pisos(prods):
    pisos = dict(PISOS_ANTECESOR)
    pisos.update({p["id"]: p["piso"] for p in prods})
    return pisos


def _pct(valores, q):
    """Percentil con interpolacion lineal entre vecinos (el metodo por defecto de numpy)."""
    s = sorted(valores)
    if not s:
        return None
    k = (len(s) - 1) * q / 100.0
    i = int(math.floor(k))
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (k - i)


def _mas_barata(lista):
    return min(lista, key=lambda o: (o["precio"], o["fecha"], o["rastro"])) if lista else None


def _ficha_min(o):
    if not o:
        return None
    return {"precio": _num_json(o["precio"]), "fecha": o["fecha"], "tienda": o["tienda"],
            "condicion": o.get("condicion", ""), "fuente": o.get("fuente", ""), "garantia": o.get("garantia_mx", ""),
            "rastro": o["rastro"]}


def _ficha(prod, var, g, desde30, hasta):
    tope, origen = topes_vigia(prod, var)
    validas = {}
    for o in g["validas"]:
        validas[o["fuente"] or "sin dato"] = validas.get(o["fuente"] or "sin dato", 0) + 1
    descartadas = {}
    for motivo, _ in g["descartes"]:
        descartadas[motivo] = descartadas.get(motivo, 0) + 1
    ficha = {"nombre": var["nombre"], "lista": var["lista"], "topes": tope, "topes_origen": origen,
             "topes_manual": topes_fracciones(prod, var), "sin_confirmar_bajo": None, "minimo": None,
             "minimo_30d": None, "tipico": None, "percentiles": None, "ajeno": None, "pisos_esperados": [],
             "baja_mas": [], "n_datos": {"validas": validas, "descartadas": descartadas,
                                         "ajeno": len([o for o in g["ajenas"] if not condicion_de_acceso(o)]),
                                         "condicion_de_acceso": len([o for o in g["ajenas"] if condicion_de_acceso(o)])}}
    det = {"validas": g["validas"], "ajenas": g["ajenas"], "descartes": g["descartes"], "dias": [],
           "desde30": desde30, "hasta": hasta, "sin_capacidad": _sin_capacidad(var)}
    if det["sin_capacidad"] or not g["validas"]:
        return ficha, det
    por_dia = {}
    for o in g["validas"]:
        actual = por_dia.get(o["fecha"])
        if actual is None or (o["precio"], o["rastro"]) < (actual["precio"], actual["rastro"]):
            por_dia[o["fecha"]] = o
    dias = sorted(por_dia.items())
    det["dias"] = dias
    ficha["minimo"] = _ficha_min(_mas_barata(g["validas"]))
    ficha["minimo_30d"] = _ficha_min(_mas_barata([o for o in g["validas"] if o["fecha"] >= desde30]))
    recientes = [o["precio"] for f, o in dias if f >= desde30]
    if len(recientes) >= MIN_DIAS_TIPICO:
        ficha["tipico"] = {"precio": _num_json(round(_pct(recientes, 50), 2)), "n": len(recientes)}
    todos = [o["precio"] for _, o in dias]
    ficha["percentiles"] = {"p10": _num_json(round(_pct(todos, 10), 2)), "p25": _num_json(round(_pct(todos, 25), 2)),
                            "p50": _num_json(round(_pct(todos, 50), 2)), "n": len(todos)}
    aj = _mas_barata(g["ajenas"])
    if aj:
        acceso = condicion_de_acceso(aj)
        ficha["ajeno"] = {"precio": _num_json(aj["precio"]), "fecha": aj["fecha"], "tienda": aj["tienda"],
                          "banco": aj.get("banco", ""), "rastro": aj["rastro"],
                          "motivo": "condicion de acceso" if acceso else "banco ajeno",
                          "condicion": acceso or aj.get("banco", "")}
    return ficha, det


def calcular(obs, prods, hoy=None):
    """Fichas de la fase 1 por variante de productos.json. Devuelve (variantes, detalle)."""
    hoy = _hoy(hoy)
    hasta = hoy.isoformat()
    desde30 = (hoy - dt.timedelta(days=VENTANA_DIAS - 1)).isoformat()
    pisos = _pisos(prods)
    grupos = {}
    for o in obs:
        g = grupos.setdefault("%s|%s" % (o["producto"], o["variante"]), {"validas": [], "ajenas": [], "descartes": []})
        motivo = "fecha_posterior" if o["fecha"] > hasta else clasificar(o, pisos)
        if motivo == "valida":
            g["validas"].append(o)
        elif motivo in ("ajeno", "condicion_de_acceso"):   # la serie aparte: no fija referencias ni topes
            g["ajenas"].append(o)
        else:
            g["descartes"].append((motivo, o))
    variantes, detalle = {}, {}
    for p in prods:
        for v in p["variantes"]:
            clave = "%s|%s" % (p["id"], v["id"])
            g = grupos.get(clave) or {"validas": [], "ajenas": [], "descartes": []}
            variantes[clave], detalle[clave] = _ficha(p, v, g, desde30, hasta)
    return variantes, detalle


def cargar_todo(base=AQUI):
    prods = leer_productos(os.path.join(base, CONFIG))
    obs = cargar_historia(os.path.join(base, HISTORIA))
    obs_vigia, sin_traducir = cargar_historial(os.path.join(base, HISTORIAL), prods)
    return prods, obs_vigia + obs, sin_traducir


# ---------------------------------------------------------------- fase 2: serie diaria valida

def _clave_asin(o):
    """ASIN y sufijo (":tercero") del id de un renglon de Keepa: 'B0FBTK7ZVB:2025-08-01:tercero' -> 'B0FBTK7ZVB:tercero'."""
    partes = str(o.get("id") or "").split(":")
    clave = partes[0] + ("".join(":" + x for x in partes[2:]) if len(partes) > 2 else "")
    return "%s|%s" % (o.get("archivo", ""), clave)


def serie_diaria(validas, factores, hasta, todas):
    """{fecha: (precio, observacion)} con el precio valido mas barato de cada dia, ya rellenado hacia adelante.

    validas: observaciones validas; todas: todas las del producto (validas o no), para saber cual es el renglon
    siguiente de cada ASIN y cuando una tienda vuelve a leerse. factores: variante -> factor (1 TB a 512 GB).
    Keepa vale desde su fecha hasta el dia antes del renglon siguiente del mismo ASIN, sin pasar de un renglon
    que diga "ultimo dia del tramo"; el ultimo renglon de un ASIN vale hasta la fecha mas reciente de su archivo
    (el dia en que se leyo la grafica). 'propio' vale su dia y hasta 2 dias mas si esa tienda no se vuelve a
    leer; prensa, antecedente y foro valen su dia y los 2 siguientes.
    Como ninguna regla mira datos posteriores salvo para detenerse, esta serie cortada en t es identica a la
    que se armaria solo con datos de fecha <= t (por eso la prueba hacia atras no ve el futuro)."""
    todas = [o for o in todas if o["variante"] in factores]
    siguiente, max_archivo, lecturas, grupos = {}, {}, {}, {}
    for o in todas:
        if o["fecha"] > max_archivo.get(o["archivo"], ""):
            max_archivo[o["archivo"]] = o["fecha"]
        if o["fuente"] == "keepa":
            grupos.setdefault(_clave_asin(o), []).append(o)
        elif o["fuente"] == "propio":
            lecturas.setdefault((o["tienda"], o["variante"]), set()).add(o["fecha"])
    for g in grupos.values():
        g.sort(key=lambda o: (o["fecha"], o["renglon"]))
        for a, b in zip(g, g[1:]):
            siguiente[id(a)] = b["fecha"]
    dia = {}
    for o in validas:
        f = factores.get(o["variante"])
        if f is None:
            continue
        precio = o["precio"] * f
        d0 = _d(o["fecha"])
        if o["fuente"] == "keepa":
            if ULTIMO_TRAMO.search(o.get("nota") or ""):
                fin = d0
            elif id(o) in siguiente:
                fin = _d(siguiente[id(o)]) - UN_DIA
            else:
                fin = _d(max_archivo.get(o["archivo"], o["fecha"]))
            fin = max(fin, d0)
        elif o["fuente"] == "propio":
            fin = d0
            vistas = lecturas.get((o["tienda"], o["variante"]), set())
            for k in (1, 2):
                dk = d0 + dt.timedelta(days=k)
                if dk.isoformat() in vistas:
                    break
                fin = dk
        else:
            fin = d0 + dt.timedelta(days=2)
        d = d0
        tope = min(fin, hasta)
        while d <= tope:
            act = dia.get(d)
            if act is None or (precio, o["rastro"]) < (act[0], act[1]["rastro"]):
                dia[d] = (precio, o)
            d += UN_DIA
    return dia


def nivel_en(serie, t):
    """(mediana de los 30 dias que terminan en t, dias con dato); mediana None con menos de 5 dias."""
    vals = [serie[t - dt.timedelta(days=k)][0] for k in range(VENTANA_DIAS) if (t - dt.timedelta(days=k)) in serie]
    return (_pct(vals, 50) if len(vals) >= MIN_DIAS_TIPICO else None), len(vals)


def horizonte(t):
    if t.month >= 10:
        return dt.date(t.year + 1, 1, 31)
    if t.month == 1:
        return dt.date(t.year, 1, 31)
    return t + dt.timedelta(days=90)


def _minimo_ciclo(serie, t):
    mejor = None
    for k in range(DIAS_CICLO):
        d = t - dt.timedelta(days=k)
        v = serie.get(d)
        if v and (mejor is None or (v[0], d) <= (mejor[0], mejor[1])):
            mejor = (v[0], d, v[1])
    return mejor


def _frase_base(a, dia_a, valor, nivel_a, t):
    precio_a, o = valor[1], valor[2]
    baja = int(round((1 - precio_a / nivel_a) * 100))
    capac = " (el de 1 TB)" if o.get("variante") == "1tb" else ""
    rel = "%d %% debajo" % baja if baja >= 0 else "%d %% arriba" % -baja
    return "%s%s llegó a %s en %s el %s, %s de su nivel de %s" % (
        a["corto"], capac, _dinero(o["precio"]), o.get("tienda") or "una tienda", _fecha_corta(dia_a, t + dt.timedelta(days=9999)),
        rel, MESES_LARGOS[(t - dt.timedelta(days=a["desfase"])).month - 1])


def pronostico(serie, analogos, t, eventos, s=S_DEFECTO, sesgo=0.0):
    """Piso esperado de aqui al horizonte, por evento y por analogo, en la fecha t (solo mira datos <= t).

    analogos: [{"nombre", "corto", "desfase", "serie"}]. El piso trae bajo, central y alto: con dos o mas anios
    parecidos, el menor, la mediana y el mayor de sus pisos; con uno (o ninguno, y entonces central = minimo de
    120 dias), bajo = central / (1 + sesgo) y alto = central x (1 + s). Devuelve un diccionario con objetos
    internos (fechas, observaciones y caminos) que _ficha_pron() vuelve JSON."""
    H = horizonte(t)
    niv, n_niv = nivel_en(serie, t)
    mc = _minimo_ciclo(serie, t)
    futuros = [t + dt.timedelta(days=k) for k in range(1, (H - t).days + 1)]
    usados, infos = [], []
    for a in analogos:
        desf = dt.timedelta(days=a["desfase"])
        ta, sa = t - desf, a["serie"]
        antes = sum(1 for k in range(VENTANA_DIAS) if (ta - dt.timedelta(days=k)) in sa)
        futuro = sum(1 for d in futuros if (d - desf) in sa)
        info = {"nombre": a["nombre"], "corto": a["corto"], "desfase": a["desfase"], "dias_antes": antes,
                "dias_futuro": futuro, "usado": False}
        if niv is None:
            info["motivo"] = "sin nivel propio: %d dias con dato en los ultimos 30 (hacen falta %d)" % (n_niv, MIN_DIAS_TIPICO)
        elif antes < 15:
            info["motivo"] = "solo %d de los 30 dias que terminan el %s tienen dato (hacen falta 15)" % (antes, ta.isoformat())
        elif futuro < 20:
            info["motivo"] = "solo %d dias con dato del %s al %s (hacen falta 20)" % (
                futuro, (t + UN_DIA - desf).isoformat(), (H - desf).isoformat())
        else:
            na, _ = nivel_en(sa, ta)
            camino = {}
            for d in futuros:
                v = sa.get(d - desf)
                if v:
                    camino[d] = (niv * v[0] / na, v[0], v[1])
            dmin = min(camino, key=lambda d: (camino[d][0], d))
            minimos = {}
            for etiqueta, L in (("7", 7), ("30", 30), ("horizonte", None)):
                lim = H if L is None else min(t + dt.timedelta(days=L), H)
                vals_m = [camino[d][0] for d in camino if d <= lim]
                minimos[etiqueta] = min(vals_m) if vals_m else None
            info.update(usado=True, nivel=na, piso=camino[dmin][0], fecha=dmin, fecha_analogo=dmin - desf,
                        obs=camino[dmin][2], base=_frase_base(a, dmin - desf, camino[dmin], na, t), minimos=minimos)
            usados.append((a, info, camino))
        infos.append(info)
    piso = None
    if usados:
        vals = sorted(i["piso"] for _, i, _ in usados)
        el_bajo = min((i for _, i, _ in usados), key=lambda i: i["piso"])
        central = _pct(vals, 50)
        if len(vals) >= 2:
            bajo, alto = vals[0], vals[-1]
        else:
            bajo, alto = central / (1 + sesgo), central * (1 + s)
        piso = {"precio": central, "bajo": bajo, "alto": alto, "rango": [bajo, alto], "n_analogos": len(vals),
                "fecha": el_bajo["fecha"], "tipo": "analogo", "horizonte": H, "obs": [i["obs"] for _, i, _ in usados]}
    elif mc:
        central = mc[0]
        bajo, alto = central / (1 + sesgo), central * (1 + s)
        piso = {"precio": central, "bajo": bajo, "alto": alto, "rango": [bajo, alto], "n_analogos": 0,
                "fecha": mc[1], "tipo": "estimacion", "horizonte": H, "obs": [mc[2]]}
    pisos_e = []
    for e in eventos:
        try:
            ini, fin = _d(e["inicio"]) - UN_DIA, _d(e["fin"]) + UN_DIA
        except Exception:
            continue
        a_, b_ = max(ini, t + UN_DIA), min(fin, H)
        if a_ > b_ or not usados:
            continue
        mins = []
        for a, info, camino in usados:
            dias = [d for d in camino if a_ <= d <= b_]
            if dias:
                dm = min(dias, key=lambda d: (camino[d][0], d))
                mins.append((camino[dm][0], dm, a, info, camino[dm]))
        if not mins:
            continue
        vals = sorted(m[0] for m in mins)
        low = min(mins, key=lambda m: m[0])
        pe = {"evento": e["evento"], "inicio": e["inicio"], "fin": e["fin"], "piso": _pct(vals, 50),
              "rango": [vals[0], vals[-1]], "tipo": "analogo",
              "base": _frase_base(low[2], low[1] - dt.timedelta(days=low[2]["desfase"]), low[4], low[3]["nivel"], t),
              "obs": [m[4][2] for m in mins], "fecha": low[1], "calendario": e.get("rastro", "")}
        if niv is not None and pe["piso"] > niv:   # tope de cordura: un piso esperado nunca arriba de lo normal de hoy
            pe["piso"] = niv
            pe["rango"] = [min(x, niv) for x in pe["rango"]]
            pe["base"] = "no se espera rebaja respecto de lo normal de hoy"
            pe["cordura"] = True
        pisos_e.append(pe)
    return {"t": t, "horizonte": H, "nivel": niv, "n_nivel": n_niv, "minimo_ciclo": mc, "analogos": infos,
            "piso": piso, "pisos_esperados": pisos_e, "_usados": usados}


def calcular_topes(bajo, central, alto, minimo_ciclo, nivel):
    """Terna (ganga, comprar, mirar) redondeada hacia abajo a centenas (reglas de la fase 2b).
    ganga = el menor entre bajo x 1.02 y minimo_ciclo; comprar = central x 1.02, al menos ganga x 1.03;
    mirar = el menor entre alto x 1.02 y nivel x 0.97, al menos comprar x 1.03."""
    g = _floor100(bajo * 1.02 if minimo_ciclo is None else min(bajo * 1.02, minimo_ciclo))
    c = central * 1.02
    if c < g * 1.03:
        c = g * 1.03
    c = _floor100(c)
    m = alto * 1.02 if not nivel else min(alto * 1.02, nivel * 0.97)
    if m < c * 1.03:
        m = c * 1.03
    return [g, c, _floor100(m)]


def topes_de(pron):
    """La terna de un pronostico, o None si no hay piso."""
    p = pron.get("piso")
    if not p:
        return None
    return calcular_topes(p["bajo"], p["precio"], p["alto"], pron["minimo_ciclo"][0] if pron.get("minimo_ciclo") else None,
                          pron.get("nivel"))


def sin_confirmar(pron):
    piso = pron.get("piso")
    if not piso:
        return None
    vals = [piso["bajo"]] + ([pron["minimo_ciclo"][0]] if pron.get("minimo_ciclo") else [])
    return _floor100(0.80 * min(vals))


def baja_mas(pron, precio):
    """En cuantos analogos el camino bajo de precio x 0.98 a 7 dias, 30 dias y al horizonte (con cuentas)."""
    usados, t, H = pron["_usados"], pron["t"], pron["horizonte"]
    salida, vistos = [], set()
    for L in (7, 30, (H - t).days):
        if L <= 0 or L in vistos:
            continue
        vistos.add(L)
        lim = min(t + dt.timedelta(days=L), H)
        veces, hasta = 0, None
        for _, _, camino in usados:
            vals = [camino[d][0] for d in camino if d <= lim]
            if vals and min(vals) < precio * 0.98:
                veces += 1
                hasta = min(vals) if hasta is None else min(hasta, min(vals))
        plazo = "en los %d días siguientes" % L if L in (7, 30) else "de aquí al %s" % _fecha_corta(lim, t)
        if len(usados) == 1:
            frase = ("el año parecido bajó más %s, hasta el equivalente de %s" % (plazo, _dinero(hasta)) if veces
                     else "el año parecido no bajó más %s" % plazo)
        elif veces:
            frase = "en %d de %d años parecidos bajó más %s, hasta el equivalente de %s" % (veces, len(usados), plazo, _dinero(hasta))
        else:
            frase = "en ninguno de los %d años parecidos bajó más %s" % (len(usados), plazo)
        salida.append({"dias": L, "hasta_fecha": lim.isoformat(), "veces": veces, "de": len(usados),
                       "hasta": _entero(hasta) if hasta is not None else None, "frase": frase})
    return salida


def _rastros(obs):
    vistos, salida = set(), []
    for o in obs:
        if o["rastro"] not in vistos:
            vistos.add(o["rastro"])
            salida.append(o["rastro"])
    return salida


def _ficha_pron(pron):
    """La parte JSON del pronostico."""
    f = {"nivel": None, "minimo_ciclo": None, "piso": None, "pisos_esperados": [], "analogos": []}
    if pron["nivel"] is not None:
        f["nivel"] = {"precio": _entero(pron["nivel"]), "n": pron["n_nivel"]}
    mc = pron["minimo_ciclo"]
    if mc:
        f["minimo_ciclo"] = {"precio": _num_json(round(mc[0], 2)), "fecha": mc[1].isoformat(), "tienda": mc[2]["tienda"],
                             "fuente": mc[2].get("fuente", ""), "garantia": mc[2].get("garantia_mx", ""),
                             "rastro": mc[2]["rastro"]}
    p = pron["piso"]
    if p:
        f["piso"] = {"precio": _entero(p["precio"]), "bajo": _entero(p["bajo"]),
                     "rango": [_entero(p["rango"][0]), _entero(p["rango"][1])], "n_analogos": p["n_analogos"],
                     "fecha": p["fecha"].isoformat(), "tipo": p["tipo"], "horizonte": p["horizonte"].isoformat(),
                     "rastros": _rastros(p["obs"])}
    for e in pron["pisos_esperados"]:
        f["pisos_esperados"].append({"evento": e["evento"], "inicio": e["inicio"], "fin": e["fin"],
                                     "piso": _entero(e["piso"]), "rango": [_entero(e["rango"][0]), _entero(e["rango"][1])],
                                     "tipo": e["tipo"], "base": e["base"], "rastros": _rastros(e["obs"])})
    for i in pron["analogos"]:
        a = {"nombre": i["nombre"], "desfase": i["desfase"], "usado": i["usado"], "dias_antes": i["dias_antes"],
             "dias_futuro": i["dias_futuro"]}
        if i["usado"]:
            a.update(nivel=_entero(i["nivel"]), piso=_entero(i["piso"]), fecha=i["fecha"].isoformat(),
                     fecha_analogo=i["fecha_analogo"].isoformat(), base=i["base"], rastro=i["obs"]["rastro"],
                     minimos={k: (_entero(x) if x is not None else None) for k, x in i["minimos"].items()})
        else:
            a["motivo"] = i["motivo"]
        f["analogos"].append(a)
    return f


def _escalar(ficha_base, factor):
    """Piso y pisos esperados de una variante hermana, por proporcion de lista."""
    f = {}
    p = ficha_base.get("piso")
    if p:
        f["piso"] = dict(p, precio=_entero(p["precio"] * factor), rango=[_entero(x * factor) for x in p["rango"]],
                         tipo="proporcion")
        if "bajo" in p:
            f["piso"]["bajo"] = _entero(p["bajo"] * factor)
    f["pisos_esperados"] = [dict(e, piso=_entero(e["piso"] * factor), rango=[_entero(x * factor) for x in e["rango"]],
                                 base=e["base"] + " (por proporción de lista)") for e in ficha_base.get("pisos_esperados", [])]
    f["analogos"] = []
    for a in ficha_base.get("analogos", []):
        a2 = dict(a)
        if isinstance(a.get("minimos"), dict):
            a2["minimos"] = {k: (_entero(x * factor) if x is not None else None) for k, x in a["minimos"].items()}
        for k in ("nivel", "piso"):
            if a.get(k) is not None:
                a2[k] = _entero(a[k] * factor)
        f["analogos"].append(a2)
    return f


def topes_activos(manual, calculados, n_analogos, lista, por="pronostico"):
    """Los topes que usa el vigia (fase 2c): ganga y comprar a mano; mirar se amplia solo con dos o mas anios
    parecidos (o por proporcion de una variante que los tiene). Devuelve (topes, origen, nota)."""
    manual = list(manual)
    if calculados and not (isinstance(calculados, (list, tuple)) and len(calculados) == 3
                           and all(type(x) in (int, float) for x in calculados)):
        return manual, "fracciones", "topes calculados mal formados (%r): quedan los de a mano" % (calculados,)
    if n_analogos >= 2 and calculados:
        if calculados[2] > manual[2]:
            # un aviso de mirar tiene que ser una rebaja de verdad: al menos 10 % bajo el precio de lista. Si el
            # calculo da menos que eso (PS5: 12,200 con lista de 12,499), avisaria por casi nada: queda el de a mano
            if calculados[2] <= lista * 0.90:
                return [manual[0], manual[1], calculados[2]], "a mano; mirar ampliado por %s" % por, None
            return manual, "fracciones", ("el mirar calculado (%d) no llega a 10 %% de rebaja sobre la lista (%d): "
                                          "queda el de a mano" % (calculados[2], lista))
        return manual, "fracciones", "dos años parecidos, pero el mirar calculado no supera el de a mano"
    if n_analogos == 1:
        return manual, "fracciones", "un solo año parecido: el pronóstico es orientación, no fija topes"
    return manual, "fracciones", "sin año parecido ni historia suficiente"


def _desempatar(variantes, prods):
    """Dos variantes de distinta lista nunca con la misma terna: se sube 100 la de mayor lista."""
    for p in prods:
        claves = sorted(["%s|%s" % (p["id"], v["id"]) for v in p["variantes"]], key=lambda c: variantes[c]["lista"])
        cambio = True
        while cambio:
            cambio = False
            for i, a in enumerate(claves):
                for b in claves[i + 1:]:
                    fa, fb = variantes[a], variantes[b]
                    if fa["lista"] != fb["lista"] and fa["topes"] == fb["topes"]:
                        sube, otra = (fa, b) if fb.get("topes_origen") == "topes_fijos" else (fb, a)
                        if sube.get("topes_origen") == "topes_fijos":
                            continue   # dos ternas fijas de Saul iguales: no se tocan (el vigia usa las fijas)
                        sube["topes"] = [x + 100 for x in sube["topes"]]
                        sube["topes_motivo"] = "subida 100 para no repetir la terna de %s" % otra
                        cambio = True


class Contexto(object):
    """Todo lo leido de disco, con las series diarias en cache."""

    def __init__(self, base=AQUI, hoy=None):
        self.base = base
        self.hoy = _hoy(hoy)
        self.prods, self.obs, self.sin_traducir = cargar_todo(base)
        self.por_id = {p["id"]: p for p in self.prods}
        eventos, self.origen_calendario = cargar_calendario(os.path.join(base, HISTORIA))
        self.eventos = eventos
        self.temporadas = [e for e in eventos if e.get("tipo") == "temporada" and e.get("fin")]
        self.listas, self.origen_listas = cargar_listas(os.path.join(base, HISTORIA), self.prods)
        pisos = _pisos(self.prods)
        hasta = self.hoy.isoformat()
        self.validas, self.todas = {}, {}
        for o in self.obs:
            self.todas.setdefault(o["producto"], []).append(o)
            if o["fecha"] <= hasta and clasificar(o, pisos) == "valida":
                self.validas.setdefault(o["producto"], []).append(o)
        self._series = {}

    def serie(self, producto, factores):
        clave = (producto, tuple(sorted(factores.items())))
        if clave not in self._series:
            self._series[clave] = serie_diaria(self.validas.get(producto, []), factores, self.hoy,
                                               self.todas.get(producto, []))
        return self._series[clave]

    def analogos(self, specs):
        return [dict(a, serie=self.serie(a["producto"], a["factores"])) for a in specs]


# ---------------------------------------------------------------- fase 2: prueba hacia atras

def _casos(hoy):
    h = hoy.isoformat()
    return [
        {"caso": "A", "titulo": "Fold7 512 GB, temporada 2025-26", "producto": "fold7", "factores": {"512": 1.0},
         "analogos": [dict(ANALOGOS_FOLD[1], desfase=364)], "inicio": "2025-10-15", "fin": "2026-01-31",
         "piso_producto": 12000, "lista": ("fija", LISTA_LANZAMIENTO_FOLD7_512), "fracciones": [0.411, 0.449, 0.486],
         "clave_lista": ("fold7", "512")},
        {"caso": "B", "titulo": "PS5 Digital, temporada 2025-26", "producto": "ps5", "factores": {"digital": 1.0},
         "analogos": ANALOGOS_PS5, "inicio": "2025-10-15", "fin": "2026-01-31", "piso_producto": 6500,
         "lista": ("vigente", None), "fracciones": [0.60, 0.72, 0.864], "clave_lista": ("ps5", "digital")},
        {"caso": "C", "titulo": "Fold8 Ultra 512 GB, del 15 oct 2026 a hoy", "producto": "fold8",
         "factores": {"ultra-512": 1.0}, "analogos": ANALOGOS_FOLD, "inicio": "2026-10-15", "fin": h,
         "piso_producto": 12000, "lista": ("productos", "ultra-512"), "fracciones": [0.411, 0.449, 0.486],
         "clave_lista": ("fold8", "ultra-512")},
        {"caso": "D", "titulo": "Fold8 512 GB, del 15 oct 2026 a hoy", "producto": "fold8",
         "factores": {"normal-512": 1.0}, "analogos": ANALOGOS_FOLD, "inicio": "2026-10-15", "fin": h,
         "piso_producto": 12000, "lista": ("productos", "normal-512"), "fracciones": [0.411, 0.449, 0.486],
         "clave_lista": ("fold8", "normal-512")},
        {"caso": "E", "titulo": "PS5 Digital, del 15 oct 2026 a hoy", "producto": "ps5", "factores": {"digital": 1.0},
         "analogos": ANALOGOS_PS5, "inicio": "2026-10-15", "fin": h, "piso_producto": 6500,
         "lista": ("vigente", None), "fracciones": [0.60, 0.72, 0.864], "clave_lista": ("ps5", "digital")},
    ]


def _lista_caso(ctx, caso, t):
    modo, valor = caso["lista"]
    if modo == "fija":
        return valor
    if modo == "productos":
        p = ctx.por_id[caso["producto"]]
        return next(v["lista"] for v in p["variantes"] if v["id"] == valor)
    defecto = None
    p = ctx.por_id.get(caso["producto"])
    if p:
        defecto = next((v["lista"] for v in p["variantes"] if v["id"] == caso["clave_lista"][1]), None)
    return lista_vigente(ctx.listas, caso["clave_lista"][0], caso["clave_lista"][1], t, defecto)[0]


def error_piso(ctx, caso):
    """Piso pronosticado el 15 de noviembre contra el minimo real del dia siguiente al 31 de enero (o a hoy)."""
    serie = ctx.serie(caso["producto"], caso["factores"])
    t = dt.date(_d(caso["inicio"]).year, 11, 15)
    if t > ctx.hoy:
        return None
    pron = pronostico(serie, ctx.analogos(caso["analogos"]), t, ctx.temporadas)
    fin = min(horizonte(t), ctx.hoy)
    reales = [(serie[d][0], d, serie[d][1]) for d in serie if t < d <= fin]
    if not pron["piso"] or not reales:
        return {"t": t, "piso": pron["piso"]["precio"] if pron["piso"] else None, "real": None, "error": None,
                "parcial": fin < horizonte(t), "tipo": pron["piso"]["tipo"] if pron["piso"] else None}
    real = min(reales, key=lambda x: (x[0], x[1]))
    return {"t": t, "piso": pron["piso"]["precio"], "tipo": pron["piso"]["tipo"], "real": real[0], "fecha_real": real[1],
            "tienda_real": real[2]["tienda"], "error": (pron["piso"]["precio"] - real[0]) / real[0],
            "parcial": fin < horizonte(t)}


def s_de_errores(errores):
    """(s, sesgo, explicacion). s = media de los errores en valor absoluto, acotada a [0.05, 0.12];
    sesgo = media con signo si es positiva (el piso salio alto), si no 0, acotada a 0.15."""
    vals = [e["error"] for e in errores if e and e.get("error") is not None]
    if not vals:
        return S_DEFECTO, 0.0, "sin prueba hacia atras: s = %.2f y sesgo = 0" % S_DEFECTO
    s = sum(abs(x) for x in vals) / len(vals)
    media = sum(vals) / len(vals)
    sesgo = min(SESGO_MAX, max(0.0, media))
    return (min(S_MAX, max(S_MIN, s)), sesgo,
            "de %d errores del piso: media sin signo %.3f (s acotada entre %.2f y %.2f), media con signo %+.3f (sesgo acotado a %.2f)"
            % (len(vals), s, S_MIN, S_MAX, media, SESGO_MAX))


def _nivel_aviso(precio, topes, sc):
    g, c, m = topes
    if sc and precio < sc:
        return "sin confirmar"
    return "ganga" if precio <= g else "comprar" if precio <= c else "mirar" if precio <= m else None


RANGO_NIVEL = {None: 0, "mirar": 1, "comprar": 2, "ganga": 3, "sin confirmar": 4}
METODOS_RETRO = ("a mano", "calculado", "activo")


def topes_retro(serie, analogos, t, eventos, s, sesgo, piso_producto):
    """Topes del dia t en la prueba hacia atras: se calculan como el dia anterior (solo datos de fecha < t, lo que
    el vigia sabria antes de leer el precio de ese dia). Sin cota de lista (el historial de listas viejas es
    incompleto); si el orden creciente o el piso del producto fallan, devuelve None. (topes, sin_confirmar, pron)"""
    pron = pronostico(serie, analogos, t - UN_DIA, eventos, s, sesgo)
    if not pron["piso"] or pron["piso"]["tipo"] == "estimacion":   # fase 2c: sin anio parecido no se calcula nada
        return None, None, pron
    tp = topes_de(pron)
    if tp and _terna_ok(tp, piso_producto, float("inf")):
        return tp, sin_confirmar(pron), pron
    return None, None, pron


def _activos_retro(manual, calc, pron):
    """Topes activos de un dia de la prueba hacia atras (las reglas de construir(), sin cota de lista)."""
    if not manual:
        return None, None
    n = pron["piso"]["n_analogos"] if (pron["piso"] and pron["piso"]["tipo"] != "estimacion") else 0
    tp = topes_activos(manual, calc, n, float("inf"))[0]
    if n >= 1:
        sc = sin_confirmar(pron)
    elif pron["minimo_ciclo"]:
        sc = _floor100(0.80 * pron["minimo_ciclo"][0])
    else:
        sc = None
    return tp, sc


def correr_caso(ctx, caso, s, sesgo=0.0, filas_csv=None):
    serie = ctx.serie(caso["producto"], caso["factores"])
    analogos = ctx.analogos(caso["analogos"])
    ini, fin = _d(caso["inicio"]), min(_d(caso["fin"]), ctx.hoy)
    metodos = dict((nombre, {"avisos": [], "estado": None, "precio": None, "sc_dias": [], "sin_calculo_dias": 0,
                             "estimacion_dias": 0}) for nombre in METODOS_RETRO)
    t = ini
    while t <= fin:
        lista = _lista_caso(ctx, caso, t)
        manual = [int(round(lista * f / 100.0)) * 100 for f in caso["fracciones"]] if lista else None
        calc, sc_calc, pron = topes_retro(serie, analogos, t, ctx.temporadas, s, sesgo, caso["piso_producto"])
        activo, sc_act = _activos_retro(manual, calc, pron)
        if not pron["piso"] or pron["piso"]["tipo"] == "estimacion":
            metodos["calculado"]["estimacion_dias"] += 1
        if calc is None:
            metodos["calculado"]["sin_calculo_dias"] += 1
        v = serie.get(t)
        for nombre, topes, scv in (("a mano", manual, None), ("calculado", calc, sc_calc), ("activo", activo, sc_act)):
            mt = metodos[nombre]
            lvl = _nivel_aviso(v[0], topes, scv) if (v and topes) else None
            aviso = ""
            if v and topes:
                if lvl is None:
                    mt["estado"], mt["precio"] = None, None
                elif RANGO_NIVEL[lvl] > RANGO_NIVEL[mt["estado"]] or (mt["precio"] is not None and v[0] <= mt["precio"] - BAJA_MINIMA):
                    mt["avisos"].append({"fecha": t, "nivel": lvl, "precio": v[0], "tienda": v[1]["tienda"], "rastro": v[1]["rastro"],
                                         "n_analogos": pron["piso"]["n_analogos"] if (nombre != "a mano" and pron["piso"]) else None})
                    mt["estado"], mt["precio"] = max(lvl, mt["estado"], key=lambda x: RANGO_NIVEL[x]), v[0]
                    aviso = lvl
                if lvl == "sin confirmar":
                    mt["sc_dias"].append((t, v[0]))
            if filas_csv is not None:
                filas_csv.append([caso["caso"], nombre, t.isoformat(), _entero(v[0]) if v else "", v[1]["tienda"] if v else "",
                                  topes[0] if topes else "", topes[1] if topes else "", topes[2] if topes else "",
                                  scv or "", lvl or "", aviso, (pron["piso"]["tipo"] if pron["piso"] else "") if nombre != "a mano" else ""])
        t += UN_DIA
    reales = [(serie[d][0], d, serie[d][1]) for d in serie if ini <= d <= fin]
    minimo = min(reales, key=lambda x: (x[0], x[1])) if reales else None
    res = {"caso": caso, "minimo": minimo, "metodos": {}, "error": error_piso(ctx, caso)}
    for nombre, mt in metodos.items():
        compra = [a for a in mt["avisos"] if a["nivel"] in ("comprar", "ganga")]
        gangas = [a for a in mt["avisos"] if a["nivel"] == "ganga"]
        cazo = bool(minimo) and any(a["fecha"] <= minimo[1] and a["precio"] <= minimo[0] * 1.05 for a in compra)
        primero = compra[0] if compra else None
        pganga = gangas[0] if gangas else None
        sobre = lambda a: ((a["precio"] - minimo[0]) / minimo[0]) if (a and minimo) else None
        r = {"avisos": mt["avisos"], "cazo": cazo, "primero": primero, "sc_dias": mt["sc_dias"],
             "sin_calculo_dias": mt["sin_calculo_dias"], "estimacion_dias": mt["estimacion_dias"],
             "avisos_sc": len([a for a in mt["avisos"] if a["nivel"] == "sin confirmar"]),
             "de_mas": (primero["precio"] - minimo[0]) if (primero and minimo) else None,
             "primero_pct": sobre(primero), "primera_ganga": pganga, "ganga_pct": sobre(pganga),
             "analogos_ganga": pganga.get("n_analogos") if pganga else None}
        r["sobreprecio"] = r["ganga_pct"] is not None and r["ganga_pct"] > 0.10
        motivos = []
        if not minimo:
            motivos.append("no hay precios validos en la temporada")
        elif not cazo:
            motivos.append("no aviso de comprar o ganga el dia del minimo ni antes a menos de 5 % de el")
        if len(mt["avisos"]) > MAX_AVISOS:
            motivos.append("%d avisos (el tope es %d)" % (len(mt["avisos"]), MAX_AVISOS))
        r["veredicto"] = "VERDE" if not motivos else "ROJO"
        r["motivo"] = "; ".join(motivos)
        res["metodos"][nombre] = r
    return res


def _cuenta_niveles(avisos):
    c = {}
    for a in avisos:
        c[a["nivel"]] = c.get(a["nivel"], 0) + 1
    return ", ".join("%s %d" % (k, c[k]) for k in ("mirar", "comprar", "ganga", "sin confirmar") if k in c) or "ninguno"


def retroprueba(base=AQUI, ruta_csv=None, hoy=None, escribir=True, ctx=None):
    """Corre los cinco casos. Devuelve (resultados, s, texto_md)."""
    ctx = ctx or Contexto(base, hoy)
    casos = _casos(ctx.hoy)
    s, sesgo, s_motivo = s_de_errores([error_piso(ctx, c) for c in casos if c["caso"] in ("A", "B")])
    filas = [] if ruta_csv else None
    resultados = [correr_caso(ctx, c, s, sesgo, filas) for c in casos]
    md = _retro_md(resultados, s, sesgo, s_motivo, ctx)
    if escribir:
        ruta = os.path.join(base, HISTORIA, RETRO_MD)
        carpeta = os.path.dirname(ruta)
        fd, tmp = tempfile.mkstemp(prefix=".retro.", suffix=".tmp", dir=carpeta)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(md)
            os.replace(tmp, ruta)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    if ruta_csv:
        with open(ruta_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["caso", "metodo", "fecha", "precio", "tienda", "ganga", "comprar", "mirar", "sin_confirmar",
                        "nivel", "aviso", "tipo_piso"])
            w.writerows(filas)
    return resultados, s, sesgo, md


def _veredicto_txt(x):
    return x["veredicto"] + (" con sobreprecio" if x.get("sobreprecio") else "")


def _apertura(resultados):
    """El primer parrafo de RETROPRUEBA.md (fase 2c), con las cifras de esta corrida."""
    por = {r["caso"]["caso"]: r for r in resultados}
    a, b = por.get("A"), por.get("B")
    e = a["error"] if a else None
    n_txt = ("%d %% %s" % (int(round(abs(e["error"]) * 100)), "arriba" if e["error"] >= 0 else "abajo")
             if (e and e.get("error") is not None) else "sin comparar")
    xa = a["metodos"]["calculado"] if a else None
    m_txt = ("la primera etiqueta de ganga, %d %% %s" % (int(round(abs(xa["ganga_pct"]) * 100)),
                                                       "arriba" if xa["ganga_pct"] >= 0 else "abajo")
             if (xa and xa.get("ganga_pct") is not None) else "no hubo etiqueta de ganga")
    if b:
        xb = b["metodos"]["calculado"]
        dias = (_d(b["caso"]["fin"]) - _d(b["caso"]["inicio"])).days + 1
        con = dias - xb["estimacion_dias"]
        ps5 = ("para la PS5 2025-26 no había año parecido con datos" if con <= 0 else
               "para la PS5 2025-26 solo hubo año parecido con datos en %d de %d días" % (con, dias))
    else:
        ps5 = "la PS5 2025-26 no se pudo correr"
    return ("La prueba hacia atrás no alcanza para certificar topes calculados: en el único caso probado sin conocer la "
            "respuesta (Fold7 2025-26, un solo año parecido) el piso pronosticado quedó %s del mínimo real y %s; %s. "
            "Por eso las etiquetas de ganga y comprar siguen siendo las de a mano y solo se amplía el tope de mirar donde "
            "hay dos años parecidos (Fold8)." % (n_txt, m_txt, ps5))


LIMITES = [
    "Son dos temporadas (Fold7 2025-26 y PS5 2025-26): sirve para detectar un método malo, no para certificar uno bueno.",
    "Los topes a mano se calibraron en noviembre de 2026 viendo ya la historia del Fold7, así que su resultado en el "
    "caso A no es una prueba limpia (conocían la respuesta); el método nuevo sí se probó sin verla, usando solo al Fold6.",
    "La serie de Amazon sale de una gráfica leída por píxeles (± $50, ± 1 día) y los precios de la tienda de Samsung con "
    "cupón solo se conocen por notas de prensa de días sueltos, así que el mínimo real de una temporada puede haber sido "
    "más bajo de lo registrado.",
    "No existe ninguna temporada pasada con dos años parecidos con datos, que es la situación del Fold8 hoy: ese caso "
    "no se pudo probar hacia atrás.",
]


def _retro_md(resultados, s, sesgo, s_motivo, ctx):
    L = [_apertura(resultados), "", "# Prueba hacia atrás de prospectiva.py (%s)" % ctx.hoy.isoformat(), "",
         "Cada día de la temporada se calcularon los topes solo con lo que se sabía antes de ese día (datos de fecha "
         "anterior), y se miró el precio válido más barato de ese día. Hay aviso cuando el precio entra a un nivel "
         "(mirar, comprar, ganga, sin confirmar) o cuando, ya avisado, baja %d pesos o más. Un aviso de \"sin "
         "confirmar\" también es un correo enviado, pero no cuenta como compra. Se comparan tres juegos de topes: \"a "
         "mano\" (porcentajes fijos de la lista), \"calculado\" (la terna completa del pronóstico; los días sin año "
         "parecido no hay topes calculados y se cuentan aparte) y \"activo\" (lo que hoy usa el vigía: ganga y comprar a "
         "mano, y mirar ampliado solo con dos años parecidos). \"Con sobreprecio\" = el primer aviso de ganga quedó más "
         "de 10 %% arriba del mínimo real de la temporada. VERDE/ROJO por caso usa la vara de la fase 2: informa, no "
         "decide." % BAJA_MINIMA, "",
         "Holgura entre topes (s): %.3f; sesgo del piso: %.3f (%s)." % (s, sesgo, s_motivo), ""]
    for r in resultados:
        c, m = r["caso"], r["minimo"]
        L.append("## Caso %s: %s" % (c["caso"], c["titulo"]))
        L.append("")
        if m:
            L.append("- Lo más barato de la temporada: %s el %s en %s (%s)." % (_dinero(m[0]), _fecha_corta(m[1], ctx.hoy + dt.timedelta(days=9999)), m[2]["tienda"], m[2]["rastro"]))
        else:
            L.append("- Sin precios válidos en la temporada.")
        e = r["error"]
        if e and e.get("error") is not None:
            L.append("- Piso pronosticado el %s: %s (%s); lo más bajo real después: %s el %s, error %+.1f %%%s." % (
                e["t"].isoformat(), _dinero(e["piso"]), e["tipo"], _dinero(e["real"]), e["fecha_real"].isoformat(),
                e["error"] * 100, " (parcial: la temporada no ha terminado)" if e["parcial"] else ""))
        elif e:
            L.append("- Piso del %s: no se pudo comparar (piso %s, real %s)." % (
                e["t"].isoformat(), _dinero(e["piso"]) if e.get("piso") else "sin dato", "sin dato"))
        else:
            L.append("- Piso del 15 de noviembre: todavía no llega esa fecha.")
        for nombre in METODOS_RETRO:
            x = r["metodos"][nombre]
            L.append("- Topes %s: %s. %d avisos (%s)." % (nombre, _veredicto_txt(x) + (" (" + x["motivo"] + ")" if x["motivo"] else ""),
                                                       len(x["avisos"]), _cuenta_niveles(x["avisos"])))
            if x["avisos"]:
                L.append("  - Avisos: " + "; ".join("%s %s %s" % (a["fecha"].isoformat(), a["nivel"], _dinero(a["precio"])) for a in x["avisos"]) + ".")
            L.append("  - %s." % ("Cazó el mínimo" if x["cazo"] else "No cazó el mínimo"))
            for etiqueta, a, pct in (("Primer aviso de ganga", x["primera_ganga"], x["ganga_pct"]),
                                     ("Primer aviso de comprar o ganga", x["primero"], x["primero_pct"])):
                if a:
                    L.append("  - %s: %s, %s (%s), %s del mínimo real." % (
                        etiqueta, a["fecha"].isoformat(), _dinero(a["precio"]), a["nivel"],
                        "%d %% arriba" % int(round(pct * 100)) if pct is not None else "sin comparar"))
                else:
                    L.append("  - %s: no hubo." % etiqueta)
            if x["primero"]:
                L.append("  - Quien compra en el primer aviso de comprar o ganga paga %s el %s, %s más que el mínimo." % (
                    _dinero(x["primero"]["precio"]), x["primero"]["fecha"].isoformat(), _dinero(x["de_mas"])))
            else:
                L.append("  - Nunca avisó de comprar ni de ganga.")
            if nombre != "a mano":
                L.append("  - Avisos de \"sin confirmar\" enviados: %d. Días con un precio válido real en \"sin confirmar\": %s." % (
                    x["avisos_sc"], "; ".join("%s %s" % (d.isoformat(), _dinero(p)) for d, p in x["sc_dias"]) or "ninguno"))
            if nombre == "calculado" and x["sin_calculo_dias"]:
                L.append("  - Días sin topes calculados (sin año parecido utilizable o terna fuera de orden): %d." % x["sin_calculo_dias"])
        L.append("")
    L.append("## Límites de esta prueba")
    L.append("")
    for i, lim in enumerate(LIMITES, 1):
        L.append("%d. %s" % (i, lim))
    return "\n".join(L) + "\n"


def _tabla_retro(resultados, s, sesgo):
    print("s = %.3f; sesgo = %.3f" % (s, sesgo))
    print("%-4s %-9s %-21s %-6s %-9s %-5s %-28s %-27s %-27s %-11s %s" % (
        "caso", "topes", "veredicto", "avisos", "sin conf.", "cazo", "minimo real", "primera ganga (% arriba)",
        "primer comprar/ganga (%)", "sin calcular", "error piso"))
    for r in resultados:
        m, e = r["minimo"], r["error"]
        mt = "%s %s %s" % (_dinero(m[0]), m[1].isoformat(), m[2]["tienda"]) if m else "-"
        et = ("%+.1f %%%s" % (e["error"] * 100, " parcial" if e["parcial"] else "")) if (e and e.get("error") is not None) else "-"
        for nombre in METODOS_RETRO:
            x = r["metodos"][nombre]
            def _a(a, pct):
                if not a:
                    return "-"
                return "%s %s (%s)" % (_dinero(a["precio"]), a["fecha"].isoformat(), "%+d %%" % int(round(pct * 100)) if pct is not None else "?")
            print("%-4s %-9s %-21s %-6d %-9d %-5s %-28s %-27s %-27s %-11s %s" % (
                r["caso"]["caso"], nombre, _veredicto_txt(x), len(x["avisos"]), x["avisos_sc"], "si" if x["cazo"] else "no", mt,
                _a(x["primera_ganga"], x["ganga_pct"]), _a(x["primero"], x["primero_pct"]),
                "%d dias" % x["sin_calculo_dias"] if nombre == "calculado" else "", et if nombre == "calculado" else ""))


# ---------------------------------------------------------------- fase 2: armar prospectiva.json

def construir(base=AQUI, ahora=None, ctx=None):
    """Calcula todo en memoria. Devuelve (doc, detalle, info)."""
    ahora = (ahora or dt.datetime.now(MX)).astimezone(MX).replace(microsecond=0)
    ctx = ctx or Contexto(base, ahora.date())
    hoy = ctx.hoy
    variantes, detalle = calcular(ctx.obs, ctx.prods, hoy)
    casos = _casos(hoy)
    s, sesgo, s_motivo = s_de_errores([error_piso(ctx, c) for c in casos if c["caso"] in ("A", "B")])
    prons = {}
    for p in ctx.prods:
        for v in p["variantes"]:
            clave = "%s|%s" % (p["id"], v["id"])
            f = variantes[clave]
            manual = topes_fracciones(p, v)
            f["topes_manual"] = manual
            f["analogo"] = QUIEN.get(p["id"], "modelo_anterior")
            f["s"] = round(s, 4)
            f["sesgo"] = round(sesgo, 4)
            for k, vacio in (("nivel", None), ("minimo_ciclo", None), ("piso", None), ("analogos", []),
                             ("topes_calculados", None)):
                f.setdefault(k, vacio)
            fijos = (p.get("topes_fijos") or {}).get(v["id"])
            if clave not in PROPIAS:
                continue
            serie = ctx.serie(p["id"], {v["id"]: 1.0})
            pron = pronostico(serie, ctx.analogos(PROPIAS[clave]), hoy, ctx.temporadas, s, sesgo)
            prons[clave] = pron
            detalle[clave]["pron"] = pron
            f.update(_ficha_pron(pron))
            piso = pron["piso"]
            con_base = bool(piso) and piso["tipo"] != "estimacion"
            n = piso["n_analogos"] if con_base else 0
            if not con_base:   # sin anio parecido no hay base: no se calcula piso ni topes, tampoco desde el propio precio
                f["piso"], f["pisos_esperados"] = None, []
            calc = topes_de(pron) if con_base else None
            f["topes_calculados"] = calc
            if fijos:
                f["topes"], f["topes_origen"] = [int(x) for x in fijos], "topes_fijos"
            else:
                f["topes"], f["topes_origen"], nota = topes_activos(manual, calc, n, v["lista"])
                if nota:
                    f["topes_nota"] = nota
            mc = pron["minimo_ciclo"]
            if n >= 1:
                f["sin_confirmar_bajo"] = sin_confirmar(pron)
            elif mc:
                f["sin_confirmar_bajo"] = _floor100(0.80 * mc[0])
            if pron["_usados"]:
                f["baja_mas"] = [{"tope": nm, "precio": x, "plazos": baja_mas(pron, x)}
                                 for nm, x in zip(("ganga", "comprar", "mirar"), f["topes"])]
    for clave, base_clave in HERMANAS.items():
        if clave not in variantes or base_clave not in variantes:
            continue
        f, fb = variantes[clave], variantes[base_clave]
        pid, vid = clave.split("|")
        p = ctx.por_id[pid]
        v = next(x for x in p["variantes"] if x["id"] == vid)
        factor = float(v["lista"]) / float(fb["lista"])
        f.update(_escalar(fb, factor))
        calc_b = fb.get("topes_calculados")
        f["topes_calculados"] = [_floor100(x * factor) for x in calc_b] if calc_b else None
        n = (fb.get("piso") or {}).get("n_analogos") or 0
        if fb.get("sin_confirmar_bajo"):
            f["sin_confirmar_bajo"] = _floor100(fb["sin_confirmar_bajo"] * factor)
        fijos = (p.get("topes_fijos") or {}).get(vid)
        if fijos:
            f["topes"], f["topes_origen"] = [int(x) for x in fijos], "topes_fijos"
            continue
        f["topes"], f["topes_origen"], nota = topes_activos(f["topes_manual"], f["topes_calculados"], n, v["lista"],
                                                            "proporcion de %s" % base_clave.split("|")[1])
        if nota:
            f["topes_nota"] = nota
    _desempatar(variantes, ctx.prods)
    for clave, f in variantes.items():   # una terna subida por el desempate tambien debe caber en su rango
        p = ctx.por_id[clave.split("|")[0]]
        if not _terna_ok(f["topes"], p["piso"], f["lista"]):
            f["topes"], f["topes_origen"] = list(f["topes_manual"]), "fracciones"
            f["topes_motivo"] = "fuera de rango tras el desempate"
            f["sin_confirmar_bajo"] = None
    for f in variantes.values():   # "sin confirmar" nunca arriba de 0.9 x ganga (tambien con topes_fijos)
        sc = f.get("sin_confirmar_bajo")
        cota = _floor100(f["topes"][0] * 0.9)
        if sc is not None and sc > cota:
            f["sin_confirmar_bajo"] = cota
            f["sin_confirmar_motivo"] = "acotado a 0.9 x ganga (antes %d)" % sc
    doc = {"version": VERSION, "generado": ahora.isoformat(),
           "vence": (hoy + dt.timedelta(days=VIGENCIA_DIAS)).isoformat(), "metodo": METODO, "variantes": variantes}
    info = {"observaciones": len(ctx.obs), "sin_traducir": ctx.sin_traducir, "eventos": len(ctx.eventos),
            "temporadas": len(ctx.temporadas), "origen_calendario": ctx.origen_calendario, "listas": len(ctx.listas),
            "origen_listas": ctx.origen_listas, "s": s, "sesgo": sesgo, "s_motivo": s_motivo, "ctx": ctx}
    return doc, detalle, info


def escribir_json(doc, ruta):
    """Escritura atomica: archivo temporal en la misma carpeta y luego renombrar."""
    carpeta = os.path.dirname(os.path.abspath(ruta))
    fd, tmp = tempfile.mkstemp(prefix=".prospectiva.", suffix=".tmp", dir=carpeta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, ruta)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def generar(base=AQUI, ahora=None, ruta=None):
    """Calcula todo y escribe prospectiva.json. Devuelve (doc, detalle, info)."""
    doc, detalle, info = construir(base, ahora)
    escribir_json(doc, ruta or os.path.join(base, SALIDA))
    return doc, detalle, info


def recalcular(base=AQUI, ahora=None, ruta=None, si_viejo=None):
    """Como generar, pero solo reemplaza el archivo si el resultado valida y ningun tope salta mas de 15 %.
    Ante cualquier problema deja el archivo anterior intacto y lo dice por stderr. Devuelve 0 siempre.
    si_viejo=H: no hace nada si el archivo se genero hace menos de H horas (vigia.yml lo corre en cada corrida con
    20: en la practica, una vez al dia; asi prospectiva.json no caduca solo a los 30 dias)."""
    ruta = ruta or os.path.join(base, SALIDA)
    if si_viejo is not None and os.path.exists(ruta):
        try:
            with open(ruta, encoding="utf-8") as f:
                gen = dt.datetime.fromisoformat(str(json.load(f).get("generado", "")))
            edad = ((ahora or dt.datetime.now(MX)) - gen).total_seconds() / 3600
            if 0 <= edad < si_viejo:
                print("prospectiva.json tiene %.1f h (menos de %s): no se recalcula" % (edad, si_viejo))
                return 0
        except Exception as e:   # fecha ilegible o sin zona: se recalcula, que es lo que se pidio
            _aviso("recalcular: no se leyo la edad del archivo (%s); se recalcula" % e)
    try:
        doc, _, info = construir(base, ahora)
        prods = {p["id"]: p for p in info["ctx"].prods}
        limpio, fallas = _validar(doc["variantes"], prods)
        if fallas:
            _aviso("recalcular: no se reemplaza (%s)" % "; ".join(fallas))
            return 0
        if os.path.exists(ruta):
            try:
                with open(ruta, encoding="utf-8") as f:
                    anterior = json.load(f).get("variantes") or {}
            except Exception as e:
                anterior = {}
                _aviso("recalcular: el archivo anterior no se pudo leer (%s); se reemplaza" % e)
            saltos = []
            for clave, f in doc["variantes"].items():
                ant = anterior.get(clave) if isinstance(anterior.get(clave), dict) else {}
                n_ant = (ant.get("piso") or {}).get("n_analogos") if isinstance(ant.get("piso"), dict) else 0
                n_new = (f.get("piso") or {}).get("n_analogos") if isinstance(f.get("piso"), dict) else 0
                baja = isinstance(n_ant, int) and n_ant >= 2 and not (isinstance(n_new, int) and n_new >= 2)
                if baja:
                    _aviso("recalcular: %s paso de %d a %s anios parecidos; su mirar vuelve al de a mano (%s)"
                           % (clave, n_ant, n_new or 0, f["topes"][2]))
                viejo = ant.get("topes")
                if not isinstance(viejo, list) or len(viejo) != 3:
                    continue
                for n, a, b in zip(("ganga", "comprar", "mirar"), viejo, f["topes"]):
                    if baja and n == "mirar":
                        continue   # volver al mirar de a mano es la direccion segura: no lo frena el resguardo del 15 %
                    a = _numero(a)
                    if a and isinstance(b, (int, float)) and abs(b - a) / a > SALTO_MAX:
                        saltos.append("%s %s de %s a %s" % (clave, n, _num_json(a), b))
            if saltos:
                _aviso("recalcular: no se reemplaza; topes que se mueven mas de %d %% (revisar a mano con --generar): %s"
                       % (int(SALTO_MAX * 100), "; ".join(saltos)))
                return 0
        escribir_json(doc, ruta)
        print("prospectiva.json reemplazado (%s)" % doc["generado"])
    except Exception as e:
        _aviso("recalcular: no se reemplaza (%s: %s)" % (type(e).__name__, e))
    return 0


# ---------------------------------------------------------------- lo que llama vigia.py

def _terna_ok(topes, piso, lista):
    if not isinstance(topes, list) or len(topes) != 3:
        return False
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in topes):
        return False
    g, c, m = topes
    return g < c < m and piso <= g and m <= lista


def _validar(variantes, prods):
    """(variantes validas, fallas). Si una variante de un producto falla, se quita todo ese producto."""
    por_prod, fallas = {}, []
    for clave, datos in variantes.items():
        pid = str(clave).split("|", 1)[0]
        if pid not in prods:
            fallas.append("%s no esta en productos.json" % clave)
            continue
        por_prod.setdefault(pid, {})[clave] = datos
    limpio = {}
    for pid, grupo in por_prod.items():
        p = prods[pid]
        listas = {"%s|%s" % (pid, v["id"]): v["lista"] for v in p["variantes"]}
        por_var = {"%s|%s" % (pid, v["id"]): v for v in p["variantes"]}
        falla, ternas = None, {}
        for clave, datos in sorted(grupo.items()):
            fijos = (p.get("topes_fijos") or {}).get(por_var[clave]["id"]) if clave in por_var else None
            if clave not in listas:
                falla = "%s no esta en productos.json" % clave
            elif not isinstance(datos, dict) or not _terna_ok(datos.get("topes"), p["piso"], listas[clave]):
                falla = "%s trae topes no validos (%r)" % (clave, datos.get("topes") if isinstance(datos, dict) else datos)
            elif datos.get("topes_calculados") is not None and not (
                    isinstance(datos["topes_calculados"], list) and len(datos["topes_calculados"]) == 3
                    and all(isinstance(x, int) and not isinstance(x, bool) for x in datos["topes_calculados"])):
                falla = "%s trae topes_calculados no validos (%r)" % (clave, datos["topes_calculados"])
            elif datos.get("sin_confirmar_bajo") is not None and not (
                    type(datos["sin_confirmar_bajo"]) is int and 0 < datos["sin_confirmar_bajo"] < datos["topes"][0]):
                falla = "%s trae sin_confirmar_bajo no valido (%r)" % (clave, datos["sin_confirmar_bajo"])
            elif datos.get("topes_manual") != topes_fracciones(p, por_var[clave]):
                falla = "%s: las fracciones o la lista de productos.json cambiaron desde que se genero (%r, hoy %r)" % (
                    clave, datos.get("topes_manual"), topes_fracciones(p, por_var[clave]))
            elif fijos and list(datos["topes"]) != [int(x) for x in fijos]:
                falla = "%s: productos.json tiene topes_fijos %r y el archivo trae %r" % (clave, list(fijos), datos["topes"])
            else:
                t = tuple(datos["topes"])
                otra = ternas.get(t)
                if otra and listas[otra] != listas[clave]:
                    falla = "%s y %s tienen listas distintas y la misma terna %s" % (otra, clave, list(t))
                ternas.setdefault(t, clave)
            if falla:
                break
        if falla:
            fallas.append("%s; el producto %s vuelve a sus fracciones" % (falla, pid))
            continue
        limpio.update(grupo)
    return limpio, fallas


def _cargar_json(ruta, hoy):
    ruta = ruta or os.path.join(AQUI, SALIDA)
    nombre = os.path.basename(ruta)
    if not os.path.exists(ruta):
        _aviso("%s no existe; se usan las fracciones" % nombre)
        return {}
    with open(ruta, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or type(doc.get("version")) is not int or doc["version"] != VERSION:
        _aviso("%s con version distinta de %d; no se usa" % (nombre, VERSION))
        return {}
    vence = dt.date.fromisoformat(str(doc.get("vence", ""))[:10])
    if _hoy(hoy) > vence:
        _aviso("%s vencio el %s; no se usa" % (nombre, vence.isoformat()))
        return {}
    variantes = doc.get("variantes")
    if not isinstance(variantes, dict):
        _aviso("%s sin 'variantes'; no se usa" % nombre)
        return {}
    prods = {p["id"]: p for p in leer_productos(os.path.join(os.path.dirname(os.path.abspath(ruta)), CONFIG))}
    limpio, fallas = _validar(variantes, prods)
    for falla in fallas:
        _aviso("%s: %s" % (nombre, falla))
    return limpio


def cargar_json(ruta=None, hoy=None):
    """Variantes validadas de prospectiva.json, o {} ante cualquier problema. Nunca lanza excepcion."""
    try:
        return _cargar_json(ruta, hoy)
    except Exception as e:
        _aviso("prospectiva.json no se usa (%s: %s)" % (type(e).__name__, e))
        return {}


def _eventos_validos(d, hoy):
    pisos = d.get("pisos_esperados") if isinstance(d.get("pisos_esperados"), list) else []
    salida = []
    for e in pisos:
        if not isinstance(e, dict):
            continue
        piso, nombre = _numero(e.get("piso")), e.get("evento")
        try:
            fin = _fecha_txt(e.get("fin"))
        except Exception:
            continue
        try:
            inicio = _fecha_txt(e.get("inicio"))
        except Exception:
            inicio = fin
        if piso is None or not isinstance(nombre, str) or not nombre.strip() or fin < hoy.isoformat():
            continue
        salida.append((inicio, nombre.strip(), piso, fin))
    return sorted(salida)


PROHIBIDAS = re.compile(r"\bpiso|años\s+parecidos|an[aá]logo|\bnivel|\bcentral|proporci[oó]n|\bficha", re.I)
HERMANA_TXT = "calculado a partir del de 512 GB"
ENCABEZADO = {"sin confirmar": "Sin confirmar:", "ganga": "Ganga", "comprar": "Buen precio para comprar", "mirar": "Conviene esperar:",
              None: "Conviene esperar:"}


def _fecha_aviso(txt, hoy):
    """'13 oct'; con el anio solo si la fecha queda a mas de 300 dias de hoy."""
    try:
        f = dt.date.fromisoformat(str(txt).strip()[:10])
    except Exception:
        return ""
    s = "%d %s" % (f.day, MESES[f.month - 1])
    return s if abs((f - hoy).days) <= 300 else "%s %d" % (s, f.year)


def _etiqueta_aviso(p, d):
    """La etiqueta que pondra vigia.nivel() con estos datos: 'sin confirmar', 'ganga', 'comprar', 'mirar', None
    (arriba de todos los topes) o '?' (sin topes con que saberlo)."""
    sc = _numero(d.get("sin_confirmar_bajo"))
    if sc is not None and p < sc:
        return "sin confirmar"
    tp = d.get("topes")
    if not (isinstance(tp, list) and len(tp) == 3):
        return "?"
    g, c, m = [_numero(x) for x in tp]
    if g is None or c is None or m is None:
        return "?"
    return "ganga" if p <= g else "comprar" if p <= c else "mirar" if p <= m else None


def _minimo_citado(d, hoy):
    """El minimo que se cita (regla A12-6): el de toda la historia si tiene menos de 180 dias; si no, el de los
    ultimos cuatro meses (minimo_ciclo); None si no hay. Devuelve precio, donde, segun y tipo ('salio' o 'meses')."""
    mn = d.get("minimo") if isinstance(d.get("minimo"), dict) else {}
    mc = d.get("minimo_ciclo") if isinstance(d.get("minimo_ciclo"), dict) else {}
    usar, tipo, edad = None, None, None
    if _numero(mn.get("precio")) is not None:
        try:
            edad = (hoy - dt.date.fromisoformat(str(mn.get("fecha")).strip()[:10])).days
        except Exception:
            edad = None
        if edad is not None and edad < 180:
            usar, tipo = mn, "salio"
    if usar is None and _numero(mc.get("precio")) is not None:
        usar, tipo = mc, "meses"
    if usar is None and _numero(mn.get("precio")) is not None and edad is None:   # sin fecha: no se sabe la edad
        usar, tipo = mn, "salio"
    if usar is None:
        return None
    fecha = _fecha_aviso(usar.get("fecha"), hoy)
    tienda = usar.get("tienda").strip() if isinstance(usar.get("tienda"), str) else ""
    donde = " ".join(x for x in ("el " + fecha if fecha else "", "en " + tienda if tienda else "") if x)
    segun = {"foro": "según el foro de ofertas", "prensa": "según la prensa"}.get(str(usar.get("fuente") or "").strip().lower(), "")
    return {"precio": _numero(usar["precio"]), "donde": donde, "segun": segun, "tipo": tipo,
            "nombre": "lo más barato visto" if tipo == "salio" else "lo más barato de los últimos cuatro meses"}


def _detalle(m, coma):
    """'$X, el D en T, segun ...' (coma=True) o '$X el D en T, segun ...'."""
    if m["donde"]:
        txt = _dinero(m["precio"]) + (", " if coma else " ") + m["donde"]
        return txt + (", " + m["segun"] if m["segun"] else "")
    return _dinero(m["precio"]) + (", " + m["segun"] if m["segun"] else "")


def _contra_minimo(p, m, corto):
    det = _dinero(m["precio"]) if corto else None
    if abs(p - m["precio"]) < IGUAL_MARGEN:
        return "iguala %s (%s)" % (m["nombre"], det or _detalle(m, True))
    if p < m["precio"]:
        record = ("%s es lo más barato visto en México desde que salió" if m["tipo"] == "salio"
                  else "%s es lo más barato de los últimos cuatro meses") % _dinero(p)
        return "%s (antes, %s)" % (record, det or _detalle(m, False))
    return "está %s arriba de %s (%s)" % (_dinero(p - m["precio"]), m["nombre"], det or _detalle(m, True))


def _contra_esperado(p, e, corto):
    aqui = "" if (corto or not e["hasta"]) else " de aquí al %s" % e["hasta"]
    nota = ", " + HERMANA_TXT if e["hermana"] else ""
    if p < e["central"]:
        return "queda debajo de lo más bajo que se espera%s (unos %s%s)" % (aqui, _dinero(e["central"]), nota)
    if p <= e["central"] * 1.02:
        return "está en lo más bajo que se espera%s (unos %s%s)" % (aqui, _dinero(e["central"]), nota)
    return "lo más bajo que se espera%s ronda %s%s" % (aqui, _dinero(e["central"]), " (%s)" % HERMANA_TXT if e["hermana"] else "")


def _esperado(d, hoy):
    """El pronostico, solo con al menos un anio para comparar: central, hasta, hermana, n y las bajas de cada anio."""
    piso = d.get("piso") if isinstance(d.get("piso"), dict) else {}
    n = piso.get("n_analogos")
    central = _numero(piso.get("precio"))
    if not isinstance(n, int) or isinstance(n, bool) or n < 1 or central is None:
        return None
    eventos = [x[2] for x in _eventos_validos(d, hoy)]   # "lo mas bajo que se espera" no queda arriba de un evento (C10)
    if eventos:
        central = min(central, min(eventos))
    analogos = d.get("analogos") if isinstance(d.get("analogos"), list) else []
    mins = [_numero((a.get("minimos") or {}).get("horizonte")) for a in analogos
            if isinstance(a, dict) and a.get("usado") and isinstance(a.get("minimos"), dict)]
    return {"central": central, "hasta": _fecha_aviso(piso.get("horizonte"), hoy), "hermana": piso.get("tipo") == "proporcion",
            "n": n, "mins": [x for x in mins if x is not None]}


def _baja_mas_txt(p, e):
    """Si en los anios comparables el precio bajo mas que p (2 % o mas) de aqui al fin del pronostico."""
    mins = e["mins"]
    bajaron = [x for x in mins if x < p * 0.98]
    if not mins:
        return None
    if len(mins) == 1:
        return ("Puede bajar más (el año pasado bajó hasta unos %s), pero no es seguro." % _dinero(min(bajaron)) if bajaron
                else "El año pasado no bajó más.")
    if bajaron:
        return "Puede bajar más (pasó %d de cada %d años, hasta unos %s), pero no es seguro." % (len(bajaron), len(mins), _dinero(min(bajaron)))
    return "En los años comparables no bajó más."


def _versiones(cabeza, clausulas, cola=""):
    """Versiones de una frase de largo a corto: clausulas = [(larga, corta) o None]; se acorta de atras hacia adelante."""
    cl = [c for c in clausulas if c]
    salida = []
    pasos = [[c[0] for c in cl]]
    for i in range(len(cl) - 1, -1, -1):          # cada clausula a su forma corta, de la ultima a la primera
        actual = list(pasos[-1])
        actual[i] = cl[i][1]
        pasos.append(actual)
    for i in range(len(cl) - 1, -1, -1):          # luego se quitan, de la ultima a la primera
        actual = list(pasos[-1])
        actual[i] = None
        pasos.append(actual)
    for paso in pasos:
        cuerpo = " y ".join(x for x in paso if x)
        txt = "%s %s." % (cabeza, cuerpo) if cuerpo else "%s." % cabeza.rstrip(":")
        if cola:
            txt = (txt + " " + cola).strip()
        if txt and txt not in salida:
            salida.append(txt)
    return salida


def _razon(precio, datos, hoy):
    p = _numero(precio)
    if p is None:
        return ""
    d = datos if isinstance(datos, dict) else {}
    try:
        hoy = _hoy(hoy)
    except Exception:
        hoy = _hoy(None)
    etiqueta = _etiqueta_aviso(p, d)
    m = _minimo_citado(d, hoy)
    e = _esperado(d, hoy) if etiqueta != "sin confirmar" else None
    piezas = []   # cada pieza: versiones de larga a corta; None al final = se puede quitar
    if etiqueta == "sin confirmar":   # nunca se recorta
        piezas.append(["Sin confirmar: está muy por debajo de todo lo visto en cuatro meses. Antes de creerlo, abre la página "
                       "del producto en la tienda y revisa quién vende."])
        if m and p < m["precio"]:
            piezas.append(["Si es real, sería %s (antes, %s)." % (m["nombre"], _dinero(m["precio"])), None])
    elif etiqueta in ("ganga", "comprar"):
        cabeza = "Ganga:" if etiqueta == "ganga" else "Buen precio para comprar:"
        cola = "Compra si quien vende da garantía en México." if etiqueta == "ganga" else ""
        cl = [(_contra_minimo(p, m, False), _contra_minimo(p, m, True)) if m else None,
              (_contra_esperado(p, e, False), _contra_esperado(p, e, True)) if e else None]
        piezas.append(_versiones(cabeza, cl, cola))
        if etiqueta == "comprar" and e and _baja_mas_txt(p, e):
            piezas.append([_baja_mas_txt(p, e), None])
    else:
        if e and etiqueta in ("mirar", None):
            quien = "la misma consola" if d.get("analogo") == "misma_consola" else "el modelo anterior"
            nota = "; " + HERMANA_TXT if e["hermana"] else ""
            vienen = [x for x in _eventos_validos(d, hoy) if x[2] < p]
            tp = d.get("topes")
            comprar = _numero(tp[1]) if isinstance(tp, list) and len(tp) == 3 else None
            if vienen:
                ini, nom, val, fin = vienen[0]   # evento ya empezado: "de aqui al <fin>", no "hacia" (C10)
                cuando = ("de aquí al %s (%s)" % (_fecha_aviso(fin, hoy), nom) if ini <= hoy.isoformat() else "hacia %s" % nom)
                piezas.append(["Conviene esperar: %s se espera alrededor de %s (así bajó %s en estas fechas, llevado al precio "
                               "de hoy%s)." % (cuando, _dinero(val), quien, nota),
                               "Conviene esperar: %s se espera alrededor de %s%s." % (
                                   cuando, _dinero(val), " (%s)" % HERMANA_TXT if e["hermana"] else "")])
            elif p <= e["central"] * 1.02 and comprar is not None:   # ya debajo de lo esperado: sin cifra esperada (C10)
                piezas.append(["Conviene esperar: todavía no llega a tu precio de compra (%s)." % _dinero(comprar)])
            else:
                piezas.append(["Conviene esperar: %s." % _contra_esperado(p, e, False),
                               "Conviene esperar: %s." % _contra_esperado(p, e, True)])
        elif e:   # sin topes: no se sabe la etiqueta del correo; solo lo esperado
            piezas.append([(lambda t: t[0].upper() + t[1:] + ".")(_contra_esperado(p, e, False)), None])
        if m:
            piezas.append(["%s: %s." % (m["nombre"][0].upper() + m["nombre"][1:], _detalle(m, True)),
                           "%s: %s." % (m["nombre"][0].upper() + m["nombre"][1:], _dinero(m["precio"])), None])
    if e and e["n"] == 1:
        piezas.append(["Es orientación: solo hay un año para comparar.", None])
    tip = d.get("tipico") if isinstance(d.get("tipico"), dict) else {}
    tpc = _numero(tip.get("precio"))
    if etiqueta != "sin confirmar" and tpc is not None and abs(p - tpc) / tpc > TIPICO_MARGEN:
        piezas.append(["En el último mes costaba unos %s." % _dinero(int(round(tpc / 100.0)) * 100), None])
    pos = [0] * len(piezas)
    armar = lambda: " ".join(v[pos[i]] for i, v in enumerate(piezas) if v[pos[i]])
    while len(armar()) > MAX_RAZON:   # se recorta de atras hacia adelante
        for i in range(len(piezas) - 1, -1, -1):
            if pos[i] < len(piezas[i]) - 1:
                pos[i] += 1
                break
        else:
            break
    texto = armar()
    if len(texto) > MAX_RAZON:
        texto = texto[:MAX_RAZON - 3].rsplit(" ", 1)[0] + "..."
    return texto


def razon(precio, datos, hoy=None):
    """Frases en lenguaje llano (320 caracteres como maximo) de por que el precio es bueno o no."""
    try:
        return _razon(precio, datos, hoy)
    except Exception:
        return ""


# ---------------------------------------------------------------- pruebas

PRODS_PRUEBA = {"productos": [
    {"id": "fold8", "nombre": "Galaxy Z Fold8", "piso": 12000, "fracciones": [0.411, 0.449, 0.486], "topes_fijos": {},
     "variantes": [{"id": "ultra-512", "nombre": "Fold8 Ultra 512 GB", "lista": 53499},
                   {"id": "ultra", "nombre": "Fold8 Ultra (capacidad sin dato)", "lista": 53499},
                   {"id": "normal-512", "nombre": "Fold8 512 GB", "lista": 48499}]},
    {"id": "ps5", "nombre": "PlayStation 5", "piso": 6500, "fracciones": [0.60, 0.72, 0.864], "topes_fijos": {},
     "variantes": [{"id": "digital", "nombre": "PS5 Digital (sin lector)", "lista": 12499}]}]}


def _escribir(ruta, texto):
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        f.write(texto)


def _obs_prueba(fecha, precio, **k):
    o = {c: "" for c in COLUMNAS}
    o.update({"fecha": fecha, "producto": "fold8", "variante": "ultra-512", "tienda": "Samsung", "precio": float(precio),
              "tipo_banco": "ninguno", "garantia_mx": "si", "fuente": "propio", "revision": None, "archivo": "prueba.csv",
              "renglon": 0, "rastro": "prueba:%s:%s:%s" % (fecha, precio, k.get("id", ""))})
    o.update(k)
    return o


def _serie_fija(inicio, dias, precio, cambios=None):
    """Serie {fecha: (precio, obs)} de prueba: 'dias' dias desde 'inicio' a 'precio', con cambios {k: precio}."""
    cambios = cambios or {}
    s = {}
    for k in range(dias):
        d = inicio + dt.timedelta(days=k)
        pr = cambios.get(k, precio)
        s[d] = (float(pr), _obs_prueba(d.isoformat(), pr, id="S%d" % k))
    return s


def _selftest_fase2():
    D = dt.date
    # relleno hacia adelante de keepa, de un punto suelto y de 'propio'
    todas = [_obs_prueba("2026-01-01", 30000, fuente="keepa", id="X:2026-01-01", archivo="k.csv"),
             _obs_prueba("2026-01-05", 29000, fuente="keepa", id="X:2026-01-05", archivo="k.csv"),
             _obs_prueba("2026-01-08", 29500, fuente="keepa", id="X:2026-01-08", archivo="k.csv", nota="último día del tramo"),
             _obs_prueba("2026-01-20", 28000, fuente="keepa", id="X:2026-01-20", archivo="k.csv"),
             _obs_prueba("2026-01-22", 27000, fuente="keepa", id="Y:2026-01-22:tercero", archivo="k.csv", garantia_mx="no"),
             _obs_prueba("2026-02-01", 26000, fuente="prensa", id="P1"),
             _obs_prueba("2026-03-01", 25000, fuente="propio", id="V1", tienda="Samsung"),
             _obs_prueba("2026-03-02", 25500, fuente="propio", id="V2", tienda="Samsung")]
    validas = [o for o in todas if o["garantia_mx"] == "si"]
    s = serie_diaria(validas, {"ultra-512": 1.0}, D(2026, 3, 31), todas)
    assert [s[D(2026, 1, k)][0] for k in (1, 4, 5, 7, 8)] == [30000, 30000, 29000, 29000, 29500]
    assert D(2026, 1, 9) not in s and D(2026, 1, 19) not in s, "el ultimo dia del tramo corta el relleno"
    assert s[D(2026, 1, 22)][0] == 28000 and D(2026, 1, 23) not in s, "el ultimo renglon vale hasta la fecha mas reciente del archivo"
    assert [s.get(D(2026, 2, k), (None,))[0] for k in (1, 2, 3, 4)] == [26000, 26000, 26000, None]
    assert [s.get(D(2026, 3, k), (None,))[0] for k in (1, 2, 3, 4, 5)] == [25000, 25500, 25500, 25500, None]
    todas2 = [dict(o, nota="") for o in todas]
    s2 = serie_diaria([o for o in todas2 if o["garantia_mx"] == "si"], {"ultra-512": 1.0}, D(2026, 3, 31), todas2)
    assert s2[D(2026, 1, 19)][0] == 29500 and s2[D(2026, 1, 20)][0] == 28000, "sin marca de tramo sigue hasta el renglon siguiente"
    s3 = serie_diaria(validas, {"ultra-512": 1.0}, D(2026, 1, 6), todas)
    assert max(s3) == D(2026, 1, 6) and all(s3[d] == s[d] for d in s3), "cortar en t = solo datos hasta t"
    s4 = serie_diaria([_obs_prueba("2025-11-01", 56499, variante="1tb", producto="fold7", fuente="prensa")],
                      FACTOR_FOLD7, D(2025, 12, 1), [])
    assert abs(s4[D(2025, 11, 1)][0] - 48999) < 0.01, "1 TB convertido a 512 por proporcion de lista"
    # camino simulado con una serie conocida, desfases de 364 y 728
    t = D(2026, 3, 1)                           # horizonte = t + 90 = 2026-05-30
    este = _serie_fija(t - dt.timedelta(days=29), 30, 100)            # nivel de hoy = 100
    a1 = _serie_fija(t - dt.timedelta(days=364 + 29), 70, 200, {39: 120})   # nivel 200; t+10 cae en 120
    a2 = _serie_fija(t - dt.timedelta(days=728 + 29), 70, 200, {34: 140})   # nivel 200; t+5 cae en 140
    corto = _serie_fija(t - dt.timedelta(days=364 + 9), 50, 200)            # solo 10 dias antes de la fecha desfasada
    ans = [{"nombre": "A1", "corto": "el A1", "desfase": 364, "serie": a1},
           {"nombre": "A2", "corto": "el A2", "desfase": 728, "serie": a2},
           {"nombre": "C", "corto": "el C", "desfase": 364, "serie": corto}]
    ev = [{"evento": "Evento X", "inicio": "2026-03-04", "fin": "2026-03-07", "tipo": "temporada"}]
    pr = pronostico(este, ans, t, ev)
    # a mano: camino_A1(t+10) = 100 x 120 / 200 = 60; camino_A2(t+5) = 100 x 140 / 200 = 70; los demas dias 100
    usados = {i["nombre"]: i for i in pr["analogos"] if i["usado"]}
    assert sorted(usados) == ["A1", "A2"], pr["analogos"]
    assert abs(usados["A1"]["piso"] - 60) < 1e-9 and usados["A1"]["fecha"] == t + dt.timedelta(days=10)
    assert abs(usados["A2"]["piso"] - 70) < 1e-9 and usados["A2"]["fecha"] == t + dt.timedelta(days=5)
    assert usados["A1"]["fecha_analogo"] == t + dt.timedelta(days=10) - dt.timedelta(days=364)
    nousado = [i for i in pr["analogos"] if not i["usado"]][0]
    assert nousado["nombre"] == "C" and "hacen falta 15" in nousado["motivo"], nousado
    assert abs(pr["piso"]["precio"] - 65) < 1e-9 and pr["piso"]["rango"] == [60, 70] and pr["piso"]["tipo"] == "analogo"
    assert pr["piso"]["bajo"] == 60 and pr["piso"]["alto"] == 70 and pr["piso"]["n_analogos"] == 2, "dos anios: menor, mediana, mayor"
    uno = pronostico(este, [ans[0]], t, ev, 0.10, 0.05)
    # un anio: central = 60; bajo = 60 / 1.05 = 57.142857...; alto = 60 x 1.10 = 66
    assert uno["piso"]["n_analogos"] == 1 and abs(uno["piso"]["precio"] - 60) < 1e-9
    assert abs(uno["piso"]["bajo"] - 60 / 1.05) < 1e-9 and abs(uno["piso"]["alto"] - 66) < 1e-9
    assert pr["horizonte"] == D(2026, 5, 30) and pr["nivel"] == 100
    # evento del 4 al 7 de marzo, con holgura del 3 al 8: A1 da 100 (su 60 cae el 11), A2 da 70 el 6 -> mediana 85
    assert len(pr["pisos_esperados"]) == 1 and abs(pr["pisos_esperados"][0]["piso"] - 85) < 1e-9
    assert pr["pisos_esperados"][0]["rango"] == [70, 100]
    bm = baja_mas(pr, 72)   # 72 x 0.98 = 70.56: a 7 dias solo A2 (70); a 30 dias los dos (hasta 60)
    assert (bm[0]["veces"], bm[0]["de"], bm[0]["hasta"]) == (1, 2, 70) and (bm[1]["veces"], bm[1]["hasta"]) == (2, 60), bm
    assert "en 1 de 2 años parecidos bajó más en los 7 días siguientes" in bm[0]["frase"]
    sin = pronostico(este, [ans[2]], t, ev, 0.10, 0.05)
    assert sin["piso"]["tipo"] == "estimacion" and sin["piso"]["precio"] == 100 and sin["pisos_esperados"] == []
    # ninguno: central = minimo de 120 dias (100); bajo = 100 / 1.05; alto = 110
    assert sin["piso"]["n_analogos"] == 0 and abs(sin["piso"]["bajo"] - 100 / 1.05) < 1e-9 and abs(sin["piso"]["alto"] - 110) < 1e-9
    assert horizonte(D(2026, 11, 25)) == D(2027, 1, 31) and horizonte(D(2026, 1, 10)) == D(2026, 1, 31)
    # topes (fase 2b), calculados a mano:
    # dos anios: ganga = min(23670 x 1.02 = 24143, 26081) -> 24100; comprar = 26054 x 1.02 = 26575 -> 26500;
    # mirar = min(28439 x 1.02 = 29008, 36010 x 0.97 = 34930) -> 29000
    assert calcular_topes(23670, 26054, 28439, 26081, 36010) == [24100, 26500, 29000]
    assert calcular_topes(30000, 31000, 33000, 29000, 40000) == [29000, 31600, 33600], "ganga acotada por minimo_ciclo"
    # comprar = 30600 < ganga x 1.03 = 31518 -> 31500; mirar = 30600 < 31500 x 1.03 = 32445 -> 32400
    assert calcular_topes(30000, 30000, 30000, None, None) == [30600, 31500, 32400], "separacion minima de 3 %"
    # mirar = min(33660, 33000 x 0.97 = 32010) = 32010 < 31600 x 1.03 = 32548 -> 32500
    assert calcular_topes(30000, 31000, 33000, 29000, 33000) == [29000, 31600, 32500], "mirar acotado por el nivel"
    s1, g1, _ = s_de_errores([{"error": 0.20}, {"error": -0.30}])
    assert s1 == 0.12 and g1 == 0.0, "errores de signo mezclado: sesgo 0"
    s2, g2, _ = s_de_errores([{"error": 0.174}, {"error": 0.036}])
    assert abs(s2 - 0.105) < 1e-9 and abs(g2 - 0.105) < 1e-9, "los dos altos: sesgo = media con signo"
    assert s_de_errores([{"error": 0.40}, {"error": 0.30}])[1] == SESGO_MAX and s_de_errores([{"error": 0.01}])[0] == 0.05
    assert s_de_errores([None, {"error": None}])[:2] == (S_DEFECTO, 0.0)
    # prueba hacia atras sin ver el dia: un precio bajisimo el propio dia t no cambia los topes de t
    ta = D(2026, 3, 1)
    sa = _serie_fija(ta - dt.timedelta(days=29), 29, 10000)          # datos hasta el dia anterior
    sb = dict(sa)
    sb[ta] = (5000.0, _obs_prueba(ta.isoformat(), 5000, id="HOY"))
    an = [{"nombre": "AN", "corto": "el AN", "desfase": 364,
           "serie": _serie_fija(ta - UN_DIA - dt.timedelta(days=364 + 29), 70, 20000, {40: 16000})}]
    tpa, tpb = topes_retro(sa, an, ta, [], 0.09, 0.0, 1000)[0], topes_retro(sb, an, ta, [], 0.09, 0.0, 1000)[0]
    # pronostico del dia anterior: un anio, central = 10000 x 16000 / 20000 = 8000 = bajo; alto = 8720; minimo 120 dias 10000
    # ganga = min(8160, 10000) -> 8100; comprar = 8160 < 8343 -> 8300; mirar = min(8894, 9700) -> 8800
    assert tpa == tpb == [8100, 8300, 8800], (tpa, tpb)
    assert topes_retro(sa, [], ta, [], 0.09, 0.0, 1000)[0] is None, "sin anio parecido no hay topes calculados"
    assert topes_de(pronostico(sb, [], ta, [], 0.09, 0.0))[0] == 5000, "viendo el dia, el minimo de 120 dias seria el de hoy"
    # topes activos (fase 2c)
    man = [22000, 24000, 26000]
    assert topes_activos(man, [24100, 26500, 29000], 2, 53499) == ([22000, 24000, 29000], "a mano; mirar ampliado por pronostico", None)
    assert topes_activos(man, [24100, 26500, 29000], 2, 53499, "proporcion de ultra-512")[1] == "a mano; mirar ampliado por proporcion de ultra-512"
    tp1 = topes_activos(man, [24100, 26500, 29000], 1, 53499)
    assert tp1[0] == man and tp1[1] == "fracciones" and tp1[2].startswith("un solo año parecido")
    assert topes_activos(man, None, 0, 53499)[2] == "sin año parecido ni historia suficiente"
    assert topes_activos(man, [20000, 22000, 25000], 2, 53499)[0] == man, "mirar calculado menor: se queda el de a mano"
    assert topes_activos(man, [30000, 40000, 60000], 2, 53499)[0] == man, "mirar calculado arriba de la lista: a mano"
    for malo in ([1, 2], ["1", "2", "99999"], [1, 2, None], (1, 2, 3, 4), "abc", [True, 2, 3]):   # C10: mal formado = a mano
        tm = topes_activos(man, malo, 2, 53499)
        assert tm[0] == man and tm[1] == "fracciones" and tm[2].startswith("topes calculados mal formados"), (malo, tm)
    # tope de cordura: el camino da 150 en todo el evento, mas que el nivel de hoy (100)
    a5 = _serie_fija(t - dt.timedelta(days=364 + 29), 70, 200, dict((k, 300) for k in range(30, 70)))
    p5 = pronostico(este, [{"nombre": "A5", "corto": "el A5", "desfase": 364, "serie": a5}], t, ev)
    e5 = p5["pisos_esperados"][0]
    assert e5["piso"] == 100 and e5["rango"] == [100, 100] and e5["base"] == "no se espera rebaja respecto de lo normal de hoy", e5
    vs = {"p|a": {"lista": 100000, "topes": [20000, 21000, 22000]}, "p|b": {"lista": 120000, "topes": [20000, 21000, 22000]},
          "p|c": {"lista": 100000, "topes": [20000, 21000, 22000]}}
    _desempatar(vs, [{"id": "p", "variantes": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}])
    assert vs["p|b"]["topes"] == [20100, 21100, 22100] and vs["p|a"]["topes"] == vs["p|c"]["topes"] == [20000, 21000, 22000]


def selftest():
    hoy = dt.date(2026, 11, 25)
    prods = PRODS_PRUEBA["productos"]
    # 1. topes de fracciones iguales a los de vigia.py
    esperado = {"fold8|ultra-512": [22000, 24000, 26000], "fold8|normal-512": [19900, 21800, 23600],
                "ps5|digital": [7500, 9000, 10800]}
    for p in prods:
        for v in p["variantes"]:
            clave = "%s|%s" % (p["id"], v["id"])
            if clave in esperado:
                assert topes_vigia(p, v)[0] == esperado[clave], (clave, topes_vigia(p, v))
    try:  # contra vigia.topes() con el productos.json real (solo lectura)
        import importlib.util
        spec = importlib.util.spec_from_file_location("_vigia_selftest", os.path.join(AQUI, "vigia.py"))
        vg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vg)
        if isinstance(getattr(vg, "PROSPECTIVA", None), dict):
            vg.PROSPECTIVA.clear()   # comparar contra el camino de fracciones, no contra un prospectiva.json ya escrito
        reales = leer_productos(os.path.join(AQUI, CONFIG))
    except Exception as e:
        vg, reales = None, []
        _aviso("selftest: sin comparar contra vigia.py (%s)" % e)
    for p in reales:
        for v in p["variantes"]:
            assert list(vg.topes(p, v)) == topes_vigia(p, v)[0], (p["id"], v["id"])
    _selftest_fase2()
    with tempfile.TemporaryDirectory() as tmp:
        hist = os.path.join(tmp, HISTORIA)
        os.mkdir(hist)
        _escribir(os.path.join(tmp, CONFIG), json.dumps(PRODS_PRUEBA))
        enc = ",".join(COLUMNAS) + ",revision\n"
        _escribir(os.path.join(hist, "prueba.csv"), enc +
                  "2026-11-20,fold8,ultra-512,Samsung,30000,53499,,,ninguno,Samsung,si,propio,u,P1,,\n"
                  "2026-11-21,fold8,ultra-512,Liverpool,25000,,10% con Banorte,Banorte,ajeno,Liverpool,si,propio,u,P2,,\n"
                  "2026-11-21,fold8,ultra-512,Samsung,20000,,con canje de tu equipo,,ninguno,Samsung,si,propio,u,P3,,\n"
                  "2026-11-22,fold8,ultra-512,Samsung,21000,,,,ninguno,sin dato,si,foro,u,F4,,dudosa\n"
                  "2026-11-22,fold8,ultra-512,Mercado Libre,19000,,,,ninguno,Importadora,no,propio,u,P5,,\n"
                  "2026-11-22,fold8,ultra-512,Samsung,29000,,,,ninguno,sin dato,sin dato,foro,u,F6,,ok\n"
                  "2026-11-22,fold8,ultra-512,Samsung,29000\n"
                  "2026-11-22,fold8,ultra-512,Samsung,abc,,,,ninguno,sin dato,si,propio,u,P8,,\n"
                  "2026-11-23,fold8,ultra-512,Samsung,9000,,,,ninguno,Samsung,si,propio,u,P9,,\n"
                  "2026-11-23,fold8,ultra-512,Amazon,31000,,\"linea uno\nlinea dos\",,ninguno,Amazon,si,keepa,u,K10,,\n"
                  "2026-11-24,fold8,ultra-512,Samsung,28000,,,,ninguno,sin dato,si,foro,u,F11,,\n"
                  "2026-11-24,fold8,normal-512,Samsung,26495,,,,ninguno,Samsung,si,propio,u,R12,el documento dice no reverificado,ok\n")
        _escribir(os.path.join(hist, "calendario.csv"), ",".join(COLUMNAS_CALENDARIO) +
                  "\nBuen Fin 2026,temporada,2026-11-13,2026-11-17,,u,cita\nAnuncio,lanzamiento,2026-07-22,,fold8,u,cita\n")
        _escribir(os.path.join(hist, "vacio.csv"), "")
        _escribir(os.path.join(hist, "fechas.csv"), ",".join(COLUMNAS) +   # arreglo 11: fechas que no son AAAA-MM-DD
                  "\n20261121,fold8,ultra-512,Samsung,29000,,,,ninguno,Samsung,si,propio,u,FX1,\n"
                  "2026-W48-1,fold8,ultra-512,Samsung,28000,,,,ninguno,Samsung,si,propio,u,FX2,\n")
        _escribir(os.path.join(hist, "cortado.csv"), ",".join(COLUMNAS) + "\n2026-11-24,fold8,ultra-512,Samsung,\"300")
        _escribir(os.path.join(tmp, HISTORIAL), ",".join(COLUMNAS_HISTORIAL) + "\n"
                  "2026-11-19 18:57,Samsung,Fold8 Ultra 512 GB,34199.20,Samsung,\"con cupones X\",https://x\n"
                  "2026-11-19 19:25,Liverpool,Fold8 Ultra (capacidad sin dato),55999.00,Liverpool,,https://y\n"
                  "2026-11-24 10:06,Amazon,PS5 con lector de disco,14498.00,Amazon México,,https://z\n"
                  "2026-11-24 10:53,Gameplanet,PS5 Digital (sin lector),10999.99,Gameplanet,,https://w\n")
        # 2. cargador: encabezado ajeno, renglon roto, archivo vacio, archivo cortado
        obs = cargar_historia(hist)
        ids = {o["id"]: o for o in obs}
        assert sorted(ids) == ["F11", "F4", "F6", "K10", "P1", "P2", "P3", "P5", "P9", "R12"], sorted(ids)
        assert ids["K10"]["renglon"] == 11 and ids["F11"]["renglon"] == 13, (ids["K10"]["renglon"], ids["F11"]["renglon"])
        assert ids["P1"]["rastro"] == "historia/prueba.csv:P1" and ids["P1"]["revision"] == ""
        # 3. traduccion de historial_precios.csv
        ov, sin = cargar_historial(os.path.join(tmp, HISTORIAL), prods)
        assert len(ov) == 3 and sin == {"PS5 con lector de disco": 1}, (len(ov), sin)
        o = ov[0]
        assert (o["producto"], o["variante"], o["fecha"], o["precio"]) == ("fold8", "ultra-512", "2026-11-19", 34199.2)
        assert o["id"] == "2026-11-19 18:57|Samsung|ultra-512" and o["fuente"] == "propio"
        assert o["garantia_mx"] == "si" and o["tipo_banco"] == "ninguno" and o["condicion"] == "con cupones X"
        assert ov[1]["variante"] == "ultra" and ov[2]["producto"] == "ps5" and ov[2]["renglon"] == 5
        # 4. reglas de validez
        pisos = dict(PISOS_ANTECESOR, fold8=12000, ps5=6500)
        motivos = {k: clasificar(v, pisos) for k, v in ids.items()}
        assert motivos == {"P1": "valida", "P2": "ajeno", "P3": "canje_o_plan", "F4": "revision_no_ok",
                           "P5": "sin_garantia", "F6": "valida", "P9": "bajo_piso", "K10": "valida",
                           "F11": "sin_revision", "R12": "no_reverificado"}, motivos
        assert clasificar(_obs_prueba("2026-11-20", 30000, condicion="con plan Telcel"), pisos) == "canje_o_plan"
        assert clasificar(_obs_prueba("2026-11-20", 30000, condicion="Trade-in"), pisos) == "canje_o_plan"
        assert clasificar(_obs_prueba("2026-11-20", 30000, tipo_banco="propio"), pisos) == "valida"
        assert clasificar(_obs_prueba("2026-11-20", 13000, producto="fold7", variante="512"), pisos) == "valida"
        assert clasificar(_obs_prueba("2026-11-20", 11000, producto="fold6", variante="512"), pisos) == "bajo_piso"
        # 5. minimo, tipico y percentiles con una serie conocida
        serie = [("2026-11-25", 25000), ("2026-11-25", 26000), ("2026-11-24", 20000), ("2026-11-20", 30000),
                 ("2026-11-15", 15000), ("2026-11-10", 35000), ("2026-11-01", 22500), ("2026-09-01", 12500),
                 ("2026-11-26", 13000)]
        var, det = calcular([_obs_prueba(f, x) for f, x in serie], prods, hoy)
        f = var["fold8|ultra-512"]
        assert f["minimo"]["precio"] == 12500 and f["minimo"]["fecha"] == "2026-09-01", f["minimo"]
        assert f["minimo_30d"]["precio"] == 15000, f["minimo_30d"]
        assert f["tipico"] == {"precio": 23750, "n": 6}, f["tipico"]
        assert f["percentiles"] == {"p10": 14000, "p25": 17500, "p50": 22500, "n": 7}, f["percentiles"]
        assert f["n_datos"]["descartadas"] == {"fecha_posterior": 1}
        var, _ = calcular([_obs_prueba(f2, x) for f2, x in serie[:5]], prods, hoy)
        assert var["fold8|ultra-512"]["tipico"] is None
        # 6. generar con los archivos de prueba; cargar_json lo acepta
        ruta = os.path.join(tmp, SALIDA)
        doc, det, info = generar(tmp, dt.datetime(2026, 11, 25, 12, 0, tzinfo=MX))
        v = doc["variantes"]
        assert doc["vence"] == "2026-12-25" and doc["generado"] == "2026-11-25T12:00:00-06:00"
        u = v["fold8|ultra-512"]
        assert u["minimo"]["precio"] == 29000 and u["minimo"]["rastro"] == "historia/prueba.csv:F6", u["minimo"]
        assert u["ajeno"]["precio"] == 25000 and u["ajeno"]["banco"] == "Banorte"
        assert u["n_datos"]["validas"] == {"propio": 2, "foro": 1, "keepa": 1}
        assert u["n_datos"]["descartadas"] == {"canje_o_plan": 1, "revision_no_ok": 1, "sin_garantia": 1,
                                               "bajo_piso": 1, "sin_revision": 1}
        assert v["fold8|normal-512"]["n_datos"]["descartadas"] == {"no_reverificado": 1}
        assert v["fold8|ultra"]["minimo"] is None and v["fold8|normal-512"]["minimo"] is None
        assert v["ps5|digital"]["minimo"]["precio"] == 10999.99
        for clave, t3 in esperado.items():
            assert v[clave]["topes_manual"] == t3
        # sin anios parecidos en los datos de prueba: no hay piso ni topes calculados; activos = a mano;
        # sin confirmar = 0.80 x minimo de 120 dias (29,000) = 23,200, acotado a 0.9 x ganga (22,000) = 19,800 (arreglo 2);
        # s = 0.09 y sesgo 0 (sin casos A y B)
        assert u["piso"] is None and u["topes_calculados"] is None and u["s"] == S_DEFECTO and u["sesgo"] == 0, u["piso"]
        assert u["topes"] == esperado["fold8|ultra-512"] and u["topes_origen"] == "fracciones", (u["topes"], u["topes_origen"])
        assert u["topes_nota"] == "sin año parecido ni historia suficiente"
        assert v["fold8|ultra"]["topes_origen"] == "fracciones" and v["fold8|ultra"]["topes"] == esperado["fold8|ultra-512"]
        assert v["fold8|normal-512"]["topes_origen"] == "fracciones" and v["fold8|normal-512"]["topes"] == esperado["fold8|normal-512"]
        assert v["ps5|digital"]["topes"] == esperado["ps5|digital"] and v["ps5|digital"]["topes_calculados"] is None
        assert u["sin_confirmar_bajo"] == 19800, u["sin_confirmar_bajo"]
        for clave_sc, f_sc in v.items():
            assert f_sc["sin_confirmar_bajo"] is None or f_sc["sin_confirmar_bajo"] <= _floor100(0.9 * f_sc["topes"][0]), clave_sc
        # arreglo 5: el minimo dice de donde sale (F6 es de foro con garantia "sin dato")
        assert u["minimo"]["garantia"] == "sin dato" and u["minimo"]["fuente"] == "foro", u["minimo"]
        r5 = razon(30000, u, hoy)   # arriba de todos los topes, sin pronostico: solo el minimo, con su fuente
        assert r5.startswith("Lo más barato visto: $29,000, el 22 nov en Samsung, según el foro de ofertas.") \
            and "con garantía" not in r5, r5
        r5 = razon(15000, u, hoy)
        assert r5.startswith("Sin confirmar: está muy por debajo de todo lo visto en cuatro meses.") and "con garantía" not in r5, r5
        assert info["origen_calendario"] == "historia/calendario.csv" and info["eventos"] == 2 and info["temporadas"] == 1
        with open(ruta, "rb") as fb:
            assert b"\r\n" not in fb.read()
        assert sorted(cargar_json(ruta, hoy)) == sorted(v), sorted(cargar_json(ruta, hoy))
        assert cargar_json(ruta, "2026-12-25") and cargar_json(ruta, "2026-12-26") == {}
        # topes_fijos ganan siempre
        pf = json.loads(json.dumps(PRODS_PRUEBA))
        pf["productos"][0]["topes_fijos"] = {"ultra-512": [20000, 22000, 24000]}
        otra = os.path.join(tmp, "fijos")
        os.mkdir(otra)
        os.mkdir(os.path.join(otra, HISTORIA))
        _escribir(os.path.join(otra, CONFIG), json.dumps(pf))
        _escribir(os.path.join(otra, HISTORIA, "prueba.csv"), open(os.path.join(hist, "prueba.csv"), encoding="utf-8").read())
        dfj, _, _ = construir(otra, dt.datetime(2026, 11, 25, 12, 0, tzinfo=MX))
        assert dfj["variantes"]["fold8|ultra-512"]["topes"] == [20000, 22000, 24000]
        assert dfj["variantes"]["fold8|ultra-512"]["topes_origen"] == "topes_fijos"
        # arreglo 3: con topes_fijos, "sin confirmar" tambien queda bajo 0.9 x la ganga fija (23,200 -> 18,000)
        assert dfj["variantes"]["fold8|ultra-512"]["sin_confirmar_bajo"] == 18000, dfj["variantes"]["fold8|ultra-512"]["sin_confirmar_bajo"]
        # arreglo 4: productos.json con topes_fijos nuevos y un prospectiva.json de antes: ese producto vuelve a fracciones
        escribir_json(doc, os.path.join(otra, SALIDA))
        assert not any(k.startswith("fold8|") for k in cargar_json(os.path.join(otra, SALIDA), hoy))
        escribir_json(dfj, os.path.join(otra, SALIDA))
        assert "fold8|ultra-512" in cargar_json(os.path.join(otra, SALIDA), hoy)
        # 7. cargar_json rechaza lo malo
        def prueba(nombre, cambio):
            d2 = json.loads(json.dumps(doc))
            cambio(d2)
            r = os.path.join(tmp, nombre)
            escribir_json(d2, r)
            return cargar_json(r, hoy)
        _escribir(os.path.join(tmp, "roto.json"), "{no es json")
        assert cargar_json(os.path.join(tmp, "roto.json"), hoy) == {}
        assert cargar_json(os.path.join(tmp, "no_existe.json"), hoy) == {}
        assert prueba("v2.json", lambda d2: d2.update(version=2)) == {}
        assert prueba("vtrue.json", lambda d2: d2.update(version=True)) == {}, "arreglo 9"
        for malo in ("6000", float("inf"), 7500, 9000, True, 6000.0, {"precio": 6000}, [6000]):   # arreglo 1
            assert "ps5|digital" not in prueba("sc.json", lambda d2: d2["variantes"]["ps5|digital"].update(sin_confirmar_bajo=malo)), malo
        assert "ps5|digital" in prueba("sc_bien.json", lambda d2: d2["variantes"]["ps5|digital"].update(sin_confirmar_bajo=6000))
        # arreglo 4: fracciones o lista cambiadas en productos.json desde que se genero
        assert "ps5|digital" not in prueba("manual.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes_manual=[6900, 8100, 10000]))
        r = prueba("desordenada.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[9000, 7500, 10800]))
        assert "ps5|digital" not in r and "fold8|ultra-512" in r
        r = prueba("repetida.json", lambda d2: d2["variantes"]["fold8|normal-512"].update(topes=list(u["topes"])))
        assert not any(k.startswith("fold8|") for k in r) and "ps5|digital" in r, sorted(r)
        r = prueba("misma_lista.json", lambda d2: d2["variantes"]["fold8|ultra"].update(topes=list(u["topes"])))
        assert "fold8|ultra" in r
        assert "ps5|digital" not in prueba("alto.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[7500, 9000, 13000]))
        assert "ps5|digital" not in prueba("bajo.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[6000, 9000, 10800]))
        assert "ps5|digital" not in prueba("float.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[7500.5, 9000, 10800]))
        assert "ps5|digital" not in prueba("dos.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[7500, 9000]))
        r = prueba("extra.json", lambda d2: d2["variantes"].update({"ps5|pro": {"topes": [7000, 8000, 9000]}}))
        assert not any(k.startswith("ps5|") for k in r)
        assert "ps5|digital" not in prueba("calc_malo.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes_calculados=[1, 2]))
        assert "ps5|digital" in prueba("calc_bien.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes_calculados=[7000, 8000, 9000]))
        sincfg = os.path.join(tmp, "sin_config")
        os.mkdir(sincfg)
        escribir_json(doc, os.path.join(sincfg, SALIDA))
        assert cargar_json(os.path.join(sincfg, SALIDA), hoy) == {}
        # 8. --recalcular: historia rota deja el archivo intacto; salto de mas de 15 % no reemplaza; lo normal si
        with open(ruta, "rb") as fb:
            antes = fb.read()
        g = globals()
        original = g["cargar_historia"]

        def rota(carpeta):
            raise IOError("historia rota de prueba")
        g["cargar_historia"] = rota
        try:
            assert recalcular(tmp, dt.datetime(2026, 11, 25, 13, 0, tzinfo=MX)) == 0
        finally:
            g["cargar_historia"] = original
        with open(ruta, "rb") as fb:
            assert fb.read() == antes, "historia rota: el archivo anterior debe quedar intacto"
        d2 = json.loads(antes.decode("utf-8"))
        d2["variantes"]["ps5|digital"]["topes"] = [7500, 9000, 10800]
        d2["variantes"]["fold8|ultra-512"]["topes"] = [15000, 16000, 17000]
        escribir_json(d2, ruta)
        with open(ruta, "rb") as fb:
            antes = fb.read()
        assert recalcular(tmp, dt.datetime(2026, 11, 25, 13, 0, tzinfo=MX)) == 0
        with open(ruta, "rb") as fb:
            assert fb.read() == antes, "un salto de mas de 15 % no reemplaza"
        escribir_json(doc, ruta)
        assert recalcular(tmp, dt.datetime(2026, 11, 25, 13, 0, tzinfo=MX)) == 0
        with open(ruta, encoding="utf-8") as fb:
            assert json.load(fb)["generado"] == "2026-11-25T13:00:00-06:00", "sin saltos si reemplaza"
        # el archivo anterior tenia dos anios parecidos y mirar ampliado a 31,000 (16 % arriba de 26,000); ahora no hay
        # anio parecido: el mirar vuelve al de a mano aunque baje mas de 15 %, y se reemplaza
        d3 = json.loads(json.dumps(doc))
        d3["variantes"]["fold8|ultra-512"]["topes"] = [22000, 24000, 31000]
        d3["variantes"]["fold8|ultra-512"]["piso"] = {"n_analogos": 2}
        escribir_json(d3, ruta)
        assert recalcular(tmp, dt.datetime(2026, 11, 25, 14, 0, tzinfo=MX)) == 0
        with open(ruta, encoding="utf-8") as fb:
            d4j = json.load(fb)
        assert d4j["generado"] == "2026-11-25T14:00:00-06:00" and d4j["variantes"]["fold8|ultra-512"]["topes"] == [22000, 24000, 26000]
        # arreglo 8: topes anteriores escritos como texto no bloquean el recalculo para siempre
        d4j["variantes"]["ps5|digital"]["topes"] = ["7500", "9000", "10800"]
        escribir_json(d4j, ruta)
        assert recalcular(tmp, dt.datetime(2026, 11, 25, 15, 0, tzinfo=MX)) == 0
        with open(ruta, encoding="utf-8") as fb:
            assert json.load(fb)["generado"] == "2026-11-25T15:00:00-06:00", "topes anteriores como texto"
        # pendiente 1 del 26 nov: --si-viejo 20 no recalcula un archivo de menos de 20 h; si pasa de 20 h, si
        assert recalcular(tmp, dt.datetime(2026, 11, 26, 10, 59, tzinfo=MX), si_viejo=20) == 0
        with open(ruta, encoding="utf-8") as fb:
            assert json.load(fb)["generado"] == "2026-11-25T15:00:00-06:00", "--si-viejo: 19.98 h no recalcula"
        assert recalcular(tmp, dt.datetime(2026, 11, 26, 11, 1, tzinfo=MX), si_viejo=20) == 0
        with open(ruta, encoding="utf-8") as fb:
            assert json.load(fb)["generado"] == "2026-11-26T11:01:00-06:00", "--si-viejo: 20.02 h si recalcula"
        # retroprueba: si la escritura falla, no queda el temporal en historia/
        original_md = g["_retro_md"]
        g["_retro_md"] = lambda *a: None   # f.write(None) lanza TypeError
        try:
            try:
                retroprueba(tmp, hoy=hoy)
                raise AssertionError("retroprueba debia fallar al escribir")
            except TypeError:
                pass
        finally:
            g["_retro_md"] = original_md
        assert not glob.glob(os.path.join(hist, ".retro.*")), glob.glob(os.path.join(hist, ".retro.*"))
    # 9. razon (A12): veredicto primero, con la etiqueta que llevara el correo; sin jerga
    def nivel_vigia(precio, dd):   # copia de vigia.nivel() (vigia.py), con los topes del archivo
        g, c, t = dd["topes"]
        bajo = dd.get("sin_confirmar_bajo")
        if bajo and precio < bajo:
            return "SIN CONFIRMAR"
        return "GANGA" if precio <= g else "COMPRAR" if precio <= c else "MIRAR" if precio <= t else None
    df = {"topes": [22000, 24000, 29000], "sin_confirmar_bajo": 15700, "analogo": "modelo_anterior",
          "minimo": {"precio": 23464, "fecha": "2026-10-13", "tienda": "Samsung", "fuente": "foro", "garantia": "sin dato"},
          "minimo_ciclo": {"precio": 23464, "fecha": "2026-10-13", "tienda": "Samsung", "fuente": "foro", "garantia": "sin dato"},
          "tipico": {"precio": 35004.5, "n": 16},
          "piso": {"precio": 24059, "bajo": 19680, "rango": [19680, 28439], "n_analogos": 2, "tipo": "analogo",
                   "horizonte": "2027-01-31"},
          "analogos": [{"usado": True, "minimos": {"horizonte": 19680}}, {"usado": True, "minimos": {"horizonte": 28439}}],
          "pisos_esperados": [{"evento": "Black Friday y Cyber Monday 2026", "inicio": "2026-11-27", "fin": "2026-11-30", "piso": 30461},
                              {"evento": "Navidad 2026", "inicio": "2026-12-16", "fin": "2027-01-06", "piso": 27620},
                              {"evento": "Cuesta de enero 2027", "inicio": "2027-01-07", "fin": "2027-01-31", "piso": 31181}]}
    # ganga: contra el minimo y contra lo esperado; "queda debajo" porque 21,500 < 24,059
    r = razon(21500, df, hoy)
    assert r == ("Ganga: $21,500 es lo más barato visto en México desde que salió (antes, $23,464 el 13 oct en Samsung, "
                 "según el foro de ofertas) y queda debajo de lo más bajo que se espera de aquí al 31 ene (unos $24,059). "
                 "Compra si quien vende da garantía en México. En el último mes costaba unos $35,000."), r
    # comprar: "pasó k de cada n años"
    r = razon(23999, df, hoy)
    assert r.startswith("Buen precio para comprar: está $535 arriba de lo más barato visto ($23,464, el 13 oct en Samsung, "
                        "según el foro de ofertas) y queda debajo de lo más bajo que se espera de aquí al 31 ene (unos $24,059). "
                        "Puede bajar más (pasó 1 de cada 2 años, hasta unos $19,680), pero no es seguro."), r
    r = razon(24100, dict(df, topes=[22000, 24500, 29000]), hoy)   # 24,100: arriba de 24,059 pero dentro de 2 %
    assert "y está en lo más bajo que se espera de aquí al 31 ene (unos $24,059)." in r, r
    r = razon(25500, dict(df, topes=[22000, 26000, 29000]), hoy)   # arriba de 24,059 x 1.02
    assert "y lo más bajo que se espera de aquí al 31 ene ronda $24,059." in r, r
    r = razon(23999, dict(df, analogos=[{"usado": True, "minimos": {"horizonte": 26000}},
                                        {"usado": True, "minimos": {"horizonte": 27600}}]), hoy)
    assert "En los años comparables no bajó más." in r, r
    # mirar y sin etiqueta: conviene esperar
    r = razon(28999, df, hoy)
    assert r.startswith("Conviene esperar: hacia Navidad 2026 se espera alrededor de $27,620 (así bajó el modelo anterior en "
                        "estas fechas, llevado al precio de hoy). Lo más barato visto: $23,464, el 13 oct en Samsung, según el "
                        "foro de ofertas."), r
    assert razon(34199, df, hoy).startswith("Conviene esperar: hacia Black Friday y Cyber Monday 2026 se espera alrededor de $30,461")
    r = razon(28999, dict(df, pisos_esperados=[]), hoy)
    assert r.startswith("Conviene esperar: lo más bajo que se espera de aquí al 31 ene ronda $24,059."), r
    r = razon(23000, dict(df, topes=[19900, 21800, 28100]), hoy)   # mirar, pero ya debajo de lo esperado
    # C10: "conviene esperar" nunca va junto a una cifra esperada mayor que el precio
    assert r.startswith("Conviene esperar: todavía no llega a tu precio de compra ($21,800). Lo más barato visto:") \
        and "se espera" not in r and "24,059" not in r, r
    # C10: evento ya en curso -> "de aqui al <fin>", no "hacia"
    r = razon(28999, df, dt.date(2026, 12, 20))
    assert r.startswith("Conviene esperar: de aquí al 6 ene (Navidad 2026) se espera alrededor de $27,620"), r
    # sin confirmar: nunca se recorta; no hay veredicto ni "lo normal"
    r = razon(14999, df, hoy)
    assert r == ("Sin confirmar: está muy por debajo de todo lo visto en cuatro meses. Antes de creerlo, abre la página del "
                 "producto en la tienda y revisa quién vende. Si es real, sería lo más barato visto (antes, $23,464)."), r
    # variante calculada a partir de otra: un solo parentesis, pegado a la cifra esperada
    dh = dict(df, piso=dict(df["piso"], tipo="proporcion"), minimo=None, minimo_ciclo=None, tipico=None)
    r = razon(28500, dh, hoy)
    assert r == ("Conviene esperar: hacia Navidad 2026 se espera alrededor de $27,620 (así bajó el modelo anterior en estas "
                 "fechas, llevado al precio de hoy; calculado a partir del de 512 GB)."), r
    assert "(unos $24,059, calculado a partir del de 512 GB)" in razon(21500, dh, hoy)
    # PS5: minimo de toda la historia de hace mas de 180 dias -> el de los ultimos cuatro meses; "la misma consola"
    dp = {"topes": [7500, 9000, 10800], "sin_confirmar_bajo": 6100, "analogo": "misma_consola",
          "minimo": {"precio": 6675, "fecha": "2025-12-01", "tienda": "Walmart", "fuente": "foro", "garantia": "sin dato"},
          "minimo_ciclo": {"precio": 7717, "fecha": "2026-11-13", "tienda": "Walmart", "fuente": "foro", "garantia": "sin dato"},
          "tipico": {"precio": 11870, "n": 5},
          "piso": {"precio": 10434, "bajo": 9073, "rango": [9073, 11687], "n_analogos": 1, "tipo": "analogo", "horizonte": "2027-01-31"},
          "analogos": [{"usado": True, "minimos": {"horizonte": 10434}}, {"usado": False, "motivo": "x"}],
          "pisos_esperados": [{"evento": "Black Friday y Cyber Monday 2026", "inicio": "2026-11-27", "fin": "2026-11-30", "piso": 10434}]}
    r = razon(7400, dp, hoy)
    assert r.startswith("Ganga: $7,400 es lo más barato de los últimos cuatro meses (antes, $7,717 el 13 nov en Walmart, según "
                        "el foro de ofertas) y queda debajo de lo más bajo que se espera de aquí al 31 ene (unos $10,434). "
                        "Compra si quien vende da garantía en México.") and "6,675" not in r, r
    assert "Es orientación: solo hay un año para comparar." in r, r
    r = razon(8999, dp, hoy)
    assert r.startswith("Buen precio para comprar: está $1,282 arriba de lo más barato de los últimos cuatro meses ($7,717") \
        and "El año pasado no bajó más." in r, r
    assert razon(10740, dp, hoy).startswith("Conviene esperar: hacia Black Friday y Cyber Monday 2026 se espera alrededor de "
                                            "$10,434 (así bajó la misma consola en estas fechas, llevado al precio de hoy).")
    # C10: Black Friday ya empezado (28 nov): "de aqui al 30 nov", no "hacia"
    r = razon(10740, dp, dt.date(2026, 11, 28))
    assert r.startswith("Conviene esperar: de aquí al 30 nov (Black Friday y Cyber Monday 2026) se espera alrededor de $10,434"), r
    # C10: se cita lo mas bajo entre el horizonte (11,669) y los eventos (Black Friday, 10,434), como el prospectiva.json real
    dp2 = dict(dp, piso=dict(dp["piso"], precio=11669, n_analogos=2),
               analogos=[{"usado": True, "minimos": {"horizonte": 10434}}, {"usado": True, "minimos": {"horizonte": 12904}}])
    for prc in (8000, 8999):
        r = razon(prc, dp2, hoy)
        assert "(unos $10,434)" in r and "11,669" not in r, (prc, r)
    for prc in range(9001, 10801, 50):   # mirar: ninguna cifra esperada mayor que el precio junto a "conviene esperar"
        r = razon(prc, dp2, hoy)
        assert r.startswith("Conviene esperar:") and "11,669" not in r and "aunque" not in r, (prc, r)
    assert razon(5999, dp, hoy).endswith("Si es real, sería lo más barato de los últimos cuatro meses (antes, $7,717).")
    dps = dict(dp, piso=None, analogos=[], pisos_esperados=[])   # sin pronostico: solo minimo y lo normal
    r = razon(7400, dps, hoy)
    assert r.startswith("Ganga: $7,400 es lo más barato de los últimos cuatro meses") and "se espera" not in r, r
    assert razon(8999, dps, hoy).startswith("Buen precio para comprar: está $1,282 arriba")
    r = razon(10000, dps, hoy)
    assert r.startswith("Lo más barato de los últimos cuatro meses: $7,717, el 13 nov en Walmart, según el foro de ofertas."), r
    r = razon(10740, dict(dp, minimo_ciclo=None), hoy)   # minimo viejo y sin el de cuatro meses: se omite
    assert "6,675" not in r and "Lo más barato" not in r, r
    # iguala; fuente propia con garantia: no se dice de donde sale; prensa
    r = razon(23500, dict(df, topes=[24000, 25000, 29000]), hoy)
    assert r.startswith("Ganga: iguala lo más barato visto ($23,464, el 13 oct en Samsung, según el foro de ofertas)"), r
    propio = dict(df, minimo={"precio": 23464, "fecha": "2026-10-13", "tienda": "Amazon", "fuente": "keepa", "garantia": "si"})
    assert "según" not in razon(28999, propio, hoy)
    assert "según la prensa" in razon(28999, dict(propio, minimo=dict(propio["minimo"], fuente="prensa", garantia="sin dato")), hoy)
    # barrido: largo, palabras prohibidas, "está en" solo si no queda debajo, un solo parentesis, encabezado = etiqueta
    largo = dict(df, minimo=dict(df["minimo"], tienda="T" * 200), sin_confirmar_bajo=99999,
                 pisos_esperados=[{"evento": "E" * 300, "inicio": "2026-11-27", "fin": "2026-11-30", "piso": 2}])
    fijos = dict(df, topes=[14000, 15000, 16000], sin_confirmar_bajo=12600)
    for dd in (df, dh, dp, dps, largo, fijos, dict(df, minimo=dict(df["minimo"], tienda="T" * 120))):
        tiene_pron = bool(_esperado(dd, hoy))
        for prc in range(5000, 40001, 125):
            r = razon(prc, dd, hoy)
            lvl = nivel_vigia(prc, dd)
            assert len(r) <= MAX_RAZON and not PROHIBIDAS.search(r) and ") (" not in r, (prc, r)
            if lvl == "SIN CONFIRMAR":
                assert r.startswith(ENCABEZADO["sin confirmar"] + " está muy por debajo de todo lo visto"), (prc, r)
            elif lvl in ("GANGA", "COMPRAR"):
                assert r.startswith(ENCABEZADO[lvl.lower()]), (prc, lvl, r)
                if lvl == "GANGA":
                    assert "Compra si quien vende da garantía en México." in r, (prc, r)
            elif tiene_pron:
                assert r.startswith("Conviene esperar:"), (prc, lvl, r)
            else:
                assert not r.startswith(("Ganga", "Buen precio", "Conviene", "Sin confirmar")), (prc, lvl, r)
            if tiene_pron and prc < _esperado(dd, hoy)["central"]:
                assert "está en lo más bajo" not in r, (prc, r)
    assert razon(30000, {}, hoy) == "" and razon(30000, None, hoy) == ""
    # condicion de acceso (fase 2d): va a la serie aparte, no fija referencias
    pz = dict(PISOS_ANTECESOR, fold8=12000, ps5=6500)
    for k, txt in (("condicion", "cupón CART5_EPP"), ("tienda", "Samsung tienda de estudiantes"), ("nota", "solo Samsung Business"),
                   ("condicion", "para nueva cuenta"), ("condicion", "en tu primera compra"), ("nota", "programa de empleados"),
                   ("condicion", "precio Meli+"), ("condicion", "con Meli Plus")):
        assert clasificar(_obs_prueba("2026-11-20", 30000, **{k: txt}), pz) == "condicion_de_acceso", (k, txt)
    assert clasificar(_obs_prueba("2026-11-20", 30000, condicion="cupón FOLD8U_35CLUB"), pz) == "valida"
    # C10: las letras dentro de otra palabra no son condicion de acceso (titulo del hilo en la direccion)
    for slug in ("fold8-para-desempleados", "renueva-cuenta-telcel", "sobrenueva-cuenta", "superestudiante-gamer",
                 "estudiantesco", "businessweek-oferta", "empleadora-fold8", "cameli-plus-ps5"):
        o = {"url": "https://www.promodescuentos.com/ofertas/%s-1111739" % slug, "condicion": "", "tienda": "Samsung", "nota": ""}
        assert condicion_de_acceso(o) is None, (slug, condicion_de_acceso(o))
    for slug in ("precio-para-estudiantes", "samsung-epp-fold8", "nueva-cuenta-mas-paypal", "programa-empleados", "meli-plus"):
        o = {"url": "https://www.promodescuentos.com/ofertas/%s-1111739" % slug, "condicion": "", "tienda": "Samsung", "nota": ""}
        assert condicion_de_acceso(o), slug
    va, _ = calcular([_obs_prueba("2026-11-20", 30000), _obs_prueba("2026-11-21", 25000, condicion="tienda de estudiantes"),
                      _obs_prueba("2026-11-22", 27000, banco="Banorte", tipo_banco="ajeno")], PRODS_PRUEBA["productos"], hoy)
    fu = va["fold8|ultra-512"]
    assert fu["minimo"]["precio"] == 30000 and fu["ajeno"]["precio"] == 25000, (fu["minimo"], fu["ajeno"])
    assert fu["ajeno"]["motivo"] == "condicion de acceso" and fu["ajeno"]["condicion"] == "estudiantes", fu["ajeno"]
    assert fu["n_datos"]["condicion_de_acceso"] == 1 and fu["n_datos"]["ajeno"] == 1, fu["n_datos"]
    # datos incompletos o raros: nunca excepcion ni mas de 320
    raros = [None, {}, [], "x", {"minimo": None}, {"minimo": {"precio": None}}, {"minimo": "x"},
             {"minimo": {"precio": "abc", "fecha": "xx", "tienda": 5}}, {"tipico": {"precio": 0}},
             {"tipico": None, "pisos_esperados": [None, 3, {"piso": "x"}, {"evento": 4, "fin": None, "piso": 1}]},
             {"sin_confirmar_bajo": "x"}, {"sin_confirmar_bajo": {"precio": 99999}},
             {"minimo": {"precio": 30000, "fecha": None, "tienda": None}},
             {"minimo": {"precio": 30000, "tienda": "T" * 500}, "tipico": {"precio": 1}, "sin_confirmar_bajo": 99999,
              "pisos_esperados": [{"evento": "E" * 400, "fin": "2026-12-01", "piso": 2}]},
             {"minimo": {"precio": "T" * 500}},
             {"topes": [1, 2], "topes_origen": "prospectiva", "piso": {"precio": 5}},
             {"topes": ["a", None, 3], "topes_origen": "prospectiva", "piso": None},
             {"topes": [100, 200, 300], "topes_origen": 5, "piso": {"precio": "x", "horizonte": 7}},
             {"topes": [100, 200, 300], "piso": {"precio": 50, "horizonte": None, "tipo": "estimacion", "fecha": 3, "n_analogos": 1}},
             {"topes": [100, 200, 300], "piso": {"precio": 50, "n_analogos": 2}, "analogos": [{"usado": True, "minimos": 5}, 7]},
             {"topes": [20000, 22000, 24000], "minimo_ciclo": {"precio": 21000, "fecha": "malo", "tienda": ["x"], "fuente": 3}},
             dict(df, analogos=None, pisos_esperados="x", tipico=[1]), dict(dp, minimo={"precio": 6675}, minimo_ciclo=None)]
    for dd in raros:
        for prc in (None, "abc", -5, 0, 150, 25000, float("nan"), float("inf"), True, "25000", [1]):
            for hh in (hoy, None, "2026-11-25", "malo"):
                r = razon(prc, dd, hh)
                assert isinstance(r, str) and len(r) <= MAX_RAZON and not PROHIBIDAS.search(r), (prc, dd, r)
    return True


# ---------------------------------------------------------------- linea de comandos

def _fmt(x):
    return "-" if x is None else "{:,.0f}".format(float(x))


def _imprimir_tabla(doc, info):
    print("prospectiva.json generado %s, vence %s" % (doc["generado"], doc["vence"]))
    print("observaciones leidas: %d; calendario: %d eventos, %d temporadas (%s); listas: %d (%s)" % (
        info["observaciones"], info["eventos"], info["temporadas"], info["origen_calendario"], info["listas"], info["origen_listas"]))
    print("s = %.4f, sesgo = %.4f (%s)" % (info["s"], info["sesgo"], info["s_motivo"]))
    if info["sin_traducir"]:
        print("renglones del vigia sin variante en productos.json: %s" % ", ".join(
            "%s (%d)" % kv for kv in sorted(info["sin_traducir"].items())))
    print("%-17s %-19s %-19s %-19s %-45s %-12s %-35s %s" % (
        "variante", "activos", "calculados", "a mano", "origen", "nivel (n)", "piso central [bajo-alto] tipo", "anios parecidos"))
    for clave, f in sorted(doc["variantes"].items()):
        nv = f.get("nivel")
        ntxt = "%s (%d)" % (_fmt(nv["precio"]), nv["n"]) if nv else "-"
        p = f.get("piso")
        ptxt = "%s [%s-%s] %s" % (_fmt(p["precio"]), _fmt(p["rango"][0]), _fmt(p["rango"][1]), p["tipo"]) if p else "-"
        n = p.get("n_analogos", 0) if p else 0
        usados = ", ".join(a["nombre"] for a in f.get("analogos", []) if a.get("usado"))
        calc = f.get("topes_calculados")
        print("%-17s %-19s %-19s %-19s %-45s %-12s %-35s %s" % (
            clave, "/".join(str(x) for x in f["topes"]), "/".join(str(x) for x in calc) if calc else "-",
            "/".join(str(x) for x in f["topes_manual"]), f["topes_origen"], ntxt, ptxt,
            "%d%s" % (n, " (" + usados + ")" if usados else "")))
        for k in ("topes_nota", "topes_motivo"):
            if f.get(k):
                print("    %s: %s" % (k, f[k]))
        for a in f.get("analogos", []):
            if not a["usado"]:
                print("    %s sin usar: %s" % (a["nombre"], a["motivo"]))
        for e in f.get("pisos_esperados", []):
            print("    %s: %s [%s-%s]; %s" % (e["evento"], _fmt(e["piso"]), _fmt(e["rango"][0]), _fmt(e["rango"][1]), e["base"]))
        if f.get("sin_confirmar_bajo"):
            print("    sin confirmar por debajo de %s" % _fmt(f["sin_confirmar_bajo"]))
        for b in f.get("baja_mas", []):
            print("    a %s (%s): %s" % (_fmt(b["precio"]), b["tope"], "; ".join(x["frase"] for x in b["plazos"])))


def _donde(o):
    return "%s renglon %d (id %s)" % (o["archivo"], o["renglon"], o["id"])


def _rastro(prod, var):
    doc, detalle, info = construir(AQUI)
    variantes = doc["variantes"]
    clave = "%s|%s" % (prod, var)
    if clave not in variantes:
        print("no existe la variante %s en productos.json" % clave)
        return 1
    f, d = variantes[clave], detalle[clave]
    ctx = info["ctx"]
    p = ctx.por_id[prod]
    v = next(x for x in p["variantes"] if x["id"] == var)
    por_rastro = {o["rastro"]: o for o in ctx.obs}
    print("%s (%s), calculado al %s" % (clave, f["nombre"], d["hasta"]))
    print("topes a mano %s: productos.json, lista %s x fracciones %s, redondeo a centenas" % (f["topes_manual"], v["lista"], p["fracciones"]))
    print("topes activos %s: %s%s" % (f["topes"], f["topes_origen"],
                                      "; " + (f.get("topes_nota") or f.get("topes_motivo")) if (f.get("topes_nota") or f.get("topes_motivo")) else ""))
    print("topes calculados (informativos): %s" % (f.get("topes_calculados") or "null"))
    if f.get("topes_calculados"):
        print("  ganga = el menor entre bajo x 1.02 y minimo_ciclo; comprar = central x 1.02 (al menos ganga x 1.03); "
              "mirar = el menor entre alto x 1.02 y nivel x 0.97 (al menos comprar x 1.03); s = %s, sesgo = %s"
              % (f.get("s"), f.get("sesgo")))
    if d["sin_capacidad"]:
        print("variante con capacidad sin dato: no lleva minimo ni cifras propias")
    for nombre in ("minimo", "minimo_30d", "ajeno", "minimo_ciclo"):
        m = f.get(nombre)
        print("%s: %s" % (nombre, "null" if not m else "%s el %s en %s <- %s" % (
            _fmt(m["precio"]), m["fecha"], m["tienda"], _donde(por_rastro[m["rastro"]]))))
    t = f["tipico"]
    recientes = [(fe, o) for fe, o in d["dias"] if fe >= d["desde30"]]
    print("tipico: %s, del %s al %s, %d dias con lectura (hacen falta %d)" % (
        "null" if not t else _fmt(t["precio"]), d["desde30"], d["hasta"], len(recientes), MIN_DIAS_TIPICO))
    pc = f["percentiles"]
    print("percentiles: %s" % ("null" if not pc else "p10 %s, p25 %s, p50 %s sobre %d dias" % (
        _fmt(pc["p10"]), _fmt(pc["p25"]), _fmt(pc["p50"]), pc["n"])))
    pron = d.get("pron")
    if pron:
        serie = ctx.serie(prod, {var: 1.0})
        print("nivel: %s, mediana de la serie diaria rellenada del %s al %s (%d dias):" % (
            _fmt(pron["nivel"]), (pron["t"] - dt.timedelta(days=29)).isoformat(), pron["t"].isoformat(), pron["n_nivel"]))
        for k in range(VENTANA_DIAS - 1, -1, -1):
            dd = pron["t"] - dt.timedelta(days=k)
            if dd in serie:
                print("  %s %s <- %s" % (dd.isoformat(), _fmt(serie[dd][0]), _donde(serie[dd][1])))
        pz = f.get("piso")
        if pz:
            print("piso: %s [%s-%s], %s, horizonte %s" % (_fmt(pz["precio"]), _fmt(pz["rango"][0]), _fmt(pz["rango"][1]), pz["tipo"], pz["horizonte"]))
        for a, info_a, camino in pron["_usados"]:
            ta = pron["t"] - dt.timedelta(days=a["desfase"])
            print("  analogo %s (desfase %d): nivel %s el %s; camino minimo %s el %s <- analogo el %s: %s, %s" % (
                a["nombre"], a["desfase"], _fmt(info_a["nivel"]), ta.isoformat(), _fmt(info_a["piso"]), info_a["fecha"].isoformat(),
                info_a["fecha_analogo"].isoformat(), _fmt(camino[info_a["fecha"]][1]), _donde(info_a["obs"])))
            print("    dias del analogo que dan su nivel:")
            for k in range(VENTANA_DIAS - 1, -1, -1):
                dd = ta - dt.timedelta(days=k)
                if dd in a["serie"]:
                    print("      %s %s <- %s" % (dd.isoformat(), _fmt(a["serie"][dd][0]), _donde(a["serie"][dd][1])))
        for i in pron["analogos"]:
            if not i["usado"]:
                print("  analogo %s sin usar: %s" % (i["nombre"], i["motivo"]))
        if pz and pz["tipo"] == "estimacion":
            print("  piso = minimo_ciclo <- %s" % _donde(pron["minimo_ciclo"][2]))
        for e in pron["pisos_esperados"]:
            print("  %s (%s): %s [%s-%s] <- %s; %s" % (e["evento"], e["calendario"], _fmt(e["piso"]), _fmt(e["rango"][0]),
                                                     _fmt(e["rango"][1]), "; ".join(_donde(o) for o in e["obs"]), e["base"]))
    elif f.get("piso"):
        print("piso por proporcion: %s [%s-%s] (de %s)" % (_fmt(f["piso"]["precio"]), _fmt(f["piso"]["rango"][0]),
                                                         _fmt(f["piso"]["rango"][1]), HERMANAS.get(clave)))
    print("n_datos: %s" % json.dumps(f["n_datos"], ensure_ascii=False, sort_keys=True))
    for motivo, o in sorted(d["descartes"], key=lambda mo: (mo[0], mo[1]["fecha"])):
        print("  descartada %s: %s %s %s <- %s" % (motivo, o["fecha"], _fmt(o["precio"]), o["tienda"], _donde(o)))
    for o in d["ajenas"]:
        print("  banco ajeno: %s %s %s %s <- %s" % (o["fecha"], _fmt(o["precio"]), o["tienda"], o.get("banco", ""), _donde(o)))
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    orden = argv[0]
    if orden == "--selftest":
        selftest()
        print("selftest OK")
        return 0
    if orden == "--generar":
        t0 = time.perf_counter()
        doc, _, info = generar(AQUI)
        _imprimir_tabla(doc, info)
        print("tiempo: %.2f s" % (time.perf_counter() - t0))
        return 0
    if orden == "--recalcular":
        horas = float(argv[argv.index("--si-viejo") + 1]) if "--si-viejo" in argv[:-1] else None
        return recalcular(AQUI, si_viejo=horas)
    if orden == "--retroprueba":
        ruta_csv = argv[argv.index("--csv") + 1] if "--csv" in argv[:-1] else None
        resultados, s, sesgo, md = retroprueba(AQUI, ruta_csv)
        print(md.splitlines()[0])
        _tabla_retro(resultados, s, sesgo)
        print("escrito %s" % os.path.join(HISTORIA, RETRO_MD))
        return 0
    if orden == "--veredicto" and len(argv) >= 4:
        fecha = argv[argv.index("--fecha") + 1] if "--fecha" in argv[:-1] else None
        clave = "%s|%s" % (argv[1], argv[2])
        datos = cargar_json(hoy=fecha).get(clave)
        if datos is None:
            _aviso("%s no esta en un prospectiva.json valido; se calcula al momento" % clave)
            datos = construir(AQUI)[0]["variantes"].get(clave)
        print(razon(argv[3], datos, fecha) or "(sin nada que decir)")
        return 0
    if orden == "--rastro" and len(argv) >= 3:
        return _rastro(argv[1], argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
