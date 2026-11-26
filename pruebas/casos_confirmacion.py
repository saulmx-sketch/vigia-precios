"""Pruebas de la ficha antes de avisar (confirmar), de Liverpool con vendedor leido y del texto del aviso.

Deterministas: sin red (pedir se sustituye por respuestas grabadas de pruebas\\respuestas, medidas el 25 nov 2026 desde
casa y recortadas; las que se alteraron a proposito lo dicen en respuestas\\LEEME.md) y sin reloj.
Uso:  py -X utf8 pruebas\\casos_confirmacion.py      (o desde vigia.py --selftest, que llama a correr(v))
"""
import datetime as dt
import json
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RESP = os.path.join(AQUI, "respuestas")
AHORA = dt.datetime(2026, 11, 27, 13, 5, tzinfo=dt.timezone.utc)      # 07:05 en Mexico
LIV = "https://www.liverpool.com.mx/tienda"


def lee(n):
    with open(os.path.join(RESP, n), encoding="utf-8") as f:
        return f.read()


class Red:
    """Sustituto de pedir: [(regex de la liga, archivo o (estado, texto))]; lo demas responde 404."""

    v = None   # modulo vigia; cada red nueva vacia la memoria de fichas de Liverpool de "esta corrida"

    def __init__(self, mapa):
        self.mapa, self.pedidas = mapa, []
        if Red.v is not None:
            Red.v.LIVERPOOL_LEIDAS.clear()

    def __call__(self, url, datos=None, metodo=None, abridor=None, json_api=False, pausa=0.0, **_otros):
        self.pedidas.append(url)
        for patron, r in self.mapa:
            if re.search(patron, url):
                return r if isinstance(r, tuple) else (200, lee(r))
        return 404, ""


LIV_OK = [(r"tienda\?s=playstation", "liv_busca_ps5.html"), (r"tienda\?s=galaxy", "liv_busca_fold8.html"),
          (r"pdp/p/1156059630", "liv_pdp_1156059630.html"), (r"pdp/p/1203150349", "liv_pdp_1203150349.html"),
          (r"pdp/p/1207352230", "liv_pdp_1207352230_alterada.html"), (r"pdp/p/1186785807", "liv_pdp_1186785807.html"),
          (r"pdp/p/1186785793", "liv_pdp_1186785793.html")]
LIV_PORTAL_6999 = [(r"pdp/p/1156059630", "liv_pdp_1156059630_6999.html")] + LIV_OK
LIV_SIN_FICHAS = [(r"tienda\?s=playstation", "liv_busca_ps5.html"), (r"tienda\?s=galaxy", "liv_busca_fold8.html"),
                  (r"pdp/p/", (503, "<html>Service Unavailable</html>"))]


def falla(v, f, *a):
    try:
        f(*a)
    except RuntimeError as ex:
        return str(ex)
    raise AssertionError("se esperaba RuntimeError de %s" % f.__name__)


def niveles_de(avisos):
    return [a["nivel"] for a in avisos]


def correr(v):
    pedir0, nivel0 = v.pedir, v.nivel
    Red.v = v
    try:
        _correr(v)
    finally:
        v.pedir, v.nivel = pedir0, nivel0
        v.LIVERPOOL_LEIDAS.clear()
        if hasattr(v, "razon") and getattr(v.razon, "_de_prueba", False):
            del v.razon
    print("casos_confirmacion OK")


