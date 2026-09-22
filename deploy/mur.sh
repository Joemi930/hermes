#!/usr/bin/env bash
# Le mur entre le lead et les workers.
#
#   LEAD   (utilisateur hermes)        : deploy key, .env, droit de pousser
#   WORKER (utilisateur hermes-worker) : un atelier, et rien d'autre
#
# La separation est un fait du systeme de fichiers, pas une consigne dans un
# prompt. Un modele a 0,10 $/M qui envoie tout a l'entrainement d'un tiers ne
# tient jamais les identifiants du depot.
#
#   sudo bash mur.sh

set -euo pipefail

LEAD="${LEAD:-hermes}"
WORKER="${WORKER:-hermes-worker}"
RACINE="${RACINE:-/opt/hermes}"     # lead seul
ATELIERS="${ATELIERS:-/srv/ateliers}"  # terrain partage lead <-> worker

[[ $EUID -eq 0 ]] || { echo "À lancer avec sudo." >&2; exit 1; }
id -u "$LEAD" >/dev/null 2>&1 || { echo "L'utilisateur $LEAD n'existe pas." >&2; exit 1; }

echo "==> Utilisateur worker : $WORKER"
if ! id -u "$WORKER" >/dev/null 2>&1; then
  # Pas de mot de passe, pas de shell de connexion interactive depuis
  # l'exterieur : le worker n'est jamais un compte auquel on se connecte.
  adduser --disabled-password --gecos "" "$WORKER" >/dev/null
fi
# Ni sudo ni docker : un worker qui pourrait lancer un conteneur pourrait
# monter n'importe quel chemin de l'hote et contourner tout le reste.
for g in sudo docker adm; do
  gpasswd -d "$WORKER" "$g" >/dev/null 2>&1 || true
done

echo "==> Racine du lead : $RACINE (le worker ne peut pas y entrer)"
install -d -m 750 -o "$LEAD" -g "$LEAD" "$RACINE"
install -d -m 700 -o "$LEAD" -g "$LEAD" "$RACINE/depots"
install -d -m 700 -o "$LEAD" -g "$LEAD" "$RACINE/etat"

# Le lead doit pouvoir donner ses fichiers d'atelier au groupe du worker :
# sous Linux on ne peut attribuer un fichier qu'a un groupe dont on est membre.
# L'inverse reste ferme — le home du worker passe en 700.
usermod -aG "$WORKER" "$LEAD"
chmod 700 "/home/$WORKER" 2>/dev/null || true

echo "==> Terrain des ateliers : $ATELIERS"
# Le lead cree les ateliers, le worker y travaille mais n'en cree ni n'en
# detruit aucun (groupe en r-x seulement). setgid pour que tout ce qui nait
# ici appartienne d'office au groupe du worker.
install -d -m 2750 -o "$LEAD" -g "$WORKER" "$ATELIERS"

echo "==> Le lead peut endosser le worker sans mot de passe, l'inverse jamais"
cat > /etc/sudoers.d/hermes-worker <<SUDO
# Le lead lance les workers. Il ne leur donne aucun pouvoir en retour.
$LEAD ALL=($WORKER) NOPASSWD: ALL
SUDO
chmod 440 /etc/sudoers.d/hermes-worker
visudo -cf /etc/sudoers.d/hermes-worker >/dev/null

echo
echo "==> Preuve du mur (executee, pas annoncee)"
echec=0
verifier() {  # verifier "libelle" "commande" "attendu: ok|ko"
  local libelle="$1" cmd="$2" attendu="$3" res
  if eval "$cmd" >/dev/null 2>&1; then res=ok; else res=ko; fi
  if [ "$res" = "$attendu" ]; then
    printf '    ✓ %s\n' "$libelle"
  else
    printf '    ✗ %s  (obtenu:%s attendu:%s)\n' "$libelle" "$res" "$attendu"
    echec=1
  fi
}

# Appats : des secrets factices, aux memes emplacements et permissions que
# les vrais. Si le worker les lit, le mur est perce.
appat_cle="$RACINE/.appat_cle"; appat_env="$RACINE/.appat_env"
install -m 600 -o "$LEAD" -g "$LEAD" /dev/null "$appat_cle"
install -m 600 -o "$LEAD" -g "$LEAD" /dev/null "$appat_env"
echo "CLE-PRIVEE-FACTICE" > "$appat_cle"; echo "SECRET=factice" > "$appat_env"
chown "$LEAD:$LEAD" "$appat_cle" "$appat_env"

sous_worker() { sudo -n -u "$WORKER" bash -c "$1"; }

verifier "le worker ne lit pas une cle privee du lead"  "sous_worker 'cat $appat_cle'"   ko
verifier "le worker ne lit pas le .env du lead"         "sous_worker 'cat $appat_env'"   ko
verifier "le worker ne traverse pas $RACINE"            "sous_worker 'ls $RACINE'"       ko
verifier "le worker n'a pas sudo"                       "sous_worker 'sudo -n true'"     ko
verifier "le worker n'a pas docker"                     "sous_worker 'docker ps'"        ko
verifier "le worker atteint le terrain des ateliers"    "sous_worker 'ls $ATELIERS'"     ok
verifier "le worker ne cree pas d'atelier lui-meme"    "sous_worker 'mkdir $ATELIERS/pirate'" ko
verifier "le lead cree bien des ateliers"              "sudo -n -u $LEAD mkdir $ATELIERS/.essai && sudo -n -u $LEAD rmdir $ATELIERS/.essai" ok
verifier "le lead lit ses propres secrets"              "sudo -n -u $LEAD cat $appat_cle" ok

rm -f "$appat_cle" "$appat_env"

echo
if [ "$echec" -ne 0 ]; then
  echo "  ECHEC : le mur n'est pas etanche. Rien ne doit tourner dans cet etat." >&2
  exit 1
fi
echo "  Mur etanche."
