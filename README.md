# Hermès

Assistant d'orchestration de projets. Tourne en continu sur un VPS, se pilote
depuis Discord, fait travailler des agents de code sur les dépôts déclarés.

Premier projet piloté : **Yeba**.

---

## Le principe

```
TOI ──Discord──►  HERMÈS          secrétaire — relais, résumés, rapports
                     │
                     └─► LEAD            Claude, abonnement Pro
                            │            plan, revue, validation, push
                            │
                            └─► WORKERS  modèle bon marché
                                         écrivent le code, ne poussent rien
```

Trois niveaux, pas quatre. Le lead et les workers ne sont pas deux sessions
Claude : une seule session Claude qui délègue à des processus bon marché.

## Le mur

C'est le point de conception central, et il est physique, pas déclaratif.

| | Lead | Worker |
|---|---|---|
| Modèle | Claude (Pro, OAuth) | Muse Spark 1.3 |
| Clé SSH du dépôt | ✅ | ❌ |
| `.env`, secrets | ✅ | ❌ |
| Droit de pousser | ✅ | ❌ |
| Sortie | commits, PR, rapport | un diff, rien d'autre |

Un worker reçoit un worktree et une spec. Il rend un diff. Il ne tient jamais
d'identifiant.

## La porte

Aucun rendu de worker n'est accepté sur parole. Le lead exécute lui-même la
porte de validation du projet (`projets/<nom>.yaml`, clé `porte`) avant
d'accepter quoi que ce soit. Rendu refusé → renvoyé au worker avec la sortie
d'erreur brute.

Un aller-retour coûte des centimes : on préfère cinq itérations vérifiées à une
itération crue.

## Structure

```
hermes/
├── deploy/durcissement.sh   durcissement du VPS, à lancer une fois
├── projets/<nom>.yaml       un projet = un fichier, jamais de code en dur
├── lead/                    réveil, clone, plan, dispatch, revue, push
├── worker/                  processus isolé, sans secrets
├── porte/                   exécution des contrôles, verdict
└── discord/                 le bot
```

Ajouter un projet coûte un fichier dans `projets/`. Un projet non-code (notes,
rapports) ne déclare simplement pas de `porte`.

## Installation

1. **VPS** — Ubuntu 24.04. Contabo VPS 10 (4 vCPU / 8 Go / 75 Go, ~4,50 €/mois)
   ou équivalent.
2. **Durcissement** — sur ta machine : `ssh-keygen -t ed25519 -C hermes`.
   Puis sur le VPS, en root :
   ```
   bash deploy/durcissement.sh "ssh-ed25519 AAAA... toi@machine"
   ```
   Vérifie la connexion depuis un **second terminal** avant de fermer le
   premier : le script coupe l'accès par mot de passe.
3. **Deploy key** — génère la clé du dépôt sur le VPS, la privée n'en sort
   jamais, la publique va en deploy key **avec accès write**.
4. **Clés d'API** — `.env` à la racine, `chmod 600`. Jamais dans le dépôt.

## État

En construction. Fait : durcissement VPS, configuration Yeba.
À venir : lead, worker, porte, bot Discord.
