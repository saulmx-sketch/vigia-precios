"""Casos del revisor independiente R1 (25 nov 2026) y sus arreglos: un caso por hallazgo, sin red ni reloj.

Punto de partida: lineas\\C\\_scratch\\R1_pruebas.py del revisor. Respuestas grabadas en pruebas\\respuestas (LEEME.md dice
cuales estan alteradas a proposito). Uso:  py -X utf8 pruebas\\casos_revisor.py   (o vigia.py --selftest, via correr(v))
"""
import datetime as dt
import json
import os
import re
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RESP = os.path.join(AQUI, "respuestas")
AHORA = dt.datetime(2026, 11, 27, 13, 5, tzinfo=dt.timezone.utc)
LIV = "https://www.liverpool.com.mx/tienda"
K_AMZ = "Amazon|ps5|digital"


def lee(n):
    with open(os.path.join(RESP, n), encoding="utf-8") as f:
        return f.read()


class Red:
    v = None

    def __init__(self, mapa):
        self.mapa, self.pedidas = mapa, []
        if Red.v is not None:
            Red.v.LIVERPOOL_LEIDAS.clear()

    def __call__(self, url, datos=None, metodo=None, abridor=None, json_api=False, pausa=0.0, reintento=2.0):
        self.pedidas.append(url)
        for patron, r in self.mapa:
            if re.search(patron, url):
                return r if isinstance(r, tuple) else (200, lee(r))
        return 404, ""


AMZ = [(r"amazon\.com\.mx/s\?k=", "amz_busca_ps5.html"), (r"/dp/B0FTP523RH", "amz_ficha_B0FTP523RH.html")]
LIV_PS5 = [(r"tienda\?s=playstation", "liv_busca_ps5.html"), (r"pdp/p/1156059630", "liv_pdp_1156059630.html"),
           (r"pdp/p/1207352230", "liv_pdp_1207352230_alterada.html"), (r"pdp/p/1186785807", "liv_pdp_1186785807.html"),
           (r"pdp/p/1186785793", "liv_pdp_1186785793.html")]


def ps5_x(precio_txt, vendedor="TiendaX", **extra):
    def ficha(url):
        d = {"titulo": "Consola PlayStation 5 Digital 825 GB", "precio": precio_txt, "vendedor": vendedor,
             "existencia": True, "importado": False}
        d.update(extra)
        return d
    return ficha


def oferta_x(tienda, precio, url="https://tiendax.mx/p/1"):
    return {"prod": "ps5", "var": "digital", "nombre": "PS5 Digital (sin lector)", "tienda": tienda, "precio": precio,
            "url": url, "vendedor": tienda, "nota": "", "titulo": "Consola PS5 Digital"}


def correr(v):
    guardado = (v.pedir, dict(v.FICHAS), dict(v.VENDEDOR_OFICIAL), v.LIVERPOOL_SEGUNDOS)
    Red.v = v
    try:
        _correr(v)
    finally:
        v.pedir = guardado[0]
        v.FICHAS.clear(); v.FICHAS.update(guardado[1])
        v.VENDEDOR_OFICIAL.clear(); v.VENDEDOR_OFICIAL.update(guardado[2])
        v.LIVERPOOL_SEGUNDOS = guardado[3]
        v.LIVERPOOL_LEIDAS.clear()
    print("casos_revisor OK")


