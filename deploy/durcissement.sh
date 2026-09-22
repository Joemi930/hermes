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
cat > /etc/ssh/sshd_config.d/99-hermes.conf <<CONF
Port $PORT_SSH
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
AllowUsers $UTILISATEUR
CONF
sshd -t
systemctl reload ssh || systemctl reload sshd

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
echo "Terminé."
echo
echo "  AVANT DE FERMER CE TERMINAL, ouvre-en un autre et vérifie :"
echo "      ssh -p $PORT_SSH $UTILISATEUR@<ip>"
echo
echo "  Tant que tu n'as pas confirmé que ça marche, garde cette session ouverte :"
echo "  l'accès par mot de passe est désormais coupé."
