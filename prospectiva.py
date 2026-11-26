#!/usr/bin/env python3
"""prospectiva.py: cifras de referencia de precios por variante y la razon de cada aviso.

Fase 1: cargador del historial, estadisticas por variante (minimo, lo normal, percentiles, banco ajeno),
topes iguales a los de vigia.py y razon() en lenguaje llano. Solo biblioteca estandar, Python 3.8 o mayor,
sin red. Lo usa vigia.py: cargar_json() y razon() nunca lanzan excepcion.

Uso:
  py -X utf8 prospectiva.py --selftest
  py -X utf8 prospectiva.py --generar
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

AQUI = os.path.dirname(os.path.abspath(__file__))
HISTORIA = "historia"
HISTORIAL = "historial_precios.csv"
CONFIG = "productos.json"
SALIDA = "prospectiva.json"
MX = dt.timezone(dt.timedelta(hours=-6))

VERSION = 1
VIGENCIA_DIAS = 30      # prospectiva.json caduca a los 30 dias de generado
VENTANA_DIAS = 30       # "ultimos 30 dias" = hoy y los 29 anteriores
MIN_DIAS_TIPICO = 5     # con menos dias con dato no se dice que es "lo normal"
MAX_RAZON = 320
IGUAL_MARGEN = 100      # diferencia menor que esto cuenta como "iguala"
TIPICO_MARGEN = 0.02    # "lo normal" solo se menciona si difiere mas de 2 %

COLUMNAS = ["fecha", "producto", "variante", "tienda", "precio", "lista", "condicion", "banco", "tipo_banco",
            "vendedor", "garantia_mx", "fuente", "url", "id", "nota"]
COLUMNAS_HISTORIAL = ["fecha", "tienda", "producto", "precio", "vendedor", "como", "liga"]
COLUMNAS_CALENDARIO = ["evento", "tipo", "inicio", "fin", "producto", "fuente_url", "cita"]
COLUMNAS_LISTAS = ["producto", "variante", "desde", "lista", "fuente_url", "cita"]
PISOS_ANTECESOR = {"fold7": 12000, "fold6": 12000}
EVENTOS_RESPALDO = [
    {"evento": "Buen Fin 2026", "tipo": "evento", "inicio": "2026-11-13", "fin": "2026-11-17", "producto": "",
     "fuente_url": "", "cita": "contexto comun de la linea A, 25 nov 2026", "rastro": "respaldo"},
    {"evento": "Black Friday 2026", "tipo": "evento", "inicio": "2026-11-27", "fin": "2026-11-30", "producto": "",
     "fuente_url": "", "cita": "contexto comun de la linea A, 25 nov 2026", "rastro": "respaldo"},
]
CANJE = re.compile(r"canje|a\s+cambio|trade[\s-]*in|plan\s+de\s+telefon|plan\s+tarifario|\bcon\s+plan\b", re.I)
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
METODO = ("Se juntaron los precios con garantia en Mexico que se pueden pagar sin canje y sin bancos ajenos "
          "(lecturas del vigia y archivos de historia/); el minimo es el mas barato visto, lo normal es el "
          "precio mas bajo tipico de cada dia en los ultimos 30 dias, y los topes siguen siendo los "
          "porcentajes de productos.json.")


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


def topes_vigia(prod, var):
    """Los mismos topes que hoy calcula vigia.topes()."""
    fijos = (prod.get("topes_fijos") or {}).get(var["id"])
    if fijos:
        return [int(x) for x in fijos], "topes_fijos"
    return [int(round(var["lista"] * f / 100.0)) * 100 for f in prod["fracciones"]], "fracciones"


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


# ---------------------------------------------------------------- validez y estadisticas

def clasificar(o, pisos):
    """'valida', 'ajeno' (falla solo por el banco) o el motivo del descarte."""
    fuente = str(o.get("fuente", "")).lower()
    gar = str(o.get("garantia_mx", "")).lower().replace("í", "i")
    rev = o.get("revision")
    revl = (rev or "").strip().lower()
    piso = pisos.get(o.get("producto"))
    if piso is None:
        return "producto_desconocido"
    if not (gar == "si" or (gar == "sin dato" and fuente in ("foro", "prensa") and revl == "ok")):           # (a)
        return "sin_garantia" if gar == "no" else "garantia_sin_confirmar"
    if CANJE.search(o.get("condicion") or ""):                                                                 # (c)
        return "canje_o_plan"
    if rev is not None and not (revl == "ok" or (revl == "" and fuente in ("propio", "keepa"))):              # (d)
        return "revision_no_ok" if revl else "sin_revision"
    if o["precio"] < piso:                                                                                     # (e)
        return "bajo_piso"
    tb = str(o.get("tipo_banco", "")).strip().lower()                                                          # (b)
    if tb in ("ninguno", "propio"):
        return "valida"
    if tb == "ajeno":
        return "ajeno"
    return "banco_sin_dato"


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
            "condicion": o.get("condicion", ""), "fuente": o.get("fuente", ""), "rastro": o["rastro"]}


def _ficha(prod, var, g, desde30, hasta):
    tope, origen = topes_vigia(prod, var)
    validas = {}
    for o in g["validas"]:
        validas[o["fuente"] or "sin dato"] = validas.get(o["fuente"] or "sin dato", 0) + 1
    descartadas = {}
    for motivo, _ in g["descartes"]:
        descartadas[motivo] = descartadas.get(motivo, 0) + 1
    ficha = {"nombre": var["nombre"], "lista": var["lista"], "topes": tope, "topes_origen": origen,
             "topes_manual": list(tope), "sin_confirmar_bajo": None, "minimo": None, "minimo_30d": None,
             "tipico": None, "percentiles": None, "ajeno": None, "pisos_esperados": [], "baja_mas": [],
             "n_datos": {"validas": validas, "ajeno": len(g["ajenas"]), "descartadas": descartadas}}
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
        ficha["ajeno"] = {"precio": _num_json(aj["precio"]), "fecha": aj["fecha"], "tienda": aj["tienda"],
                          "banco": aj.get("banco", ""), "rastro": aj["rastro"]}
    return ficha, det


def calcular(obs, prods, hoy=None):
    """Fichas por variante de productos.json. Devuelve (variantes, detalle) ; detalle sirve a --rastro."""
    hoy = _hoy(hoy)
    hasta = hoy.isoformat()
    desde30 = (hoy - dt.timedelta(days=VENTANA_DIAS - 1)).isoformat()
    pisos = dict(PISOS_ANTECESOR)
    pisos.update({p["id"]: p["piso"] for p in prods})
    grupos = {}
    for o in obs:
        g = grupos.setdefault("%s|%s" % (o["producto"], o["variante"]), {"validas": [], "ajenas": [], "descartes": []})
        motivo = "fecha_posterior" if o["fecha"] > hasta else clasificar(o, pisos)
        if motivo == "valida":
            g["validas"].append(o)
        elif motivo == "ajeno":
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
    ahora = (ahora or dt.datetime.now(MX)).astimezone(MX).replace(microsecond=0)
    prods, obs, sin_traducir = cargar_todo(base)
    variantes, detalle = calcular(obs, prods, ahora.date())
    doc = {"version": VERSION, "generado": ahora.isoformat(),
           "vence": (ahora.date() + dt.timedelta(days=VIGENCIA_DIAS)).isoformat(),
           "metodo": METODO, "variantes": variantes}
    escribir_json(doc, ruta or os.path.join(base, SALIDA))
    eventos, origen_cal = cargar_calendario(os.path.join(base, HISTORIA))
    listas, origen_listas = cargar_listas(os.path.join(base, HISTORIA), prods)
    info = {"observaciones": len(obs), "sin_traducir": sin_traducir, "eventos": len(eventos),
            "origen_calendario": origen_cal, "listas": len(listas), "origen_listas": origen_listas}
    return doc, detalle, info


# ---------------------------------------------------------------- lo que llama vigia.py

def _terna_ok(topes, piso, lista):
    if not isinstance(topes, list) or len(topes) != 3:
        return False
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in topes):
        return False
    g, c, m = topes
    return g < c < m and piso <= g and m <= lista


def _cargar_json(ruta, hoy):
    ruta = ruta or os.path.join(AQUI, SALIDA)
    if not os.path.exists(ruta):
        _aviso("%s no existe; se usan las fracciones" % os.path.basename(ruta))
        return {}
    with open(ruta, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or doc.get("version") != VERSION:
        _aviso("prospectiva.json con version distinta de %d; no se usa" % VERSION)
        return {}
    vence = dt.date.fromisoformat(str(doc.get("vence", ""))[:10])
    if _hoy(hoy) > vence:
        _aviso("prospectiva.json vencio el %s; no se usa" % vence.isoformat())
        return {}
    variantes = doc.get("variantes")
    if not isinstance(variantes, dict):
        _aviso("prospectiva.json sin 'variantes'; no se usa")
        return {}
    prods = {p["id"]: p for p in leer_productos(os.path.join(os.path.dirname(os.path.abspath(ruta)), CONFIG))}
    por_prod = {}
    for clave, datos in variantes.items():
        pid = str(clave).split("|", 1)[0]
        if pid not in prods:
            _aviso("prospectiva.json: %s no esta en productos.json; se quita" % clave)
            continue
        por_prod.setdefault(pid, {})[clave] = datos
    limpio = {}
    for pid, grupo in por_prod.items():
        p = prods[pid]
        listas = {"%s|%s" % (pid, v["id"]): v["lista"] for v in p["variantes"]}
        falla, ternas = None, {}
        for clave, datos in sorted(grupo.items()):
            if clave not in listas:
                falla = "%s no esta en productos.json" % clave
            elif not isinstance(datos, dict) or not _terna_ok(datos.get("topes"), p["piso"], listas[clave]):
                falla = "%s trae topes no validos (%r)" % (clave, datos.get("topes") if isinstance(datos, dict) else datos)
            else:
                t = tuple(datos["topes"])
                otra = ternas.get(t)
                if otra and listas[otra] != listas[clave]:
                    falla = "%s y %s tienen listas distintas y la misma terna %s" % (otra, clave, list(t))
                ternas.setdefault(t, clave)
            if falla:
                break
        if falla:
            _aviso("prospectiva.json: %s; el producto %s vuelve a sus fracciones" % (falla, pid))
            continue
        limpio.update(grupo)
    return limpio


def cargar_json(ruta=None, hoy=None):
    """Variantes validadas de prospectiva.json, o {} ante cualquier problema. Nunca lanza excepcion."""
    try:
        return _cargar_json(ruta, hoy)
    except Exception as e:
        _aviso("prospectiva.json no se usa (%s: %s)" % (type(e).__name__, e))
        return {}


def _razon(precio, datos, hoy):
    p = _numero(precio)
    if p is None:
        return ""
    d = datos if isinstance(datos, dict) else {}
    try:
        hoy = _hoy(hoy)
    except Exception:
        hoy = _hoy(None)
    frases = []
    sc = _numero(d.get("sin_confirmar_bajo"))
    if sc is None and isinstance(d.get("sin_confirmar_bajo"), dict):
        sc = _numero(d["sin_confirmar_bajo"].get("precio"))
    if sc is not None and p < sc:
        frases.append("Sin confirmar: está muy por debajo de todo lo visto con garantía; abre la ficha y revisa "
                      "quién vende antes de creerlo.")
    mn = d.get("minimo") if isinstance(d.get("minimo"), dict) else {}
    x = _numero(mn.get("precio"))
    if x is not None:
        partes = []
        f = _fecha_corta(mn.get("fecha"), hoy)
        if f:
            partes.append("el " + f)
        t = mn.get("tienda")
        if isinstance(t, str) and t.strip():
            partes.append("en " + t.strip())
        donde = " ".join(partes)
        if abs(p - x) < IGUAL_MARGEN:
            frases.append("Iguala el precio más bajo visto con garantía (%s%s)." % (_dinero(x), ", " + donde if donde else ""))
        elif p < x:
            frases.append("%s es el precio más bajo visto con garantía en México desde que salió; el anterior fue %s%s."
                          % (_dinero(p), _dinero(x), " " + donde if donde else ""))
        else:
            frases.append("Está %s arriba del más bajo visto con garantía (%s%s)."
                          % (_dinero(p - x), _dinero(x), ", " + donde if donde else ""))
    tip = d.get("tipico") if isinstance(d.get("tipico"), dict) else {}
    tp = _numero(tip.get("precio"))
    if tp is not None and abs(p - tp) / tp > TIPICO_MARGEN:
        frases.append("Lo normal en los últimos 30 días ha sido %s." % _dinero(tp))
    pisos = d.get("pisos_esperados") if isinstance(d.get("pisos_esperados"), list) else []
    hoy_s = hoy.isoformat()
    vienen = []
    for e in pisos:
        if not isinstance(e, dict):
            continue
        piso, fin, nombre = _numero(e.get("piso")), str(e.get("fin") or "")[:10], e.get("evento")
        if piso is None or not isinstance(nombre, str) or not nombre.strip() or len(fin) != 10 or fin < hoy_s:
            continue
        if piso < p:
            vienen.append((str(e.get("inicio") or fin)[:10], nombre.strip(), piso))
    if vienen:
        _, nombre, piso = sorted(vienen)[0]
        frases.append("En %s el modelo anterior llegó a un precio equivalente a %s." % (nombre, _dinero(piso)))
    while len(" ".join(frases)) > MAX_RAZON and len(frases) > 1:
        frases.pop()
    texto = " ".join(frases)
    if len(texto) > MAX_RAZON:
        texto = texto[:MAX_RAZON - 3].rsplit(" ", 1)[0] + "..."
    return texto


def razon(precio, datos, hoy=None):
    """Una o dos frases en lenguaje llano (320 caracteres como maximo) de por que el precio es bueno o no."""
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
              "tipo_banco": "ninguno", "garantia_mx": "si", "fuente": "propio", "revision": None,
              "rastro": "prueba:%s:%s" % (fecha, precio)})
    o.update(k)
    return o


def selftest():
    hoy = dt.date(2026, 11, 25)
    prods = PRODS_PRUEBA["productos"]
    # 1. topes de fase 1 iguales a los de vigia.py
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
                  "2026-11-24,fold8,ultra-512,Samsung,28000,,,,ninguno,sin dato,si,foro,u,F11,,\n")
        _escribir(os.path.join(hist, "calendario.csv"), ",".join(COLUMNAS_CALENDARIO) +
                  "\nBuen Fin 2026,evento,2026-11-13,2026-11-17,,u,cita\n")
        _escribir(os.path.join(hist, "vacio.csv"), "")
        _escribir(os.path.join(hist, "cortado.csv"), ",".join(COLUMNAS) + "\n2026-11-24,fold8,ultra-512,Samsung,\"300")
        _escribir(os.path.join(tmp, HISTORIAL), ",".join(COLUMNAS_HISTORIAL) + "\n"
                  "2026-11-19 18:57,Samsung,Fold8 Ultra 512 GB,34199.20,Samsung,\"con cupones X\",https://x\n"
                  "2026-11-19 19:25,Liverpool,Fold8 Ultra (capacidad sin dato),55999.00,Liverpool,,https://y\n"
                  "2026-11-24 10:06,Amazon,PS5 con lector de disco,14498.00,Amazon México,,https://z\n"
                  "2026-11-24 10:53,Gameplanet,PS5 Digital (sin lector),10999.99,Gameplanet,,https://w\n")
        # 2. cargador: encabezado ajeno, renglon roto, archivo vacio, archivo cortado
        obs = cargar_historia(hist)
        ids = {o["id"]: o for o in obs}
        assert sorted(ids) == ["F11", "F4", "F6", "K10", "P1", "P2", "P3", "P5", "P9"], sorted(ids)
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
                           "F11": "sin_revision"}, motivos
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
        assert v["fold8|ultra-512"]["minimo"]["precio"] == 29000, v["fold8|ultra-512"]["minimo"]
        assert v["fold8|ultra-512"]["minimo"]["rastro"] == "historia/prueba.csv:F6", v["fold8|ultra-512"]["minimo"]
        assert v["fold8|ultra-512"]["ajeno"]["precio"] == 25000 and v["fold8|ultra-512"]["ajeno"]["banco"] == "Banorte"
        assert v["fold8|ultra-512"]["n_datos"]["validas"] == {"propio": 2, "foro": 1, "keepa": 1}
        assert v["fold8|ultra-512"]["n_datos"]["descartadas"] == {"canje_o_plan": 1, "revision_no_ok": 1,
                                                                  "sin_garantia": 1, "bajo_piso": 1, "sin_revision": 1}
        assert v["fold8|ultra"]["minimo"] is None and v["fold8|normal-512"]["minimo"] is None
        assert v["ps5|digital"]["minimo"]["precio"] == 10999.99
        for clave, t in esperado.items():
            assert v[clave]["topes"] == t and v[clave]["topes_manual"] == t and v[clave]["topes_origen"] == "fracciones"
        assert info["origen_calendario"] == "historia/calendario.csv" and info["eventos"] == 1
        with open(ruta, "rb") as fb:
            assert b"\r\n" not in fb.read()
        assert sorted(cargar_json(ruta, hoy)) == sorted(v), sorted(cargar_json(ruta, hoy))
        assert cargar_json(ruta, "2026-12-25") and cargar_json(ruta, "2026-12-26") == {}
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
        r = prueba("desordenada.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[9000, 7500, 10800]))
        assert "ps5|digital" not in r and "fold8|ultra-512" in r
        r = prueba("repetida.json", lambda d2: d2["variantes"]["fold8|normal-512"].update(topes=[22000, 24000, 26000]))
        assert not any(k.startswith("fold8|") for k in r) and "ps5|digital" in r, sorted(r)
        r = prueba("misma_lista.json", lambda d2: d2["variantes"]["fold8|ultra"].update(topes=[22000, 24000, 26000]))
        assert "fold8|ultra" in r
        assert "ps5|digital" not in prueba("alto.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[7500, 9000, 13000]))
        assert "ps5|digital" not in prueba("bajo.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[6000, 9000, 10800]))
        assert "ps5|digital" not in prueba("float.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[7500.5, 9000, 10800]))
        assert "ps5|digital" not in prueba("dos.json", lambda d2: d2["variantes"]["ps5|digital"].update(topes=[7500, 9000]))
        r = prueba("extra.json", lambda d2: d2["variantes"].update({"ps5|pro": {"topes": [7000, 8000, 9000]}}))
        assert not any(k.startswith("ps5|") for k in r)
        otra = os.path.join(tmp, "sin_config")
        os.mkdir(otra)
        escribir_json(doc, os.path.join(otra, SALIDA))
        assert cargar_json(os.path.join(otra, SALIDA), hoy) == {}
    # 8. razon
    datos = {"minimo": {"precio": 34199.2, "fecha": "2026-11-19", "tienda": "Samsung"}, "tipico": {"precio": 34199.2, "n": 6}}
    r = razon(23999, datos, hoy)
    assert r.startswith("$23,999 es el precio más bajo visto con garantía en México desde que salió; el anterior fue "
                        "$34,199 el 19 nov en Samsung."), r
    assert "Lo normal en los últimos 30 días ha sido $34,199." in r, r
    r = razon(34250, datos, hoy)
    assert r == "Iguala el precio más bajo visto con garantía ($34,199, el 19 nov en Samsung).", r
    r = razon(36000, datos, hoy)
    assert r.startswith("Está $1,801 arriba del más bajo visto con garantía ($34,199, el 19 nov en Samsung).") and "Lo normal" in r, r
    assert "Lo normal" not in razon(34500, datos, hoy)
    assert "28 nov 2025" in razon(36000, {"minimo": {"precio": 30000, "fecha": "2025-11-28", "tienda": "Amazon"}}, hoy)
    assert razon(30000, {}, hoy) == "" and razon(30000, None, hoy) == ""
    d2 = dict(datos, sin_confirmar_bajo=20000)
    assert razon(15000, d2, hoy).startswith("Sin confirmar: está muy por debajo de todo lo visto con garantía;")
    assert not razon(25000, d2, hoy).startswith("Sin confirmar")
    d3 = dict(datos, pisos_esperados=[{"evento": "Buen Fin 2026", "inicio": "2026-11-13", "fin": "2026-11-17", "piso": 20000},
                                      {"evento": "Black Friday 2026", "inicio": "2026-11-27", "fin": "2026-11-30", "piso": 28000}])
    r = razon(36000, d3, hoy)
    assert "En Black Friday 2026 el modelo anterior llegó a un precio equivalente a $28,000." in r and "Buen Fin" not in r, r
    assert "modelo anterior" not in razon(27000, d3, hoy)
    raros = [None, {}, [], "x", {"minimo": None}, {"minimo": {"precio": None}}, {"minimo": "x"},
             {"minimo": {"precio": "abc", "fecha": "xx", "tienda": 5}}, {"tipico": {"precio": 0}},
             {"tipico": None, "pisos_esperados": [None, 3, {"piso": "x"}, {"evento": 4, "fin": None, "piso": 1}]},
             {"sin_confirmar_bajo": "x"}, {"sin_confirmar_bajo": {"precio": 99999}},
             {"minimo": {"precio": 30000, "fecha": None, "tienda": None}},
             {"minimo": {"precio": 30000, "tienda": "T" * 500}, "tipico": {"precio": 1}, "sin_confirmar_bajo": 99999,
              "pisos_esperados": [{"evento": "E" * 400, "fin": "2026-12-01", "piso": 2}]},
             {"minimo": {"precio": "T" * 500}}]
    for dd in raros:
        for pr in (None, "abc", -5, 0, 25000, float("nan"), float("inf"), True, "25000", [1]):
            for hh in (hoy, None, "2026-11-25", "malo"):
                r = razon(pr, dd, hh)
                assert isinstance(r, str) and len(r) <= MAX_RAZON, (pr, dd, r)
    assert razon(25000, {"minimo": {"precio": 30000, "fecha": None, "tienda": None}}, hoy) == \
        "$25,000 es el precio más bajo visto con garantía en México desde que salió; el anterior fue $30,000."
    return True


# ---------------------------------------------------------------- linea de comandos

def _fmt(x):
    return "-" if x is None else "{:,.0f}".format(float(x))


def _imprimir_tabla(doc, info):
    print("prospectiva.json generado %s, vence %s" % (doc["generado"], doc["vence"]))
    print("observaciones leidas: %d; calendario: %d eventos (%s); listas: %d (%s)" % (
        info["observaciones"], info["eventos"], info["origen_calendario"], info["listas"], info["origen_listas"]))
    if info["sin_traducir"]:
        print("renglones del vigia sin variante en productos.json: %s" % ", ".join(
            "%s (%d)" % kv for kv in sorted(info["sin_traducir"].items())))
    print("%-17s %-19s %-34s %-12s %-26s %-6s %s" % ("variante", "topes", "minimo", "tipico (n)", "validas", "ajeno", "descartadas"))
    for clave, f in sorted(doc["variantes"].items()):
        m = f["minimo"]
        mtxt = "%s %s %s" % (_fmt(m["precio"]), m["fecha"], m["tienda"]) if m else "-"
        t = f["tipico"]
        ttxt = "%s (%d)" % (_fmt(t["precio"]), t["n"]) if t else "-"
        nd = f["n_datos"]
        val = ", ".join("%s %d" % kv for kv in sorted(nd["validas"].items())) or "0"
        desc = ", ".join("%s %d" % kv for kv in sorted(nd["descartadas"].items())) or "0"
        aj = _fmt(f["ajeno"]["precio"]) if f["ajeno"] else str(nd["ajeno"])
        print("%-17s %-19s %-34s %-12s %-26s %-6s %s" % (clave, "/".join(str(x) for x in f["topes"]), mtxt, ttxt, val, aj, desc))


def _donde(o):
    return "%s renglon %d (id %s)" % (o["archivo"], o["renglon"], o["id"])


def _rastro(prod, var):
    prods, obs, _ = cargar_todo(AQUI)
    variantes, detalle = calcular(obs, prods, None)
    clave = "%s|%s" % (prod, var)
    if clave not in variantes:
        print("no existe la variante %s en productos.json" % clave)
        return 1
    f, d = variantes[clave], detalle[clave]
    p = next(x for x in prods if x["id"] == prod)
    v = next(x for x in p["variantes"] if x["id"] == var)
    por_rastro = {o["rastro"]: o for o in d["validas"] + d["ajenas"]}
    print("%s (%s), calculado al %s" % (clave, f["nombre"], d["hasta"]))
    if f["topes_origen"] == "fracciones":
        print("topes %s: productos.json, lista %s x fracciones %s, redondeo a centenas (igual que vigia.py)"
              % (f["topes"], v["lista"], p["fracciones"]))
    else:
        print("topes %s: productos.json, topes_fijos de %s" % (f["topes"], var))
    if d["sin_capacidad"]:
        print("variante con capacidad sin dato: no lleva minimo ni cifras propias")
    for nombre in ("minimo", "minimo_30d", "ajeno"):
        m = f[nombre]
        print("%s: %s" % (nombre, "null" if not m else "%s el %s en %s <- %s" % (
            _fmt(m["precio"]), m["fecha"], m["tienda"], _donde(por_rastro[m["rastro"]]))))
    t = f["tipico"]
    recientes = [(fe, o) for fe, o in d["dias"] if fe >= d["desde30"]]
    print("tipico: %s, del %s al %s, %d dias con dato (hacen falta %d)" % (
        "null" if not t else _fmt(t["precio"]), d["desde30"], d["hasta"], len(recientes), MIN_DIAS_TIPICO))
    pc = f["percentiles"]
    print("percentiles: %s" % ("null" if not pc else "p10 %s, p25 %s, p50 %s sobre %d dias" % (
        _fmt(pc["p10"]), _fmt(pc["p25"]), _fmt(pc["p50"]), pc["n"])))
    print("precio valido mas barato de cada dia (base de tipico y percentiles):")
    for fe, o in d["dias"]:
        print("  %s %s %s%s <- %s" % (fe, _fmt(o["precio"]), o["tienda"], " (30 dias)" if fe >= d["desde30"] else "", _donde(o)))
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
        doc, _, info = generar(AQUI)
        _imprimir_tabla(doc, info)
        return 0
    if orden == "--veredicto" and len(argv) >= 4:
        fecha = argv[argv.index("--fecha") + 1] if "--fecha" in argv[:-1] else None
        clave = "%s|%s" % (argv[1], argv[2])
        datos = cargar_json(hoy=fecha).get(clave)
        if datos is None:
            _aviso("%s no esta en un prospectiva.json valido; se calcula al momento" % clave)
            prods, obs, _ = cargar_todo(AQUI)
            datos = calcular(obs, prods, fecha)[0].get(clave)
        print(razon(argv[3], datos, fecha) or "(sin nada que decir)")
        return 0
    if orden == "--rastro" and len(argv) >= 3:
        return _rastro(argv[1], argv[2])
    if orden in ("--recalcular", "--retroprueba"):
        print("pendiente de la fase 2")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
