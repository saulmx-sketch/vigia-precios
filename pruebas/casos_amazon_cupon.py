"""Cupon de ficha de Amazon apagado (RESTAR_CUPON_FICHA_AMAZON = False; revision R7, hallazgo H2, 25 nov 2026).

Cinco corridas de vigia.main() sin red sobre el mismo estado (arnes de casos_corrida.py): la ficha grabada B0FTP523RH
con su precio a pagar llevado a $7,400 (GANGA de la PS5 Digital) y un cupon de ficha de $1,000 (recorte de una pagina
real del 25 nov 2026); la busqueda grabada no trae cupon. Corridas 1 a 3 con la misma busqueda (memoria
vendedor_amazon), 4 y 5 con la busqueda $10 abajo (se relee la ficha). Se espera: un solo aviso en las cinco, el precio
de la tabla siempre el de la ficha a secas y la nota con el cupon sin restar. Con el cupon restado el precio oscilaba
entre lectura y memoria y el GANGA se repetia cada vez que la busqueda se movia.
Uso:  py -X utf8 pruebas\\casos_amazon_cupon.py   (o vigia.py --selftest, via correr(v))
"""
import importlib.util
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
CLAVE = "Amazon|ps5|digital"
CUPON = ('<span id="couponTextpctch18" class="couponLabelText"> Aplica el cup&oacute;n de $1,000.00 '
         '<span class="a-declarative" data-a-modal="{&quot;url&quot;:&quot;/promotion/details/popup/A29HLK1CVST1ZX'
         '&quot;}">T&eacute;rminos</span></span>')


def _arnes():
    spec = importlib.util.spec_from_file_location("prueba_arnes_casos_corrida", os.path.join(AQUI, "casos_corrida.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ficha(cc, pesos, centavos):
    t = cc.lee("amz_ficha_B0FTP523RH.html")
    m = re.search(r'apex-pricetopay-value.{0,800}?a-price-whole">([\d,]+).{0,200}?a-price-fraction">(\d\d)', t, re.S)
    assert m, "la ficha grabada ya no trae el precio a pagar"
    t = t[:m.start(1)] + pesos + t[m.end(1):m.start(2)] + centavos + t[m.end(2):]
    return t + CUPON


def correr(v):
    if getattr(v, "RESTAR_CUPON_FICHA_AMAZON", True):
        print("casos_amazon_cupon: el cupon de ficha esta prendido; nada que probar (escena saltada)")
        return
    cc = _arnes()
    prosp = getattr(v, "PROSPECTIVA", None)
    prosp_antes = dict(prosp) if isinstance(prosp, dict) else None
    if prosp_antes is not None:
        prosp.clear()   # topes por fracciones de productos.json, como en selftest()
    b = cc.Banco(v)
    try:
        ficha = _ficha(cc, "7,400", "00")
        busca = cc.lee("amz_busca_ps5.html")
        otra = busca.replace("$8,999.00", "$8,989.00")
        assert otra != busca, "la busqueda grabada ya no trae $8,999.00"
        avisos, precios, notas = [], [], []
        for i, texto in enumerate([busca, busca, busca, otra, otra], 1):
            m = cc.mundo_base()
            m[r"amazon\.com\.mx/s\?k="] = (200, texto)
            m[r"amazon\.com\.mx/dp/B0FTP523RH"] = (200, ficha)
            c, avs, srv, _ = b.correr(m)
            assert c == 0, ("corrida %d termino con %s" % (i, c))
            fila = b.json(b.precios).get(CLAVE) or {}
            precios.append(fila.get("precio"))
            notas.append(fila.get("nota") or "")
            avisos += [(i, a, cu) for a, cu in avs if "Amazon" in a]
        assert precios == [7400.0] * 5, ("precio de Amazon en la tabla", precios)
        assert [(i, a) for i, a, _ in avisos] == [(1, "GANGA $7,400 · PS5 Digital (sin lector) · Amazon")], \
            ("avisos de Amazon en 5 corridas", [(i, a) for i, a, _ in avisos])
        assert v.NOTA_CUPON_SIN_RESTAR in notas[0] and "Confirmado" in avisos[0][2], (notas[0], avisos[0][2][:600])
        assert "cupon en la ficha, sin restar" in avisos[0][2], avisos[0][2][:600]
        print("casos_amazon_cupon OK: 5 corridas, 1 aviso (GANGA $7,400 confirmado), tabla estable en $7,400, "
              "nota: %s" % notas[0])
    finally:
        b.borrar()
        if prosp_antes is not None:
            prosp.clear()
            prosp.update(prosp_antes)


if __name__ == "__main__":
    sys.path.insert(0, RAIZ)
    import vigia
    correr(vigia)
