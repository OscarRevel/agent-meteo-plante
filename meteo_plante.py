#!/usr/bin/env python3
"""Agent météo pour la plante - Aix-en-Provence.

Récupère les prévisions Météo-France (AROME/ARPEGE via Open-Meteo), applique
les seuils et envoie des alertes Telegram en logique "rentrer / ressortir".

Usage :
    python3 meteo_plante.py          # passage normal (planifié)
    python3 meteo_plante.py test     # envoie un message de test + l'état actuel

Variables d'environnement :
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  (obligatoires sauf en DRY_RUN)
    DRY_RUN=1                          affiche les messages au lieu de les envoyer
    STATE_FILE                         chemin du fichier d'état (défaut : state.json)
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
LAT, LON = 43.5297, 5.4474          # Aix-en-Provence
TZ = ZoneInfo("Europe/Paris")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
WINDOW_H = 30                        # horizon surveillé (heures)
CLEAR_RUNS_TO_EXIT = 2               # passages "tout va bien" avant de dire "ressors-la"
SUMMARY_HOURS = range(17, 22)        # le point du soir part au 1er passage entre 17h et 21h
NIGHT_HOURS = set(range(22, 24)) | set(range(0, 7))  # notifications silencieuses (sauf niveau >= 2)

SEUILS = {
    # 1. Froid
    "froid_attention": 12.0,   # (égal à froid_fort : pas de niveau Attention pour le froid)
    "froid_fort": 12.0,        # min < 12 °C -> niveau Fort
    "gel": 2.0,                # min <= 2 °C
    # 2. Pluie / humidité
    "averse_mm_h": 10.0,       # forte averse : >= 10 mm en 1 h
    "pluie_mm_fenetre": 20.0,  # pluie abondante : >= 20 mm cumulés dans la fenêtre
    "humidite_pct": 80,        # HR > 80 %...
    "humidite_temp_max": 15.0, # ...avec T < 15 °C...
    "humidite_heures": 6,      # ...pendant >= 6 h d'affilée
    # 3. Vent
    "rafale_attention": 45.0,
    "rafale_forte": 60.0,
    "tempete": 80.0,
    # 4. Chaleur (2 jours consécutifs)
    "chaleur": 32.0,
    "chaleur_forte": 35.0,
    # 5. Orage (énergie convective, si dispo)
    "cape_orage": 1500.0,
}
PLUIE_MIN_MM = 0.2                 # une heure compte comme "pluvieuse" au-delà
ORAGE_CODES = {95, 96, 99}         # codes WMO orage
GRELE_CODES = {96, 99}             # orage avec grêle
NIVEAUX = {1: "Attention", 2: "Fort", 3: "Critique"}

HOURLY = ["temperature_2m", "relative_humidity_2m", "precipitation",
          "weather_code", "wind_gusts_10m", "cape"]
DAILY = ["temperature_2m_min", "temperature_2m_max", "precipitation_sum",
         "precipitation_hours", "wind_gusts_10m_max"]


# --------------------------------------------------------------------------
# Données météo
# --------------------------------------------------------------------------
def _fetch(model=None):
    params = {
        "latitude": LAT, "longitude": LON,
        "hourly": ",".join(HOURLY), "daily": ",".join(DAILY),
        "timezone": "Europe/Paris", "forecast_days": 4, "wind_speed_unit": "kmh",
    }
    if model:
        params["models"] = model
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # réseau, HTTP 5xx...
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Open-Meteo injoignable : {last}")


def get_forecast():
    """Météo-France en priorité ; bascule sur le meilleur modèle si trou de données."""
    try:
        data = _fetch("meteofrance_seamless")
        temps = data.get("hourly", {}).get("temperature_2m") or []
        if temps and sum(v is None for v in temps[:WINDOW_H + 12]) <= 3:
            data["_source"] = "Météo-France (AROME/ARPEGE) via Open-Meteo"
            return data
    except RuntimeError:
        pass
    data = _fetch(None)
    data["_source"] = "Open-Meteo (meilleur modèle disponible)"
    return data


# --------------------------------------------------------------------------
# Évaluation des seuils
# --------------------------------------------------------------------------
def _hourly_rows(data, now):
    h = data["hourly"]
    n = len(h["time"])
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=WINDOW_H)
    rows = []
    for i, ts in enumerate(h["time"]):
        t = datetime.fromisoformat(ts).replace(tzinfo=TZ)
        if start <= t < end:
            row = {k: (h.get(k) or [None] * n)[i] for k in HOURLY}
            row["time"] = t
            rows.append(row)
    return rows


def _fmt_t(t):
    jours = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
    return f"{jours[t.weekday()]} {t:%Hh}"


def _vals(rows, key):
    return [(r[key], r["time"]) for r in rows if r[key] is not None]


def _longest_run(flags):
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def _consecutive_days(values, threshold):
    """True si deux jours consécutifs dépassent le seuil (>=)."""
    return any(a is not None and b is not None and a >= threshold and b >= threshold
               for a, b in zip(values, values[1:]))


def evaluate(data, now):
    """Retourne la liste des déclencheurs : {cat, level, msg}."""
    S = SEUILS
    rows = _hourly_rows(data, now)
    out = []

    # 1. Froid
    temps = _vals(rows, "temperature_2m")
    if temps:
        tmin, tt = min(temps, key=lambda x: x[0])
        lvl = 3 if tmin <= S["gel"] else 2 if tmin < S["froid_fort"] else 1 if tmin < S["froid_attention"] else 0
        if lvl:
            label = "Risque de gel" if lvl == 3 else "Froid"
            out.append({"cat": "froid", "level": lvl,
                        "msg": f"{label} : min prévue {tmin:.1f} °C ({_fmt_t(tt)})"})

    # 2a. Orage / grêle / forte averse
    codes = _vals(rows, "weather_code")
    grele = [t for c, t in codes if int(c) in GRELE_CODES]
    orage = [t for c, t in codes if int(c) in ORAGE_CODES]
    precs = _vals(rows, "precipitation")
    capes = {r["time"]: r["cape"] for r in rows if r["cape"] is not None}
    orage_cape = [t for p, t in precs if p >= 1.0 and capes.get(t, 0) >= S["cape_orage"]]
    if grele:
        out.append({"cat": "grele", "level": 3, "msg": f"Grêle possible ({_fmt_t(grele[0])})"})
    if orage or orage_cape:
        t0 = min(orage + orage_cape)
        out.append({"cat": "orage", "level": 2, "msg": f"Orage prévu ({_fmt_t(t0)})"})
    if precs:
        pmax, tp = max(precs, key=lambda x: x[0])
        if pmax >= S["averse_mm_h"]:
            out.append({"cat": "averse", "level": 2,
                        "msg": f"Forte averse : {pmax:.1f} mm/h ({_fmt_t(tp)})"})

    # 2b. Pluie abondante (la pluie faible ou courte est ignorée)
    if precs:
        wet_h = sum(1 for p, _ in precs if p >= PLUIE_MIN_MM)
        total = sum(p for p, _ in precs)
        if total >= S["pluie_mm_fenetre"]:
            out.append({"cat": "pluie", "level": 2,
                        "msg": f"Pluie abondante : {total:.0f} mm sur {WINDOW_H} h ({wet_h} h de pluie)"})
    d = data.get("daily", {})

    # 2c. Humidité + fraîcheur
    flags = [r["relative_humidity_2m"] is not None and r["temperature_2m"] is not None
             and r["relative_humidity_2m"] > S["humidite_pct"]
             and r["temperature_2m"] < S["humidite_temp_max"] for r in rows]
    run = _longest_run(flags)
    if run >= S["humidite_heures"]:
        out.append({"cat": "humidite", "level": 1,
                    "msg": f"Humidité > {S['humidite_pct']} % et < {S['humidite_temp_max']:.0f} °C pendant {run} h"})

    # 3. Vent / tempête
    gusts = _vals(rows, "wind_gusts_10m")
    if gusts:
        gmax, tg = max(gusts, key=lambda x: x[0])
        if gmax >= S["tempete"]:
            out.append({"cat": "vent", "level": 3, "msg": f"Tempête : rafales {gmax:.0f} km/h ({_fmt_t(tg)})"})
        elif gmax >= S["rafale_forte"]:
            out.append({"cat": "vent", "level": 2, "msg": f"Vent fort : rafales {gmax:.0f} km/h ({_fmt_t(tg)})"})
        elif gmax >= S["rafale_attention"]:
            out.append({"cat": "vent", "level": 1, "msg": f"Vent : rafales {gmax:.0f} km/h ({_fmt_t(tg)})"})

    # 4. Chaleur soutenue (2 jours consécutifs sur les 4 jours prévus)
    tmax = d.get("temperature_2m_max") or []
    if _consecutive_days(tmax, S["chaleur_forte"]):
        out.append({"cat": "chaleur", "level": 2, "msg": f"Forte chaleur : ≥ {S['chaleur_forte']:.0f} °C deux jours de suite"})
    elif _consecutive_days(tmax, S["chaleur"]):
        out.append({"cat": "chaleur", "level": 1, "msg": f"Chaleur : ≥ {S['chaleur']:.0f} °C deux jours de suite"})

    return sorted(out, key=lambda t: -t["level"])


# --------------------------------------------------------------------------
# Logique "rentrer / ressortir"
# --------------------------------------------------------------------------
DEFAULT_STATE = {"inside": False, "level": 0, "clear_runs": 0,
                 "last_summary": "", "fail_count": 0}


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return {**DEFAULT_STATE, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_STATE)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def decide(state, triggers):
    """Met à jour l'état et renvoie les événements à notifier."""
    level = max((t["level"] for t in triggers), default=0)
    events = []
    if triggers:
        state["clear_runs"] = 0
        if not state["inside"]:
            state["inside"] = True
            events.append(("rentrer", level))
        elif level > state["level"]:
            events.append(("aggravation", level))
        state["level"] = level
    elif state["inside"]:
        state["clear_runs"] += 1
        if state["clear_runs"] >= CLEAR_RUNS_TO_EXIT:
            state.update(inside=False, level=0, clear_runs=0)
            events.append(("ressortir", 0))
    return events


