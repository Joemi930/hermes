#!/usr/bin/env bash
# Acces du lead au depot Yeba, par deploy key.
#
# La cle privee nait sur cette machine et n'en sort jamais. Elle est portee au
# depot Yeba comme deploy key en ecriture : sa portee est UN depot, et elle se
# revoque seule, sans toucher au compte de personne.
#
# Le script se lance DEUX FOIS :
#   1re fois : il cree la cle et affiche la publique -> tu l'enregistres sur GitHub
#   2e  fois : il teste l'acces et clone
#
#   bash acces-yeba.sh          (en tant que hermes, jamais en root)

set -euo pipefail

RACINE="${RACINE:-/opt/hermes}"
CLE="$RACINE/yeba_ed25519"
ALIAS="yeba.github.com"
DEPOT_URL="git@$ALIAS:Yeba-dev-org/yeba.git"
DESTINATION="$RACINE/depots/yeba"

[[ $EUID -ne 0 ]] || { echo "À lancer en tant que lead, pas en root." >&2; exit 1; }
[ -d "$RACINE" ] || { echo "$RACINE absent : lance d'abord mur.sh." >&2; exit 1; }
[ -w "$RACINE" ] || { echo "$RACINE ne t'appartient pas. Es-tu bien le lead ?" >&2; exit 1; }

# --- 1. La cle -------------------------------------------------------------
# Trois etats possibles, pas deux : absente, complete, ou a moitie la — une
# generation interrompue laisse une cle privee sans sa publique.
if [ ! -f "$CLE" ]; then
  echo "==> Generation de la cle de deploiement"
  ssh-keygen -t ed25519 -N "" -C "hermes@$(hostname) yeba-deploy" -f "$CLE" >/dev/null
  echo "    cle creee : $CLE"
elif ! ssh-keygen -y -f "$CLE" >/dev/null 2>&1; then
  echo "  $CLE existe mais n'est pas une cle privee SSH exploitable." >&2
  echo "  Deplace-le ou supprime-le, puis relance :" >&2
  echo "      mv $CLE $CLE.hors-service" >&2
  exit 1
elif [ ! -f "$CLE.pub" ]; then
  echo "==> Cle privee presente, publique manquante : on la redérive"
  ssh-keygen -y -f "$CLE" > "$CLE.pub"
else
  echo "==> Cle deja presente : $CLE"
fi
chmod 600 "$CLE"; chmod 644 "$CLE.pub"

# --- 2. L'alias SSH --------------------------------------------------------
# GitHub sur le port 22 est parfois filtre en sortie. On bascule sur le 443,
# que GitHub expose exactement pour ce cas.
echo "==> Choix du port vers GitHub"
if timeout 8 bash -c 'cat < /dev/null > /dev/tcp/github.com/22' 2>/dev/null; then
  hote="github.com"; port=22
else
  hote="ssh.github.com"; port=443
  echo "    port 22 filtre en sortie -> bascule sur ssh.github.com:443"
fi
echo "    $hote:$port"

install -d -m 700 "$HOME/.ssh"
touch "$HOME/.ssh/config"; chmod 600 "$HOME/.ssh/config"
# On retire un ancien bloc avant d'en ecrire un neuf : le script doit pouvoir
# se relancer sans empiler les definitions.
python3 - "$HOME/.ssh/config" "$ALIAS" <<'PY'
import sys, re
chemin, alias = sys.argv[1], sys.argv[2]
texte = open(chemin, encoding="utf-8").read()
motif = re.compile(r"(?ms)^Host[ \t]+" + re.escape(alias) + r"[ \t]*$.*?(?=^Host[ \t]|\Z)")
open(chemin, "w", encoding="utf-8").write(motif.sub("", texte).rstrip() + "\n")
PY
cat >> "$HOME/.ssh/config" <<CONF

Host $ALIAS
  HostName $hote
  Port $port
  User git
  IdentityFile $CLE
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
CONF
chmod 600 "$HOME/.ssh/config"

# --- 3. Test d'acces -------------------------------------------------------
echo "==> Test de l'acces a Yeba"
reponse=$(ssh -o BatchMode=yes -T "git@$ALIAS" 2>&1 || true)

if ! grep -q "successfully authenticated" <<<"$reponse"; then
  echo
  echo "  La cle n'est pas (encore) acceptee par GitHub."
  echo "  Reponse : $(head -2 <<<"$reponse" | tr '\n' ' ')"
  echo
  echo "  Enregistre cette cle publique comme DEPLOY KEY, avec ecriture :"
  echo "      github.com/Yeba-dev-org/yeba  ->  Settings  ->  Deploy keys"
  echo "      ->  Add deploy key  ->  COCHER \"Allow write access\""
  echo
  echo "  --- a copier, une seule ligne ---"
  cat "$CLE.pub"
  echo "  ---------------------------------"
  echo
  echo "  Puis relance : bash acces-yeba.sh"
  exit 0
fi
echo "    $(grep -o 'Hi [^.]*' <<<"$reponse" | head -1)"

# --- 4. Le depot -----------------------------------------------------------
if [ -d "$DESTINATION/.git" ]; then
  echo "==> Depot deja present, mise a jour"
  git -C "$DESTINATION" remote set-url origin "$DEPOT_URL"
  git -C "$DESTINATION" fetch --quiet --all --prune
else
  echo "==> Clonage de Yeba"
  install -d -m 700 "$RACINE/depots"
  git clone --quiet "$DEPOT_URL" "$DESTINATION"
fi
chmod 700 "$DESTINATION"

# --- 5. Preuve -------------------------------------------------------------
echo
echo "==> Verification (executee, pas annoncee)"
echec=0
v() { if eval "$2" >/dev/null 2>&1; then printf '    ✓ %s\n' "$1"; else printf '    ✗ %s\n' "$1"; echec=1; fi; }
n() { if eval "$2" >/dev/null 2>&1; then printf '    ✗ %s — A REUSSI\n' "$1"; echec=1; else printf '    ✓ %s — refuse\n' "$1"; fi; }

v "la cle privee est en 600"        "[ \"\$(stat -c %a '$CLE')\" = 600 ]"
v "le depot est clone"              "[ -d '$DESTINATION/.git' ]"
v "origin pointe sur Yeba"          "git -C '$DESTINATION' remote get-url origin | grep -q 'Yeba-dev-org/yeba'"
v "l'acces en ecriture est accorde" "! grep -q 'read-only' <<<\"\$reponse\""
n "le worker lit la cle privee"     "sudo -n -u hermes-worker cat '$CLE'"
n "le worker entre dans le depot"   "sudo -n -u hermes-worker ls '$DESTINATION'"

echo
if [ "$echec" -ne 0 ]; then
  echo "  ECHEC : ne continue pas dans cet etat." >&2
  exit 1
fi
echo "  Acces a Yeba etabli. Depot : $DESTINATION"
echo "  Branche : $(git -C "$DESTINATION" rev-parse --abbrev-ref HEAD)  |  dernier commit : $(git -C "$DESTINATION" log -1 --format='%h %ad %s' --date=short)"