def _correr(v):
    prods = v.cargar_config()
    fold, ps = prods[0], next(p for p in prods if p["id"] == "ps5")
    por_id = {p["id"]: p for p in prods}

    # F1. Amazon: dos corridas con busqueda $8,999 y ficha $12,499 -> en las dos manda $12,499 y no sale aviso
    est = {}
    for corrida in (1, 2):
        v.pedir = Red(AMZ)
        out = v._leer_amazon(ps, {"consultas": ["playstation+5+digital"]}, est, [])
        assert [o["precio"] for o in out] == [12499.0], (corrida, out)
        assert v.avisos_de_precio({K_AMZ: out[0]}, por_id, est.setdefault("avisado", {}), est, AHORA) == [], corrida
        fichas = sum("/dp/" in u for u in v.pedir.pedidas)
        assert fichas == (1 if corrida == 1 else 0), (corrida, fichas)   # la segunda usa lo recordado, con la ficha
    assert est["vendedor_amazon"]["B0FTP523RH"] == [8999.0, "Amazon México", False, "ps5", 12499.0], est["vendedor_amazon"]
    # lo recordado con cuatro elementos (antes de este arreglo) no basta: se relee la ficha
    est = {"vendedor_amazon": {"B0FTP523RH": [8999.0, "Amazon México", False, "ps5"]}}
    v.pedir = Red(AMZ)
    out = v._leer_amazon(ps, {"consultas": ["playstation+5+digital"]}, est, [])
    assert out[0]["precio"] == 12499.0 and sum("/dp/" in u for u in v.pedir.pedidas) == 1, out
    # ...y si esa ficha se rechaza (nube), queda lo de antes para no vaciar la tabla, sin reescribir la memoria
    viejo = [8999.0, "Amazon México", False, "ps5"]
    est = {"vendedor_amazon": {"B0FTP523RH": list(viejo)}}
    v.pedir = Red([AMZ[0], (r"/dp/", (503, "x" * 1669))])
    out = v._leer_amazon(ps, {"consultas": ["playstation+5+digital"]}, est, [])
    assert [(o["precio"], o["vendedor"]) for o in out] == [(8999.0, "Amazon México")], out
    assert est["vendedor_amazon"]["B0FTP523RH"] == viejo, est

    # F2. "Volvio a la venta" no contradice al aviso de precio de la misma corrida
    demo = json.loads(json.dumps(ps))
    demo["topes_fijos"] = {"digital": [8000, 9500, 11000]}
    pdemo = {"fold8": fold, "ps5": demo}
    o = oferta_x("Amazon", 8999.0, "https://www.amazon.com.mx/dp/B0FTP523RH?th=1&psc=1")
    o["vendedor"] = "Amazon México"

    def regreso(fichas):
        e = {"conocidos": {K_AMZ: {"producto": o["nombre"], "precio": 12499, "url": "u", "visto": "h"}},
             "agotados": {K_AMZ: {"desde": "desde h", "precio": 12499}}}
        return v.disponibilidad(e, [fold, demo], {K_AMZ: dict(o)}, {"Amazon"}, "h", fichas)[0]
    v.pedir = Red(AMZ)
    av = v.avisos_de_precio({K_AMZ: dict(o)}, pdemo, {}, {}, AHORA)[0]       # la ficha dice $12,499: no coincide
    assert av["resultado"] == "no_coincide" and av["asunto"].startswith("SIN CONFIRMAR $8,999"), av["asunto"]
    info = {"resultado": av["resultado"], "nivel": av["nivel"], "razon": av["razon"]}
    a = regreso({K_AMZ: dict(info, salio=True)})
    assert a[0] == "Volvio a la venta: PS5 Digital (sin lector) en Amazon a $8,999 · mira el aviso de precio de esta corrida", a[0]
    assert "COMPRAR" not in a[0] and "GANGA" not in a[0] and a[2] == K_AMZ, a
    a = regreso({K_AMZ: dict(info, salio=False)})                           # el de precio no salio: lleva lo de la ficha
    assert a[0].endswith("· SIN CONFIRMAR · abre la ficha antes de creerlo") and a[1].startswith("Sin confirmar: el listado"), a
    a = regreso({K_AMZ: {"resultado": "sin_releer", "nivel": "COMPRAR", "razon": "sin tiempo", "salio": False}})
    assert a[0].endswith("· COMPRAR · ficha sin releer"), a[0]
    a = regreso({K_AMZ: {"resultado": "confirmada", "nivel": "COMPRAR", "razon": "coincide", "salio": False}})
    assert a[0].endswith("· COMPRAR"), a[0]
    a = regreso(None)                                                         # no se releyo en esta corrida
    assert a[0].endswith("· COMPRAR · ficha sin releer"), a[0]

    # F3. Tienda sin regla en VENDEDOR_OFICIAL: no es oficial. "TiendaY" no entra a la tabla ni da GANGA
    v.VENDEDOR_OFICIAL.pop("TiendaY", None)
    v.FICHAS["TiendaY"] = ps5_x(7400.0, "Revendedor Importados MX")
    assert v.oferta(ps, "TiendaY", "Consola PlayStation 5 Digital 825 GB", 7400.0, "https://tiendaY.mx/p/9", "") is None
    assert v.oferta(ps, "TiendaY", "Consola PlayStation 5 Digital 825 GB", 7400.0, "u", "TiendaY") is None
    avs = v.avisos_de_precio({"TiendaY|ps5|digital": oferta_x("TiendaY", 7400.0, "https://tiendaY.mx/p/9")}, por_id, {}, {}, AHORA)
    assert [a["nivel"] for a in avs] == ["SIN CONFIRMAR"] and "Revendedor Importados MX" in avs[0]["razon"], avs
    assert v.vendedor_oficial(fold, "Samsung", "Samsung")                    # Samsung tiene su regla: nada cambia hoy
    v.FICHAS.pop("TiendaY")

    # F4. PS5 Pro de Liverpool sin la palabra "Pro" (2 TB): no se le pega "Digital" y la variante la rechaza
    with open(os.path.join(RESP, "liv_pdp_1186785807_2tb_alterada.html"), encoding="utf-8") as f:
        fi = v.ficha_liverpool_texto(f.read(), "1186785807")
    assert "igital" not in fi["titulo"] and "2 TB" in fi["titulo"], fi["titulo"]
    assert v.variante(ps, fi["titulo"]) is None
    assert v.variante(ps, "Consola PS5 Digital 2 TB") is None and v.variante(ps, "Consola PS5 Digital 825 GB")["id"] == "digital"
    assert v.consola_de_ficha({"Tipo de consola": "Fija", "Lector integrado": "Sin lector", "Disco duro": "2 TB"}) == ""
    assert v.consola_de_ficha({"Tipo de consola": "Fija", "Lector integrado": "Sin lector", "Disco duro": "825 GB",
                               "Capacidad de memoria RAM": "2 TB"}) == " Digital (sin lector de disco)"

    # P1. Una ficha rara (precio "8,499.00") no tumba los avisos; la oferta buena de detras sale igual
    v.VENDEDOR_OFICIAL["TiendaX"] = r"^\s*tiendax\s*$"
    v.FICHAS["TiendaX"] = ps5_x("8,499.00")
    v.pedir = Red([])
    buena = dict(oferta_x("Elektra", 8800.0, "https://www.elektra.mx/x-1302051913/p"), vendedor="Elektra")
    avs = v.avisos_de_precio({"TiendaX|ps5|digital": oferta_x("TiendaX", 8400.0), "Elektra|ps5|digital": buena},
                             por_id, {}, {}, AHORA)
    assert sorted((a["clave"], a["resultado"]) for a in avs) == [
        ("Elektra|ps5|digital", "sin_releer"), ("TiendaX|ps5|digital", "confirmada")], avs
    assert next(a for a in avs if a["clave"].startswith("TiendaX"))["precio"] == 8499.0
    assert v.numero("$8,499.00") == 8499.0 and v.numero(8499) == 8499.0 and v.numero("sin precio") is None
    v.FICHAS["TiendaX"] = ps5_x(8499.0, opciones="abc")                     # algo que truena al revisarla
    r, _, razon = v.confirmar(oferta_x("TiendaX", 8400.0), ps, {})
    assert r == "sin_releer" and razon.startswith("error al revisar la ficha: "), (r, razon)

    # P3. SIN CONFIRMAR a $7,000 y la ficha confirma a $7,100: tres corridas, exactamente un correo confirmado
    v.FICHAS["TiendaX"] = ps5_x(7100.0)
    k = "TiendaX|ps5|digital"
    est = {"avisado": {k: 7000.0}, "avisado_nivel": {k: ["SIN CONFIRMAR", 7000.0]}}
    avisado, niveles = v.normalizar_avisado(est)
    abiertos = []
    for _ in range(3):
        for av in v.avisos_de_precio({k: oferta_x("TiendaX", 7000.0)}, por_id, avisado, est, AHORA):
            v.publicar_aviso(av, avisado, False, False, abrir=lambda a, c: abiertos.append(a), niveles=niveles)
    assert abiertos == ["GANGA $7,100 · PS5 Digital (sin lector) · TiendaX"], abiertos
    assert est["avisado"] == {k: 7100.0} and est["avisado_nivel"] == {k: ["GANGA", 7100.0]}, est

    # R1. Ida y vuelta de version: si la version vieja reaviso (avisado cambio), el nivel guardado se descarta
    est = {"avisado": {k: 7200.0}, "avisado_nivel": {k: ["SIN CONFIRMAR", 7000.0]}}
    avisado, niveles = v.normalizar_avisado(est)
    assert niveles == {} and v.avisos_de_precio({k: oferta_x("TiendaX", 7200.0)}, por_id, avisado, est, AHORA) == []
    est = {"avisado": {k: 7000.0}, "avisado_nivel": {k: "SIN CONFIRMAR"}}   # texto suelto de la ronda 2: se lee
    v.normalizar_avisado(est)
    assert est["avisado_nivel"] == {k: ["SIN CONFIRMAR", 7000.0]}, est
    assert all(isinstance(x, (int, float)) for x in est["avisado"].values())

    # C1. Topes de tiempo: Liverpool deja de abrir fichas tras 2 fallas seguidas o pasado su tope; confirmar sin tiempo
    v.pedir = Red([(r"tienda\?s=playstation", "liv_busca_ps5.html"), (r"pdp/p/", (503, "no"))])
    try:
        v.leer_liverpool(ps, ps["tiendas"]["Liverpool"], {})
        raise AssertionError("Liverpool sin fichas debio fallar")
    except RuntimeError:
        pass
    assert sum("pdp/p/" in u for u in v.pedir.pedidas) == 2, v.pedir.pedidas
    v.LIVERPOOL_SEGUNDOS = -1
    v.pedir = Red(LIV_PS5)
    out = v.leer_liverpool(ps, ps["tiendas"]["Liverpool"], {})
    assert sum("pdp/p/" in u for u in v.pedir.pedidas) == 1, v.pedir.pedidas       # solo la primera (la mas barata)
    v.LIVERPOOL_SEGUNDOS = 120
    rota = Red([(r"pdp/p/", (503, "no"))])
    v.pedir = Red(LIV_PS5)
    out = v.leer_liverpool(ps, ps["tiendas"]["Liverpool"], {})
    bundle = out[0]
    v.pedir = rota                                                          # la red se cae despues de leer
    demo2 = json.loads(json.dumps(ps))
    demo2["topes_fijos"] = {"digital": [13000, 14000, 15000]}
    r, final, _ = v.confirmar(bundle, demo2, {})
    assert r == "confirmada" and v.pedir.pedidas == [], (r, v.pedir.pedidas)   # ficha ya leida en esta corrida
    avs = v.avisos_de_precio({"Liverpool|ps5|digital": dict(bundle)}, {"fold8": fold, "ps5": demo2}, {}, {}, AHORA,
                             arranque=time.monotonic() - v.SEGUNDOS_CONFIRMAR - 1)
    assert [(a["resultado"], a["razon"], a["nivel"]) for a in avs] == [("sin_releer", "sin tiempo", "GANGA")], avs

    # M1. abrir_issue sin credenciales (devuelve False): no se marca como avisado
    avisado, niv = {}, {}
    ok = v.publicar_aviso({"clave": "k", "asunto": "a", "cuerpo": "c", "precio": 1.0, "nivel": "GANGA"}, avisado, False,
                          False, abrir=lambda a, c: False, niveles=niv)
    assert ok is False and avisado == {} and niv == {}, (ok, avisado, niv)

    # M3. Costco sin dato de existencia: sin_releer con la advertencia (su nivel), no SIN CONFIRMAR
    c = json.loads(lee("cos_api_648324.json"))
    c.pop("stock")
    v.pedir = Red([(r"products/648324", (200, json.dumps(c)))])
    fdemo = json.loads(json.dumps(fold))
    fdemo["topes_fijos"] = {"normal-512": [38000, 40000, 42000]}
    oc = {"prod": "fold8", "var": "normal-512", "nombre": "Fold8 512 GB", "tienda": "Costco", "precio": 37999.0,
          "url": "https://www.costco.com.mx/x/p/648324", "vendedor": "Costco (pide membresia)", "nota": "",
          "titulo": "Samsung Galaxy Z Fold 8 512GB Grafito"}
    avs = v.avisos_de_precio({"Costco|fold8|normal-512": oc}, {"fold8": fdemo, "ps5": ps}, {}, {}, AHORA)
    assert [(a["resultado"], a["razon"], a["nivel"]) for a in avs] == [
        ("sin_releer", "la ficha no dice si hay existencia", "GANGA")], avs
    assert avs[0]["asunto"].endswith("· ficha sin releer"), avs[0]["asunto"]

    # M4. Liverpool con offersInfo vacio: no se da por vendido por Liverpool
    plano = v.next_plano(lee("liv_pdp_1186785807.html")).replace('"offersInfo":{"offersCount":0}', '"offersInfo":{}')
    fi = v.ficha_liverpool_texto("<script>self.__next_f.push([1,%s])</script>" % json.dumps(plano), "1186785807")
    assert fi["vendedor"] == "", fi

    # M5. SIN CONFIRMAR por la ficha: el ultimo paso dice el precio de la ficha; Samsung sin carrito: nota sin el error
    v.pedir = Red(AMZ)
    av = v.avisos_de_precio({K_AMZ: dict(o)}, pdemo, {}, {}, AHORA)[0]
    assert "La ficha dice $12,499: compara antes de pagar." in av["cuerpo"] and "total sea $8,999" not in av["cuerpo"], av["cuerpo"]
    v.pedir = Red([(r"searchapi\.samsung", "sam_catalogo.json")])           # el carrito responde 404
    out = v.leer_samsung(fold, fold["tiendas"]["Samsung"], {})
    assert out and all(o["nota"] in ("precio de pagina, cupones sin probar", "preventa") or
                       o["nota"].startswith("precio de pagina, cupones sin probar") for o in out), [o["nota"] for o in out]
    assert not any("carrito" in o["nota"] for o in out), [o["nota"] for o in out]


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(AQUI))
    import vigia
    vigia.time.sleep = lambda *a: None
    correr(vigia)