def _correr(v):
    prods = v.cargar_config()
    fold, ps = prods[0], next(p for p in prods if p["id"] == "ps5")
    por_id = {p["id"]: p for p in prods}
    # topes de demostracion para que el Fold8 real de Liverpool ($44,999) de nivel de aviso
    fold_demo = json.loads(json.dumps(fold))
    fold_demo["topes_fijos"] = {"normal-512": [45000, 46000, 47000]}
    por_demo = {"fold8": fold_demo, "ps5": ps}

    # --- 1. Liverpool lee quien vende en la ficha; el Portal y el tercero no se publican por ninguna via
    # La unica que entra es la PS5 Digital que Liverpool titula sin "digital" (su ficha: Fija, Sin lector)
    for mapa in (LIV_OK, LIV_PORTAL_6999):
        v.pedir = Red(mapa)
        out = v.leer_liverpool(ps, ps["tiendas"]["Liverpool"], {})
        assert [(o["url"], o["var"], o["precio"], o["vendedor"]) for o in out] == [
            (LIV + "/pdp/p/1186785807?skuId=1186785807", "digital", 12499.0, "Liverpool")], out
        assert out[0]["titulo"] == "Consola PS5 de 825 GB edición bundle Blanco Digital (sin lector de disco)", out[0]["titulo"]
        for pid in ("1207352230", "1156059630", "1186785793"):   # tercero, Portal y PS5 con lector: se leyeron y no entran
            assert any("pdp/p/" + pid in u for u in v.pedir.pedidas), "no abrio la ficha %s" % pid
    v.pedir = Red(LIV_PORTAL_6999)
    fi = v.ficha_liverpool(LIV + "/pdp/p/1156059630")
    assert fi["titulo"] == "Consola PS5 Blanco" and fi["precio"] == 6999.0 and v.variante(ps, fi["titulo"]) is None, fi
    v.pedir = Red(LIV_OK)
    fi = v.ficha_liverpool(LIV + "/pdp/p/1186785793")
    t = v.titulo_con_atributos(ps, "Liverpool", fi["titulo"], fi["atributos"])   # reglas de productos.json (ps5)
    assert t.endswith(" con lector de disco") and "igital" not in t, t
    agrega = lambda attrs: v.titulo_con_atributos(ps, "Liverpool", "X", attrs)[1:]
    assert agrega({"Tipo de consola": "Fija/portátil", "Lector integrado": "Sin lector"}) == ""
    assert agrega({"Lector integrado": "Sin lector"}) == ""
    fi = v.ficha_liverpool(LIV + "/pdp/p/1156059630")
    assert fi["titulo"] == "Consola PS5 Blanco" and fi["precio"] == 5999.0, fi
    assert fi["vendedor"] == "Liverpool" and fi["existencia"] is True, fi
    assert v.variante(ps, fi["titulo"]) is None
    fi = v.ficha_liverpool(LIV + "/pdp/p/1207352230")
    assert fi["vendedor"] == "PATISH" and fi["precio"] == 8999.0, fi
    assert v.oferta(ps, "Liverpool", fi["titulo"], fi["precio"], "u", fi["vendedor"]) is None   # tercero fuera
    v.pedir = Red(LIV_SIN_FICHAS)
    msg = falla(v, v.leer_liverpool, ps, ps["tiendas"]["Liverpool"], {})
    assert "ninguna" in msg, msg     # sin ficha leida no se publica nada (y la tienda cuenta como no leida)

    # --- 2. Liverpool Fold8: precio de la variante exacta, vendedor de la ficha, sin la nota vieja
    v.pedir = Red(LIV_OK)
    out = v.leer_liverpool(fold, fold["tiendas"]["Liverpool"], {})
    assert len(out) == 3 and all(o["var"] == "normal-512" and o["precio"] == 44999.0 and o["vendedor"] == "Liverpool"
                                 for o in out), out
    assert all("skuId=" in o["url"] and not o["nota"] for o in out), out
    assert any("Lavanda" in o["titulo"] for o in out), out
    lavanda = next(o for o in out if "Lavanda" in o["titulo"])

    # --- 3. confirmar: las tres salidas
    v.pedir = Red(LIV_OK)
    r, final, razon = v.confirmar(dict(lavanda, precio=44000.0), fold_demo, {})
    assert r == "confirmada" and final["precio"] == 44999.0 and final["vendedor"] == "Liverpool", (r, final, razon)
    portal = {"prod": "ps5", "var": "digital", "nombre": "PS5 Digital (sin lector)", "tienda": "Liverpool", "precio": 5999.0,
              "url": LIV + "/pdp/p/1156059630", "vendedor": "Liverpool", "nota": "", "titulo": "Consola PS5 Digital"}
    r, final, razon = v.confirmar(portal, ps, {})
    assert r == "no_coincide" and "$5,999" in razon and "otro artículo" in razon, (r, razon)
    tercero = dict(portal, url=LIV + "/pdp/p/1207352230", precio=8999.0, titulo="Consola PS5 Digital edicion limitada Wolverine")
    r, final, razon = v.confirmar(tercero, ps, {})
    assert r == "no_coincide" and "PATISH" in razon, (r, razon)
    v.pedir = Red(LIV_SIN_FICHAS)
    r, final, razon = v.confirmar(portal, ps, {})
    assert r == "sin_releer" and "503" in razon and final is portal, (r, razon)
    r, _, razon = v.confirmar(portal, ps, {"_confirmaciones": v.TOPE_CONFIRMAR})
    assert (r, razon) == ("sin_releer", "sin tiempo"), (r, razon)
    r, _, razon = v.confirmar(dict(portal, tienda="Walmart"), ps, {})
    assert (r, razon) == ("sin_releer", "esta tienda no deja releer la ficha"), (r, razon)
    r, _, razon = v.confirmar(dict(portal, foro=True), ps, {})
    assert r == "sin_releer", r
    # tope: 6 relecturas por corrida, las mas baratas primero; la septima sale "sin tiempo"
    v.pedir = Red(LIV_OK)
    est = {}
    rs = [v.confirmar(dict(lavanda, precio=44000.0 + i), fold_demo, est)[0] for i in range(7)]
    assert rs == ["confirmada"] * 6 + ["sin_releer"], rs

    # --- 4. el Portal y el tercero, por las tres salidas, nunca salen GANGA ni COMPRAR
    for mapa in (LIV_OK, LIV_SIN_FICHAS):
        v.pedir = Red(mapa)
        for o in (portal, tercero):
            avs = v.avisos_de_precio({"Liverpool|ps5|digital": dict(o)}, por_id, {}, {}, AHORA)
            assert len(avs) == 1, avs       # la confirmacion nunca se traga el aviso
            if mapa is LIV_OK:
                assert niveles_de(avs) == ["SIN CONFIRMAR"] and avs[0]["asunto"].startswith("SIN CONFIRMAR $"), avs[0]["asunto"]
    # ...y por la via real (lector + confirmacion) ni siquiera llegan a la tabla:
    for mapa in (LIV_OK, LIV_SIN_FICHAS):
        v.pedir = Red(mapa)
        try:
            ofs = v.leer_liverpool(ps, ps["tiendas"]["Liverpool"], {})
        except RuntimeError:
            ofs = []
        mejores = {"%s|%s|%s" % (o["tienda"], o["prod"], o["var"]): o for o in ofs}
        avs = v.avisos_de_precio(mejores, por_id, {}, {}, AHORA)
        assert not [a for a in avs if a["nivel"] in ("GANGA", "COMPRAR")], avs

    # --- 5. Amazon: con la ficha leida, el precio de la oferta es el de la ficha, no el de la busqueda
    v.pedir = Red([(r"amazon\.com\.mx/s\?k=", "amz_busca_ps5.html"), (r"/dp/B0FTP523RH", "amz_ficha_B0FTP523RH.html")])
    est = {}
    out = v._leer_amazon(ps, {"consultas": ["playstation+5+digital"]}, est, [])
    assert len(out) == 1 and out[0]["precio"] == 12499.0 and out[0]["vendedor"] == "Amazon México", out
    assert est["vendedor_amazon"]["B0FTP523RH"][0] == 8999.0      # lo recordado sigue atado al precio de busqueda
    fa = v.ficha_amazon("B0FTP523RH")
    assert fa["precio"] == 12499.0 and fa["existencia"] is True and fa["vendedor"] == "Amazon México", fa
    v.pedir = Red([(r"/dp/", (503, "x" * 1669))])
    r, _, razon = v.confirmar(dict(out[0], precio=8999.0), ps, {})
    assert r == "sin_releer" and "503" in razon, razon

    # --- 6. Elektra, Costco, Gameplanet y Samsung: fichas grabadas
    v.pedir = Red([(r"productId:1302051913", "ele_api_1302051913.json")])
    fe = v.ficha_elektra("https://www.elektra.mx/samsung-galaxy-z-fold8-12512gb-lavanda-1302051913/p")
    assert fe["vendedor"] == "Systore" and fe["precio"] == 26999.0 and fe["existencia"] is True, fe
    ele = {"prod": "fold8", "var": "normal-512", "nombre": "Fold8 512 GB", "tienda": "Elektra", "precio": 26999.0,
           "url": fe["url"], "vendedor": "Elektra", "nota": "", "titulo": fe["titulo"]}
    r, _, razon = v.confirmar(ele, fold_demo, {})
    assert r == "no_coincide" and "Systore" in razon, (r, razon)
    v.pedir = Red([(r"products/648324", "cos_api_648324.json")])
    fc = v.ficha_costco("https://www.costco.com.mx/Electronicos/x/p/648324")
    assert fc["precio"] == 37999.0 and fc["vendedor"].startswith("Costco") and fc["vigencia"] == "29 nov 23:59", fc
    v.pedir = Red([(r"gameplanet", "gp_pdp_fortnite.html")])
    fg = v.ficha_gameplanet("https://www.gameplanet.com/products/consola-playstation-5-digital-fortnite-flowering-playstation-5")
    assert fg["precio"] == 10999.99 and fg["vendedor"] == "Gameplanet" and fg["existencia"] is True, fg
    v.pedir = Red([(r"searchapi\.samsung", "sam_catalogo.json")])
    sam = {"prod": "fold8", "var": "normal-512", "nombre": "Fold8 512 GB", "tienda": "Samsung", "precio": 38999.0,
           "url": "https://www.samsung.com/mx/smartphones/galaxy-z-fold8/buy/", "vendedor": "Samsung",
           "nota": "con cupones BF1000 + FOLD8_20CLUB; sin cupon $40,799", "titulo": "Fold8 512 GB Lavanda"}
    sam_demo = json.loads(json.dumps(fold_demo))
    sam_demo["topes_fijos"] = {"normal-512": [39000, 40000, 41000]}
    r, final, _ = v.confirmar(sam, sam_demo, {})
    assert r == "confirmada" and final["precio"] == 38999.0, (r, final)    # manda el total del carrito, no la pagina
    assert "Lavanda" in final["titulo"], final["titulo"]                     # el catalogo no trae el color: se conserva
    a = v.texto_aviso(dict(final, nota=final["nota"] + "; promocion vigente hasta 2026-12-01"), sam_demo,
                      v.var_por_id(sam_demo, "normal-512"), "GANGA", "confirmada", "", {}, "07:05")
    assert "- Vigencia: hasta el 1 dic (lo dice la tienda)" in a[1] and "2. Teclea el cupón BF1000." in a[1], a[1]
    assert "- Sin cupones cuesta $40,799." in a[1] and "Ojo" not in a[1], a[1]   # debajo de los pasos, no es un paso
    assert a[1].index("Revisa que el total") < a[1].index("Sin cupones cuesta"), a[1]
    assert v.vigencia_de({"vigencia": "29 nov 23:59"}) == "hasta el 29 nov 23:59"
    assert v.vigencia_de({"vigencia": "2026-12-01T23:59"}) == "hasta el 1 dic 23:59"

    # --- 7. memoria de lo avisado: compatible con lo viejo {clave: precio}
    clave = "Liverpool|fold8|normal-512"
    v.pedir = Red(LIV_OK)
    assert v.avisos_de_precio({clave: dict(lavanda)}, por_demo, {clave: 44999.0}, {}, AHORA) == []
    assert v.avisos_de_precio({clave: dict(lavanda)}, por_demo, {clave: 45100.0}, {}, AHORA) == []     # bajo < $300
    v.pedir = Red(LIV_OK)
    avs = v.avisos_de_precio({clave: dict(lavanda)}, por_demo, {clave: 45500.0}, {}, AHORA)
    assert niveles_de(avs) == ["GANGA"] and avs[0]["resultado"] == "confirmada", avs
    # avisado como SIN CONFIRMAR y luego confirmado al mismo precio: vuelve a avisar una sola vez.
    # avisado queda en numeros (lo que lee la version d6ab147: vuelta atras segura); el nivel, en avisado_nivel.
    estado = {"avisado": {clave: 44999.0}, "avisado_nivel": {clave: "SIN CONFIRMAR"}}
    avisado, niveles = v.normalizar_avisado(estado)
    abiertos = []
    for _ in range(2):
        v.pedir = Red(LIV_OK)
        for av in v.avisos_de_precio({clave: dict(lavanda)}, por_demo, avisado, estado, AHORA):
            v.publicar_aviso(av, avisado, False, False, abrir=lambda a, c: abiertos.append(a), niveles=niveles)
    assert len(abiertos) == 1 and abiertos[0].startswith("GANGA $44,999"), abiertos
    assert estado["avisado"] == {clave: 44999.0} and estado["avisado_nivel"] == {clave: ["GANGA", 44999.0]}, estado
    assert all(isinstance(x, (int, float)) for x in estado["avisado"].values()), estado
    # lo que haya quedado escrito con el formato de diccionario de la primera version se pasa a numeros
    viejo = {"avisado": {clave: {"precio": 44999.0, "nivel": "SIN CONFIRMAR"}, "Amazon|ps5|digital": 9000.0},
             "avisado_nivel": {"Costco|ps5|digital": "GANGA"}}
    v.normalizar_avisado(viejo)
    assert viejo == {"avisado": {clave: 44999.0, "Amazon|ps5|digital": 9000.0},
                     "avisado_nivel": {clave: ["SIN CONFIRMAR", 44999.0]}}, viejo
    # y avisos_de_precio tambien lee el diccionario si llegara sin normalizar
    v.pedir = Red(LIV_OK)
    avs = v.avisos_de_precio({clave: dict(lavanda)}, por_demo, {clave: {"precio": 44999.0, "nivel": "SIN CONFIRMAR"}}, {}, AHORA)
    assert niveles_de(avs) == ["GANGA"], avs
    # el listado decia GANGA y la ficha da COMPRAR: manda la ficha
    medio = json.loads(json.dumps(fold_demo))
    medio["topes_fijos"] = {"normal-512": [44000, 46000, 47000]}
    v.pedir = Red(LIV_OK)
    avs = v.avisos_de_precio({clave: dict(lavanda, precio=43000.0)}, {"fold8": medio, "ps5": ps}, {}, {}, AHORA)
    assert niveles_de(avs) == ["COMPRAR"] and "El listado lo ponía como GANGA" in avs[0]["cuerpo"], avs

    # --- 8. SIN_AVISOS_PRECIO: no se abre ni se marca
    avisado, abiertos = {}, []
    v.pedir = Red(LIV_OK)
    for av in v.avisos_de_precio({clave: dict(lavanda)}, por_demo, avisado, {}, AHORA):
        assert v.publicar_aviso(av, avisado, False, True, abrir=lambda a, c: abiertos.append(a)) is False
    assert abiertos == [] and avisado == {}, (abiertos, avisado)

    # --- 9. texto del aviso de cada salida: liga sola, vendedor, precio, pasos; sin tecnicismos
    v.pedir = Red(LIV_OK)
    conf = v.avisos_de_precio({clave: dict(lavanda)}, por_demo, {}, {}, AHORA)[0]
    nc = v.avisos_de_precio({"Liverpool|ps5|digital": dict(portal)}, por_id, {}, {}, AHORA)[0]
    v.pedir = Red(LIV_SIN_FICHAS)
    sr = v.avisos_de_precio({clave: dict(lavanda)}, por_demo, {}, {}, AHORA)[0]
    assert conf["asunto"] == "GANGA $44,999 · Fold8 512 GB · Liverpool", conf["asunto"]
    assert "Confirmado: se releyó la ficha a las 07:05 y coincide." in conf["cuerpo"], conf["cuerpo"]
    assert nc["asunto"] == "SIN CONFIRMAR $5,999 · PS5 Digital (sin lector) · Liverpool · abre la ficha antes de creerlo", nc["asunto"]
    assert nc["cuerpo"].startswith("Sin confirmar: el listado decía $5,999; la ficha dice $5,999, vendido por Liverpool"), nc["cuerpo"]
    assert "- Precio que se vio: **$5,999**" in nc["cuerpo"] and "Pagas" not in nc["cuerpo"], nc["cuerpo"]
    assert sr["asunto"] == "GANGA $44,999 · Fold8 512 GB · Liverpool · ficha sin releer", sr["asunto"]
    assert "No se pudo releer la ficha (la tienda no la entregó: Liverpool respondio 503" in sr["cuerpo"], sr["cuerpo"]
    for a in (conf, nc, sr):
        c = a["cuerpo"]
        assert ("\n%s\n" % a["o"]["url"]) in c, ("liga no va sola en su renglon", c)
        precio = "- Precio que se vio: **$" if a is nc else "- Pagas: **$"
        assert "lo vende " + a["o"]["vendedor"] in c and precio in c and "1. Abre la liga de abajo." in c, c
        assert c.index(precio) < c.index("| Producto | Tienda | Precio |"), "la tabla va abajo"
        assert not re.search(r"\b(SKU|ASIN|issue|ld\+json)\b", c, re.I), c
        assert "Vigencia: no se sabe" in c, c
    # pasos con cupones: la lista de la linea B o, mientras no exista, la nota
    pasos = v.pasos_compra({"precio": 38999.0, "nota": "", "cupones": ["BF1000", "FOLD8_20CLUB"]})
    assert pasos[1:3] == ["Teclea el cupón BF1000.", "Teclea el cupón FOLD8_20CLUB."], pasos
    pasos = v.pasos_compra(sam)
    assert pasos == ["Abre la liga de abajo.", "Teclea el cupón BF1000.", "Teclea el cupón FOLD8_20CLUB.",
                     "Revisa que el total sea $38,999 antes de pagar."], pasos
    assert v.notas_compra(sam) == ["Sin cupones cuesta $40,799."], v.notas_compra(sam)
    assert v.notas_compra({"nota": "preventa; sin cupon $9,999"}) == ["Sin cupones cuesta $9,999.", "Ojo: preventa."]
    # renglon "Por qué" de la linea A: se usa si existe y nunca tumba el aviso
    def razon_ok(precio, prod, var):
        return "es el menor precio visto en 90 dias"
    razon_ok._de_prueba = True
    v.razon = razon_ok
    a = v.texto_aviso(dict(lavanda), fold_demo, v.var_por_id(fold_demo, "normal-512"), "GANGA", "confirmada", "", {}, "07:05")
    assert "- Por qué: es el menor precio visto en 90 dias" in a[1], a[1]
    def razon_mala(precio, prod, var):
        raise ValueError("x")
    razon_mala._de_prueba = True
    v.razon = razon_mala
    a = v.texto_aviso(dict(lavanda), fold_demo, v.var_por_id(fold_demo, "normal-512"), "GANGA", "confirmada", "", {}, "07:05")
    assert "Por qué" not in a[1]
    del v.razon
    # la tabla: tres columnas, quien vende debajo
    t = v.tabla({clave: lavanda})
    assert t.splitlines()[0] == "| Producto | Tienda | Precio |" and "- Liverpool, Fold8 512 GB: lo vende Liverpool" in t, t

    # --- 10. SIN CONFIRMAR de la linea A (nivel()) en precio, foro y vuelta a la venta
    nivel_real = v.nivel
    v.nivel = lambda precio, prod, var: "SIN CONFIRMAR"
    v.pedir = Red(LIV_OK)
    avs = v.avisos_de_precio({clave: dict(lavanda)}, por_demo, {}, {}, AHORA)
    assert niveles_de(avs) == ["SIN CONFIRMAR"] and avs[0]["resultado"] == "confirmada", avs
    assert avs[0]["asunto"].startswith("SIN CONFIRMAR $44,999") and "Aun así" in avs[0]["cuerpo"], avs[0]
    assert avs[0]["cuerpo"].startswith("Sin confirmar: el precio está muy por debajo"), avs[0]["cuerpo"]
    it = {"titulo": "PS5 Digital a $6,999 en Walmart", "link": "https://www.promodescuentos.com/ofertas/x", "guid": "g",
          "fecha": "Thu, 26 Nov 2026 10:00:00 +0000", "descripcion": "Consola PS5 Digital", "tienda": "Walmart", "precio_txt": "$6,999"}
    af = v.aviso_foro(prods, dict(it, titulo="Consola PS5 Digital 825 GB a $6,999"))
    assert af and af[0].startswith("[foro] SIN CONFIRMAR $6,999"), af
    assert af[1].startswith("Sin confirmar:") and "- Precio que se vio: **$6,999**" in af[1], af[1]
    v.nivel = lambda precio, prod, var: "GANGA"
    af = v.aviso_foro(prods, dict(it, titulo="Consola PS5 Digital 825 GB a $6,999"))
    assert af[0] == "[foro] GANGA $6,999 · PS5 Digital (sin lector) · Walmart · ficha sin releer", af[0]
    assert "\nhttps://www.promodescuentos.com/ofertas/x\n" in af[1] and "1. Abre la publicación" in af[1], af[1]
    v.nivel = lambda precio, prod, var: "SIN CONFIRMAR"
    est = {"conocidos": {clave: {"producto": "Fold8 512 GB", "precio": 44999, "url": "u", "visto": "h"}},
           "agotados": {clave: {"desde": "desde h", "precio": 44999}}}
    antes = json.dumps(est, sort_keys=True)
    abiertos = []
    av = v.avisar_disponibilidad(est, prods, {clave: dict(lavanda)}, {"Liverpool"}, "h",
                                 abrir=lambda a, c: abiertos.append(a), callar=True)
    assert len(av) == 1 and abiertos == [] and json.dumps(est, sort_keys=True) == antes, (av, abiertos, est)  # callado
    av = v.disponibilidad(est, prods, {clave: dict(lavanda)}, {"Liverpool"}, "h")
    assert len(av) == 1 and "SIN CONFIRMAR" in av[0][0] and "abre la ficha antes de creerlo" in av[0][0], av
    assert av[0][2] == clave, av                   # disponibilidad devuelve la clave junto con asunto y cuerpo
    # agotado callado: ni agotados ni ausente cambian
    est = {"conocidos": {clave: {"producto": "Fold8 512 GB", "precio": 44999, "url": "u", "visto": "h"}},
           "ausente": {clave: v.AUSENCIAS_AGOTADO - 1}, "agotados": {}}
    antes = json.dumps(est, sort_keys=True)
    av = v.avisar_disponibilidad(est, prods, {}, {"Liverpool"}, "h", abrir=lambda a, c: abiertos.append(a), callar=True)
    assert av[0][0].startswith("Agotado") and av[0][2] == clave and abiertos == [], av
    assert json.dumps(est, sort_keys=True) == antes, est
    v.nivel = nivel_real

    # --- 11. contrato de lectores nuevos: FICHAS (tienda -> funcion(url) -> diccionario de ficha o RuntimeError)
    modo = {"m": "ok"}

    def ficha_x(url):
        assert url == "https://tiendax.mx/p/77", url
        if modo["m"] == "cae":
            raise RuntimeError("TiendaX respondio 503")
        if modo["m"] == "rota":
            return {"titulo": "Consola PS5 Digital"}
        return {"titulo": "Consola PlayStation 5 Digital 825 GB", "precio": 8500.0, "existencia": True,
                "importado": False, "vendedor": "TiendaX" if modo["m"] == "ok" else "Revendedor MX"}
    v.FICHAS["TiendaX"] = ficha_x
    v.VENDEDOR_OFICIAL["TiendaX"] = r"^\s*tiendax\s*$"
    try:
        ox = {"prod": "ps5", "var": "digital", "nombre": "PS5 Digital (sin lector)", "tienda": "TiendaX", "precio": 8400.0,
              "url": "https://tiendax.mx/p/77", "vendedor": "TiendaX", "nota": "", "titulo": "Consola PS5 Digital"}
        r, final, _ = v.confirmar(ox, ps, {})
        assert r == "confirmada" and final["precio"] == 8500.0 and final["vendedor"] == "TiendaX", (r, final)
        modo["m"] = "tercero"
        r, _, razon = v.confirmar(ox, ps, {})
        assert r == "no_coincide" and "Revendedor MX" in razon, (r, razon)
        modo["m"] = "cae"
        r, _, razon = v.confirmar(ox, ps, {})
        assert r == "sin_releer" and "503" in razon, (r, razon)
        modo["m"] = "rota"
        r, _, razon = v.confirmar(ox, ps, {})
        assert (r, razon) == ("sin_releer", "la ficha llegó incompleta"), (r, razon)
    finally:
        v.FICHAS.pop("TiendaX", None)
        v.VENDEDOR_OFICIAL.pop("TiendaX", None)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(AQUI))
    import vigia
    correr(vigia)
