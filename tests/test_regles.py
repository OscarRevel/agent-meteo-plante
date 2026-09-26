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
        self.assertEqual(cats(mp.evaluate(fake(overrides={28: {"temperature_2m": 11.5}}), NOW)), {"froid": 1})
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

    def test_pluie_prolongee(self):
        ov = {h: {"precipitation": 0.5} for h in range(18, 25)}  # 7 h de pluie
        self.assertEqual(cats(mp.evaluate(fake(overrides=ov), NOW)), {"pluie": 1})
        self.assertEqual(cats(mp.evaluate(fake(phours=(0, 4, 5, 0)), NOW)), {"pluie": 1})

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


def vigi(dept_items, period_end="2026-10-06T22:00:00Z"):
    """Carte de vigilance minimale au format de l'API DPVigilance."""
    return {"product": {"periods": [{
        "echeance": "J", "begin_validity_time": "2026-10-04T22:00:00Z",
        "end_validity_time": period_end,
        "timelaps": {"domain_ids": [
            {"domain_id": "84", "phenomenon_items": [
                {"phenomenon_id": "3", "phenomenon_max_color_id": 4, "timelaps_items": []}]},
            {"domain_id": "13", "phenomenon_items": dept_items},
        ]}}]}}


class TestVigilance(unittest.TestCase):
    def test_vert(self):
        v = vigi([{"phenomenon_id": "3", "phenomenon_max_color_id": 1, "timelaps_items": []}])
        self.assertEqual(mp.evaluate_vigilance(v, NOW), [])

    def test_autre_departement_ignore(self):
        self.assertEqual(mp.evaluate_vigilance(vigi([]), NOW), [])

    def test_orange_orages_a_venir(self):
        v = vigi([{"phenomenon_id": "3", "phenomenon_max_color_id": 3, "timelaps_items": [
            {"begin_time": "2026-10-05T10:00:00Z", "end_time": "2026-10-05T16:00:00Z", "color_id": 1},
            {"begin_time": "2026-10-05T16:00:00Z", "end_time": "2026-10-06T04:00:00Z", "color_id": 3}]}])
        t = mp.evaluate_vigilance(v, NOW)
        self.assertEqual([(x["cat"], x["level"]) for x in t], [("vigilance", 2)])
        self.assertIn("Orages", t[0]["msg"])
        self.assertIn("dès", t[0]["msg"])

    def test_jaune_selon_phenomene(self):
        v = vigi([{"phenomenon_id": "1", "phenomenon_max_color_id": 2, "timelaps_items": []},
                  {"phenomenon_id": "6", "phenomenon_max_color_id": 2, "timelaps_items": []},
                  {"phenomenon_id": "4", "phenomenon_max_color_id": 3, "timelaps_items": []}])
        t = mp.evaluate_vigilance(v, NOW)
        self.assertEqual(len(t), 1)             # vent jaune oui ; canicule jaune non ; inondation ignorée
        self.assertIn("Vent violent", t[0]["msg"])

    def test_rouge_critique(self):
        v = vigi([{"phenomenon_id": "7", "phenomenon_max_color_id": 4, "timelaps_items": []}])
        self.assertEqual(mp.evaluate_vigilance(v, NOW)[0]["level"], 3)

    def test_episode_termine_ignore(self):
        v = vigi([{"phenomenon_id": "3", "phenomenon_max_color_id": 3, "timelaps_items": [
            {"begin_time": "2026-10-05T02:00:00Z", "end_time": "2026-10-05T08:00:00Z", "color_id": 3}]}])
        self.assertEqual(mp.evaluate_vigilance(v, NOW), [])

    def test_json_incomplet(self):
        self.assertEqual(mp.evaluate_vigilance({}, NOW), [])


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

    def test_pas_de_ressortir_si_vigilance_indisponible(self):
        s = dict(mp.DEFAULT_STATE)
        mp.decide(s, [{"cat": "vigilance", "level": 2, "msg": "x"}])
        for _ in range(5):
            self.assertEqual(mp.decide(s, [], can_clear=False), [])
        self.assertTrue(s["inside"])


if __name__ == "__main__":
    unittest.main()