def _bullets(triggers):
    return "\n".join(f"• {t['msg']}" for t in triggers)


def format_event(kind, level, triggers):
    if kind == "rentrer":
        icon = "🚨" if level == 3 else "🪴"
        return (f"{icon} <b>Rentre la plante</b> (niveau {NIVEAUX[level]})\n\n"
                f"{_bullets(triggers)}\n\n"
                f"<i>Prochaines {WINDOW_H} h à Aix-en-Provence. Je te préviens quand tu pourras la ressortir.</i>")
    if kind == "aggravation":
        icon = "🚨" if level == 3 else "⚠️"
        return (f"{icon} <b>Alerte renforcée : niveau {NIVEAUX[level]}</b>\n\n"
                f"{_bullets(triggers)}\n\nLa plante doit rester à l'intérieur.")
    return (f"🌿 <b>Tu peux ressortir la plante</b>\n\n"
            f"Aucun risque prévu à Aix-en-Provence sur les {WINDOW_H} prochaines heures.")


def format_summary(data, now, state, triggers):
    rows = _hourly_rows(data, now)
    temps = [r["temperature_2m"] for r in rows if r["temperature_2m"] is not None]
    gusts = [r["wind_gusts_10m"] for r in rows if r["wind_gusts_10m"] is not None]
    precs = [r["precipitation"] for r in rows if r["precipitation"] is not None]
    tmax_d = (data.get("daily", {}).get("temperature_2m_max") or [None, None])
    demain = tmax_d[1] if len(tmax_d) > 1 else None
    lines = [f"📋 <b>Point météo Aix-en-Provence</b> ({now:%d/%m %Hh%M})",
             f"Min {WINDOW_H} h : {min(temps):.1f} °C" if temps else "Min : n/d",
             f"Max demain : {demain:.1f} °C" if demain is not None else "Max demain : n/d",
             f"Rafales max : {max(gusts):.0f} km/h" if gusts else "Rafales : n/d",
             f"Pluie {WINDOW_H} h : {sum(precs):.1f} mm" if precs else "Pluie : n/d",
             ""]
    if state["inside"]:
        lines.append("🏠 Plante : <b>à l'intérieur</b>")
        if triggers:
            lines.append(_bullets(triggers))
    else:
        lines.append("🌿 Plante : <b>dehors OK</b>")
    lines.append(f"\n<i>Source : {data.get('_source', 'Open-Meteo')}</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def send(text, silent=False):
    if os.environ.get("DRY_RUN"):
        print(f"--- MESSAGE{' (silencieux)' if silent else ''} ---\n{text}\n")
        return
    token = os.environ["TELEGRAM_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    body = urllib.parse.urlencode({
        "chat_id": chat, "text": text, "parse_mode": "HTML",
        "disable_notification": "true" if silent else "false",
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, data=body, timeout=20) as r:
                if json.load(r).get("ok"):
                    return
        except Exception as e:
            last = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Envoi Telegram impossible : {last}")


# --------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------
def main(argv):
    mode = argv[1] if len(argv) > 1 else "run"
    now = datetime.now(TZ)
    state = load_state()

    try:
        data = get_forecast()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        state["fail_count"] += 1
        if state["fail_count"] == 2:
            send("⚠️ <b>Agent météo</b> : impossible de récupérer les prévisions depuis 2 passages (~6 h). "
                 "Surveille la météo toi-même en attendant.")
        save_state(state)
        return 1

    if state["fail_count"] >= 2:
        send("✅ <b>Agent météo</b> : prévisions de nouveau disponibles.")
    state["fail_count"] = 0
    triggers = evaluate(data, now)

    if mode == "test":
        send("✅ <b>Test de l'agent météo réussi</b>\n\n" + format_summary(data, now, state, triggers)
             + ("\n\nDéclencheurs actuels :\n" + _bullets(triggers) if triggers else "\n\nAucun déclencheur actuellement."))
        return 0

    sent = False
    for kind, level in decide(state, triggers):
        silent = now.hour in NIGHT_HOURS and level < 2
        send(format_event(kind, level, triggers), silent=silent)
        sent = True

    today = now.date().isoformat()
    if now.hour in SUMMARY_HOURS and state["last_summary"] != today:
        if not sent:  # une alerte envoyée ce passage prouve déjà que l'agent tourne
            send(format_summary(data, now, state, triggers), silent=True)
        state["last_summary"] = today

    save_state(state)
    print(f"{now:%Y-%m-%d %H:%M} | niveau={state['level']} dedans={state['inside']} | "
          + "; ".join(t["msg"] for t in triggers))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
