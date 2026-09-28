"""Tests des règles d'alerte avec des prévisions synthétiques (sans réseau).

Lancer : python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import meteo_plante as mp  # noqa: E402

NOW = datetime(2026, 10, 5, 15, 17, tzinfo=mp.TZ)


def fake(temp=18.0, rh=60, prec=0.0, code=1, gust=20.0, cape=0.0,
         tmax=(24, 24, 24, 24), phours=(0, 0, 0, 0), overrides=None):
    """96 h de prévisions constantes à partir de minuit, avec surcharges {heure: {clé: val}}."""
    start = NOW.replace(hour=0, minute=0)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(96)]
    h = {"time": times,
         "temperature_2m": [temp] * 96, "relative_humidity_2m": [rh] * 96,
         "precipitation": [prec] * 96, "weather_code": [code] * 96,
         "wind_gusts_10m": [gust] * 96, "cape": [cape] * 96}
    for hour, vals in (overrides or {}).items():
        for k, v in vals.items():
            h[k][hour] = v
    return {"hourly": h, "daily": {"temperature_2m_max": list(tmax),
                                   "precipitation_hours": list(phours)}}


def cats(trigs):
    return {t["cat"]: t["level"] for t in trigs}


class TestRegles(unittest.TestCase):
    def test_calme(self):
        self.assertEqual(mp.evaluate(fake(), NOW), [])

    def test_froid_niveaux(self):
        # heure 28 = demain 04h (dans la fenêtre de 30 h)
        self.assertEqual(mp.evaluate(fake(overrides={28: {"temperature_2m": 12.5}}), NOW), [])
        self.assertEqual(cats(mp.evaluate(fake(overrides={28: {"temperature_2m": 11.5}}), NOW)), {"froid": 2})
        self.assertEqual(cats(mp.evaluate(fake(overrides={28: {"temperature_2m": 9.0}}), NOW)), {"froid": 2})
        self.assertEqual(cats(mp.evaluate(fake(overrides={28: {"temperature_2m": 2.0}}), NOW)), {"froid": 3})

    def test_froid_hors_fenetre_ignore(self):
        # heure 60 = au-delà de 30 h -> pas encore d'alerte
        self.assertEqual(mp.evaluate(fake(overrides={60: {"temperature_2m": 5.0}}), NOW), [])

    def test_passe_ignore(self):
        # heure 3 = ce matin, déjà passé
        self.assertEqual(mp.evaluate(fake(overrides={3: {"temperature_2m": 5.0}}), NOW), [])

    def test_orage_grele_averse(self):
        c = cats(mp.evaluate(fake(overrides={20: {"weather_code": 95}}), NOW))
        self.assertEqual(c, {"orage": 2})
        c = cats(mp.evaluate(fake(overrides={20: {"weather_code": 99}}), NOW))
        self.assertEqual(c["grele"], 3)
        c = cats(mp.evaluate(fake(overrides={20: {"precipitation": 12.0}}), NOW))
        self.assertEqual(c["averse"], 2)
        c = cats(mp.evaluate(fake(overrides={20: {"precipitation": 2.0, "cape": 2000}}), NOW))
        self.assertEqual(c["orage"], 2)

    def test_pluie_faible_ignoree(self):
        ov = {h: {"precipitation": 0.5} for h in range(18, 30)}  # 12 h de pluie faible = 6 mm
        self.assertEqual(mp.evaluate(fake(overrides=ov), NOW), [])
        self.assertEqual(mp.evaluate(fake(phours=(0, 8, 10, 12)), NOW), [])  # plusieurs jours pluvieux

    def test_pluie_abondante(self):
        ov = {h: {"precipitation": 3.0} for h in range(18, 25)}  # 7 h à 3 mm/h = 21 mm
        self.assertEqual(cats(mp.evaluate(fake(overrides=ov), NOW)), {"pluie": 2})

    def test_humidite_fraiche(self):
        ov = {h: {"relative_humidity_2m": 90, "temperature_2m": 13.0} for h in range(22, 30)}
        self.assertEqual(cats(mp.evaluate(fake(overrides=ov), NOW)), {"humidite": 1})
        # 5 h seulement -> pas d'alerte
        ov = {h: {"relative_humidity_2m": 90, "temperature_2m": 13.0} for h in range(22, 27)}
        self.assertEqual(mp.evaluate(fake(overrides=ov), NOW), [])

    def test_vent(self):
        self.assertEqual(cats(mp.evaluate(fake(overrides={20: {"wind_gusts_10m": 50}}), NOW)), {"vent": 1})
        self.assertEqual(cats(mp.evaluate(fake(overrides={20: {"wind_gusts_10m": 65}}), NOW)), {"vent": 2})
        self.assertEqual(cats(mp.evaluate(fake(overrides={20: {"wind_gusts_10m": 90}}), NOW)), {"vent": 3})

    def test_chaleur(self):
        self.assertEqual(cats(mp.evaluate(fake(tmax=(30, 33, 34, 30)), NOW)), {"chaleur": 1})
        self.assertEqual(mp.evaluate(fake(tmax=(33, 30, 33, 30)), NOW), [])  # pas consécutif
        self.assertEqual(cats(mp.evaluate(fake(tmax=(36, 36, 30, 30)), NOW)), {"chaleur": 2})

    def test_valeurs_manquantes(self):
        d = fake()
        d["hourly"]["cape"] = [None] * 96
        d["hourly"]["temperature_2m"][20] = None
        self.assertEqual(mp.evaluate(d, NOW), [])


class TestEtat(unittest.TestCase):
    def test_cycle_rentrer_aggraver_ressortir(self):
        s = dict(mp.DEFAULT_STATE)
        froid = [{"cat": "froid", "level": 1, "msg": "x"}]
        gel = [{"cat": "froid", "level": 3, "msg": "x"}]
        self.assertEqual(mp.decide(s, froid), [("rentrer", 1)])
        self.assertEqual(mp.decide(s, froid), [])            # pas de spam
        self.assertEqual(mp.decide(s, gel), [("aggravation", 3)])
        self.assertEqual(mp.decide(s, gel), [])
        self.assertEqual(mp.decide(s, []), [])               # 1er passage calme : on attend
        self.assertEqual(mp.decide(s, froid), [])            # retour du risque : pas de faux "ressors"
        self.assertEqual(mp.decide(s, []), [])
        self.assertEqual(mp.decide(s, []), [("ressortir", 0)])
        self.assertFalse(s["inside"])



if __name__ == "__main__":
    unittest.main()
