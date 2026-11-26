"""Claves que ningun lector puede volver a producir van a `olvidar` (25 nov 2026, issue 12: "Agotado: Fold8 (capacidad
sin dato) ... en Liverpool" era el mismo telefono, que la lectura por ficha nombra "Fold8 512 GB").

Sin red ni archivos vivos: arranca de pruebas\\respuestas\\visto_olvidar_2026-11-26.json (congelado) con el arnes de
casos_corrida.py (servidor falso, carpeta temporal). Con el `olvidar` de productos.json: varias corridas sin ningun
"Agotado" de esas claves, que desaparecen del estado, y la clave nueva sigue conocida y en la tabla. Sin el `olvidar`
(copia de productos.json sin el campo): el "Agotado" si sale, para que la prueba muerda.
Uso:  py -X utf8 pruebas\\casos_olvidar.py   (o vigia.py --selftest, via correr(v))
"""
import importlib.util
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
VIEJAS = ["Liverpool|fold8|normal", "Liverpool|fold8|ultra"]
NUEVA = "Liverpool|fold8|normal-512"


def _arnes():
    spec = importlib.util.spec_from_file_location("prueba_arnes_corrida", os.path.join(AQUI, "casos_corrida.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _banco(cc, v, con_olvidar):
    with open(os.path.join(AQUI, "respuestas", "visto_olvidar_2026-11-26.json"), encoding="utf-8") as f:
        visto = json.load(f)
    b = cc.Banco(v, visto=visto)
    with open(b.config, encoding="utf-8") as f:
        cfg = json.load(f)
    fold = next(p for p in cfg["productos"] if p["id"] == "fold8")
    if not con_olvidar:
        fold.pop("olvidar", None)
    b.escribe(b.config, json.dumps(cfg, ensure_ascii=False))
    return b, fold.get("olvidar", [])


def correr(v):
    cc = _arnes()
    prosp = getattr(v, "PROSPECTIVA", None)
    prosp_antes = dict(prosp) if isinstance(prosp, dict) else None
    try:
        if prosp_antes is not None:
            prosp.clear()
        b, olvidar = _banco(cc, v, True)
        try:
            assert all(k in olvidar for k in VIEJAS), ("productos.json: fold8 sin el olvidar de", VIEJAS, olvidar)
            for i in range(v.AUSENCIAS_AGOTADO + 1):
                c, avs, srv, _ = b.correr(cc.mundo_base())
                ag = [a for a, _ in avs if a.startswith("Agotado")]
                assert c == 0 and not ag, ("con olvidar salio un Agotado", i, ag)
                est = b.json()
                for k in VIEJAS:
                    for sec in ("conocidos", "ausente", "agotados"):
                        assert k not in est.get(sec, {}), ("la clave vieja sigue en el estado", i, sec, k)
                assert NUEVA in est["conocidos"], ("olvidar borro la clave nueva", est["conocidos"].keys())
                tabla = b.json(b.precios)
                assert tabla.get(NUEVA, {}).get("precio") == 44999.0, ("la clave nueva no esta en la tabla", tabla.get(NUEVA))
        finally:
            b.borrar()
        b, _ = _banco(cc, v, False)
        try:
            c, avs, srv, _ = b.correr(cc.mundo_base())
            ag = sorted(a for a, _ in avs if a.startswith("Agotado"))
            assert ag == ["Agotado: Fold8 (capacidad sin dato) ya no esta a la venta en Liverpool",
                          "Agotado: Fold8 Ultra (capacidad sin dato) ya no esta a la venta en Liverpool"], \
                ("sin olvidar no salio el Agotado: la prueba no muerde", ag)
        finally:
            b.borrar()
    finally:
        if prosp_antes is not None:
            prosp.clear()
            prosp.update(prosp_antes)
    print("casos_olvidar OK: con olvidar, %d corridas sin Agotado y las claves viejas fuera del estado; la nueva sigue "
          "(tabla $44,999); sin olvidar, salen los 2 Agotado" % (v.AUSENCIAS_AGOTADO + 1))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(AQUI))
    import vigia
    correr(vigia)
