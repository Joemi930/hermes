#!/usr/bin/env bash
# Durcissement initial du VPS qui héberge Hermès.
#
# À lancer UNE FOIS, en root, sur un Ubuntu 24.04 fraîchement livré.
#   ssh root@<ip>
#   bash durcissement.sh "<ta-cle-publique-ssh>"
#
# Cette machine portera une deploy key en écriture sur le dépôt Yeba et des
# clés d'API facturées à l'usage. Elle ne reste pas ouverte au monde.

set -euo pipefail

CLE_PUBLIQUE="${1:-}"
UTILISATEUR="hermes"
PORT_SSH="${PORT_SSH:-22}"

if [[ $EUID -ne 0 ]]; then
  echo "À lancer en root." >&2
  exit 1
fi

if [[ -z "$CLE_PUBLIQUE" ]]; then
  echo "Usage: bash durcissement.sh \"ssh-ed25519 AAAA... toi@machine\"" >&2
  echo "Génère la paire sur TA machine : ssh-keygen -t ed25519 -C hermes" >&2
  exit 1
fi

# Refus net d'une clé qui n'en est pas une : sans clé valide, la désactivation
# du mot de passe plus bas t'enfermerait dehors.
if ! grep -qE '^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) ' <<<"$CLE_PUBLIQUE"; then
  echo "Ceci ne ressemble pas à une clé publique SSH." >&2
  exit 1
fi

echo "==> Mise à jour du système"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

echo "==> Paquets de base"
apt-get install -y -qq ufw fail2ban unattended-upgrades git curl ca-certificates jq

echo "==> Utilisateur non-root : $UTILISATEUR"
if ! id -u "$UTILISATEUR" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$UTILISATEUR"
fi
usermod -aG sudo "$UTILISATEUR"

install -d -m 700 -o "$UTILISATEUR" -g "$UTILISATEUR" "/home/$UTILISATEUR/.ssh"
echo "$CLE_PUBLIQUE" > "/home/$UTILISATEUR/.ssh/authorized_keys"
chmod 600 "/home/$UTILISATEUR/.ssh/authorized_keys"
chown "$UTILISATEUR:$UTILISATEUR" "/home/$UTILISATEUR/.ssh/authorized_keys"

# `adduser --disabled-password` laisse le compte sans mot de passe, et sudo en
# réclame un : sans cette étape, l'utilisateur se retrouve incapable d'élever
# ses privilèges dès que la session root est fermée. La clé sert à entrer, le
# mot de passe à devenir root : deux secrets distincts.
if ! passwd -S "$UTILISATEUR" | awk '{print $2}' | grep -q '^P$'; then
  echo
  echo "==> Définis maintenant le mot de passe sudo de $UTILISATEUR"
  echo "    (il ne sert PAS à se connecter — la connexion se fait par clé)"
  until passwd "$UTILISATEUR"; do
    echo "    Réessaie." >&2
  done
fi

echo "==> Vérification que la clé est bien en place avant de couper les mots de passe"
if [[ ! -s "/home/$UTILISATEUR/.ssh/authorized_keys" ]]; then
  echo "authorized_keys vide — on ne touche pas à sshd." >&2
  exit 1
fi

echo "==> SSH : clé uniquement, pas de root, pas de mot de passe"

# Les fichiers de /etc/ssh/sshd_config.d/ sont lus dans l'ordre lexical et,
# chez OpenSSH, LA PREMIERE occurrence d'une directive l'emporte. L'image
# Contabo livre un 50-cloud-init.conf posant `PasswordAuthentication yes` :
# un fichier nomme 99-* arrivait trop tard et la coupure n'avait jamais lieu,
# alors que PermitRootLogin (absent de ce fichier) passait sans probleme.
# D'ou deux precautions : se placer en tete (00-) ET neutraliser les
# definitions concurrentes partout ailleurs.
rm -f /etc/ssh/sshd_config.d/99-hermes.conf
for f in /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf; do
  [ -f "$f" ] || continue
  case "$f" in */00-hermes.conf) continue ;; esac
  sed -i -E 's/^([[:space:]]*(PasswordAuthentication|PermitRootLogin|KbdInteractiveAuthentication)[[:space:]])/#\1/I' "$f"
done

cat > /etc/ssh/sshd_config.d/00-hermes.conf <<CONF
Port $PORT_SSH
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
AllowUsers $UTILISATEUR
CONF
sshd -t   # refuse d'aller plus loin si la config est invalide

