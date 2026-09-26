# Agent météo plante : Aix-en-Provence

Toutes les 3 h, l'agent récupère les prévisions Météo-France (AROME/ARPEGE via Open-Meteo). Il les compare aux seuils et t'écrit sur Telegram :

- 🪴 **« Rentre la plante »** : dès qu'un risque apparaît dans les 30 prochaines heures ;
- ⚠️ / 🚨 **« Alerte renforcée »** : si le niveau monte (par exemple froid → gel) ;
- 🌿 **« Tu peux la ressortir »** : quand plus aucun risque n'est prévu, confirmé sur 2 passages ;
- 📋 **Point du soir** (notification silencieuse, vers 17–21 h) : c'est le message de vie. **Si tu ne le reçois pas un soir, c'est que l'agent est en panne.**

Pas de spam : tant que la plante est dedans, tu ne reçois un nouveau message que si ça s'aggrave. La nuit (22 h – 7 h), les alertes de niveau Attention arrivent sans son. Les niveaux Fort et Critique sonnent toujours.

## Seuils (modifiables dans `SEUILS`, en haut de `meteo_plante.py`)

| Risque | Attention | Fort | Critique |
|---|---|---|---|
| Froid (min sur 30 h) | < 12 °C | < 10 °C | ≤ 2 °C (gel) |
| Orage / forte averse | | orage prévu ou ≥ 10 mm/h | grêle prévue |
| Pluie prolongée | ≥ 6 h de pluie sur 30 h, ou 2 jours pluvieux de suite | ≥ 20 mm sur 30 h | |
| Humidité | > 80 % et < 15 °C pendant ≥ 6 h d'affilée | | |
| Vent (rafales) | ≥ 45 km/h | ≥ 60 km/h | ≥ 80 km/h (tempête) |
| Chaleur | ≥ 32 °C deux jours de suite | ≥ 35 °C deux jours de suite | |

## Mise en service

### 1. Bot Telegram

1. Dans Telegram, ouvre **@BotFather**, envoie `/newbot` et choisis un nom. Copie le **token** qu'il te donne (du type `123456:ABC...`).
2. Ouvre ton nouveau bot et envoie-lui un message (par exemple `salut`).
3. Dans un navigateur, va sur `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`. Repère `"chat":{"id":123456789` : ce nombre est ton **chat ID**.

### 2. Secrets GitHub

Va dans **Settings → Secrets and variables → Actions → New repository secret** et crée :

- `TELEGRAM_TOKEN` : le token du bot ;
- `TELEGRAM_CHAT_ID` : ton chat ID.

Les secrets ne sont pas visibles publiquement, même si le dépôt est public.

### 3. Test

Va dans l'onglet **Actions → Agent météo plante → Run workflow**, laisse le mode `test` et lance. Tu dois recevoir « ✅ Test de l'agent météo réussi » avec la météo actuelle. Ensuite, l'agent tourne tout seul.

## À savoir

- **Pannes** : si les prévisions sont injoignables 2 passages de suite (~6 h), tu reçois un avertissement. GitHub t'envoie aussi un e-mail si un passage échoue.
- **Dépôt public** : des utilisateurs signalent depuis l'été 2026 que les tâches planifiées ne se déclenchent pas sur les dépôts privés des comptes gratuits. GitHub ne l'a pas confirmé, mais un dépôt public évite ce risque. Le dépôt ne contient aucun secret : seulement le code et `state.json` (la mémoire de l'agent).
- **Désactivation après 60 jours** : GitHub désactive les tâches planifiées d'un dépôt public sans activité pendant 60 jours. Ici, l'agent enregistre son état au moins une fois par jour, ce qui compte comme activité.
- **Retards** : GitHub peut lancer les passages avec 15 à 60 min de retard. La fenêtre de 30 h laisse largement le temps.
- **Grêle** : Open-Meteo ne signale la grêle de façon fiable qu'en Europe centrale. Pour Aix, c'est surtout l'alerte orage qui te protège.

## Tests en local

~~~bash
python3 -m unittest discover -s tests -v        # règles d'alerte (sans réseau)
DRY_RUN=1 python3 meteo_plante.py test          # vraie météo, affichage sans envoi
~~~
