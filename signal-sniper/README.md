# Signal Sniper : signaux Telegram → MT5 Deriv, 24h/24

Écoute le canal « Signaux trader elite » avec **ton compte Telegram**, comprend les signaux de Festo
(V10, V75, V50s, Crash/Boom, Buy limit, SL en prix ou en $, TP en paliers…), passe les ordres sur ton **MT5 Deriv**
et te prévient sur **WhatsApp** (et dans tes « Messages enregistrés » Telegram).

```
Canal Telegram ─► Telethon (ton compte, ~instantané, sans VPN depuis le VPS)
                    ├─ règles rapides (~1 ms) : "Sell V10 lot 2", "Sl 4942.54", "TP 1 +15$", "buy now V75…"
                    └─ Claude Sonnet 5.5 (~1-3 s) : images de graphiques, messages ambigus, conseils/prévisions
                  ─► moteur : lots, SL/TP en $ → prix, doublons, SL de secours, TP1 → SL à l'entrée
                  ─► MT5 (Docker + Wine, pont mt5linux) ─► notifications WhatsApp / Telegram
```

## Ce que le bot comprend

| Message de Festo | Action |
|---|---|
| `Sell V10 lot 2` puis `Sl 4942.54` une minute après | Vente au marché tout de suite, avec un SL de secours, remplacé dès que le vrai SL arrive |
| Graphique + `PLACEZ les Buy Limit suivant`, puis `Buy limit at 4911.638 Lot 1 / Buy limit at 4907.058 Lot 1 / SL4900` | Symbole lu sur l'image, 2 Buy Limit, SL commun |
| Graphique + `Placez ça, lot 0.02, V75, Sl à 8.5$` | Buy Limit au prix lu sur le graphique, SL converti de $ en prix |
| `Déclenché… buy now Lot 0.02 V75 Sl 8.5$` | Achat au marché, sauf si déjà en position (doublon ignoré) ; annule la limite non déclenchée |
| `TP 1 +15$ / TP 2 +30$ / TP 3 +55$` | Une seule position : TP final chez le broker, SL remonté au prix d'entrée dès TP1 |
| `Coupons`, `vous pouvez clôturer` | Fermeture |
| `Bilan…`, `+8$`, `SL hit`, prévisions, `on attend la bougie m15` | Rien (notification si c'est une info utile) |

Les montants en $ de Festo sont calculés sur **son** lot : si tu multiplies le lot, la distance du SL/TP en prix reste la même.

## Installation sur le VPS

### 1. Les clés
- **Telegram** : https://my.telegram.org → *API development tools* → note `api_id` et `api_hash`.
- **Claude** : https://console.anthropic.com → *API Keys*.
- **WhatsApp (CallMeBot, gratuit)** : suis https://www.callmebot.com/blog/free-api-whatsapp-messages/
  (ajoute leur numéro, envoie-leur le message d'autorisation, tu reçois ta `apikey`).

### 2. Configuration
```bash
git clone <ce dépôt> && cd signal-sniper
cp .env.example .env && nano .env      # remplis les clés, LOT_MULTIPLIER, etc.
```

### 3. MetaTrader 5
```bash
docker compose up -d mt5
```
Depuis ton PC : `ssh -L 3000:localhost:3000 user@ton-vps`, puis ouvre http://localhost:3000.
Dans MT5 : *Fichier → Se connecter à un compte de trading* (ton login Deriv MT5, serveur Deriv),
puis active le bouton **Algo Trading**. Le terminal reste connecté dans le conteneur.

### 4. Connexion Telegram (une seule fois)
```bash
docker compose run --rm sniper python -m sniper.login
```
Entre ton numéro et le code reçu. Le script affiche tes canaux avec leur ID : mets celui du canal dans `TG_CHANNEL`.

### 5. Lancement
```bash
docker compose up -d --build sniper
docker compose logs -f sniper
```
Tu reçois « 🤖 Sniper démarré » sur WhatsApp/Telegram.

## Commandes
Écris-les dans tes **Messages enregistrés** Telegram : `/status`, `/pause`, `/resume`, `/closeall`.

## Modes (`MODE` dans `.env`)
- `trade` : exécution automatique.
- `dry_run` : tout comme `trade` mais n'envoie pas les ordres (pratique pour vérifier l'installation en 5 min).
- `notify` : notifications WhatsApp seulement, MT5 pas nécessaire.

## Garde-fous
- Signal pas clair (confiance < `MIN_CONFIDENCE`) → pas exécuté, tu reçois le message.
- Signal reçu en retard (coupure, redémarrage) → pas exécuté au-delà de `MAX_SIGNAL_AGE_S`.
- `MAX_LOT`, `MAX_OPEN_TRADES`, `MAX_DAILY_LOSS_USD` (pause automatique).
- Le fichier `data/sniper.session` donne accès à ton Telegram : ne le partage jamais.

## Tests
```bash
pip install -r requirements.txt pytest && pytest -q
```