# JAMAIS de `restart` ici. Un restart arrete puis redemarre : si le demarrage
# echoue, plus rien n'ecoute sur le 22 et la machine devient injoignable — ce
# qui est exactement arrive le 2026-09-22 avec `systemctl restart ssh.socket`.
# Un `reload` ne coupe jamais l'ecoute. Et sous activation par socket, chaque
# nouvelle connexion lance un sshd qui relit la config : il n'y a de toute
# facon rien a recharger.
systemctl reload ssh 2>/dev/null \
  || systemctl reload sshd 2>/dev/null \
  || echo "    (pas de reload necessaire : config relue a chaque connexion)"

# Filet : quoi qu'il arrive, on refuse de continuer si plus rien n'ecoute.
if ! ss -tln 2>/dev/null | awk '$1=="LISTEN"{print $4}' | grep -qE ':'"$PORT_SSH"'$'; then
  echo >&2
  echo "  ALERTE : plus rien n'ecoute sur le port $PORT_SSH." >&2
  echo "           Tentative de redemarrage..." >&2
  systemctl start ssh.socket 2>/dev/null || systemctl start ssh 2>/dev/null || true
  sleep 2
  if ! ss -tln 2>/dev/null | awk '$1=="LISTEN"{print $4}' | grep -qE ':'"$PORT_SSH"'$'; then
    echo "  ECHEC : impossible de remettre sshd en ecoute." >&2
    echo "          NE FERME PAS ta session. Passe par la console VNC et lance :" >&2
    echo "          journalctl -u ssh.socket -u ssh -n 30 --no-pager" >&2
    exit 1
  fi
  echo "           sshd est reparti." >&2
fi

echo "==> Verification que la coupure a REELLEMENT eu lieu"
etat_mdp=$(sshd -T 2>/dev/null | awk 'tolower($1)=="passwordauthentication"{print tolower($2)}')
etat_root=$(sshd -T 2>/dev/null | awk 'tolower($1)=="permitrootlogin"{print tolower($2)}')
if [ "$etat_mdp" != "no" ] || [ "$etat_root" != "no" ]; then
  echo >&2
  echo "  ECHEC : la configuration effective ne correspond pas a l'intention." >&2
  echo "          passwordauthentication = ${etat_mdp:-inconnu} (attendu: no)" >&2
  echo "          permitrootlogin        = ${etat_root:-inconnu} (attendu: no)" >&2
  echo "          NE FERME PAS ta session. Une autre directive gagne ailleurs :" >&2
  echo "          grep -rniE '^[[:space:]]*(PasswordAuthentication|PermitRootLogin)' \\" >&2
  echo "               /etc/ssh/sshd_config /etc/ssh/sshd_config.d/" >&2
  exit 1
fi
echo "    coupure confirmee par sshd -T"

echo "==> Pare-feu"
ufw default deny incoming
ufw default allow outgoing
ufw allow "$PORT_SSH"/tcp comment 'ssh'
ufw --force enable

echo "==> fail2ban"
cat > /etc/fail2ban/jail.d/hermes.local <<CONF
[sshd]
enabled  = true
port     = $PORT_SSH
maxretry = 4
bantime  = 1h
findtime = 10m
CONF
systemctl enable --now fail2ban
systemctl restart fail2ban

echo "==> Mises à jour de sécurité automatiques"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$UTILISATEUR"

echo
echo "==> Etat effectif (verifie, pas promis)"
sshd -T 2>/dev/null | grep -Ei '^(permitrootlogin|passwordauthentication|pubkeyauthentication|allowusers)' | sed 's/^/    /'
echo "    sudo   : $(passwd -S "$UTILISATEUR" | awk '{print $2}')  (P = mot de passe utilisable)"
echo "    ufw    : $(ufw status | head -1)"
echo "    f2b    : $(systemctl is-active fail2ban)"
echo "    groupes: $(id -nG "$UTILISATEUR")"

echo
echo "Terminé."
echo
echo "  AVANT DE FERMER CE TERMINAL, ouvre-en un autre et vérifie :"
echo "      ssh -p $PORT_SSH $UTILISATEUR@<ip>"
echo
echo "  Tant que tu n'as pas confirmé que ça marche, garde cette session ouverte :"
echo "  l'accès par mot de passe est désormais coupé."
