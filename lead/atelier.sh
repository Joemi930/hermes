#!/usr/bin/env bash
# Ateliers : l'espace de travail d'un worker, pour une tache.
#
# Un atelier est un clone LOCAL et SANS DEPOT DISTANT. Le worker y dispose de
# tout git — branches, commits, historique — mais `git push` n'a nulle part ou
# aller. Ce n'est pas une interdiction qu'on lui demande de respecter, c'est
# une absence.
#
#   atelier.sh creer <tache>      le lead prepare l'atelier
#   atelier.sh recolter <tache>   le lead recupere le travail dans son depot
#   atelier.sh detruire <tache>
#
# A lancer en tant que lead (hermes), jamais en root.

set -euo pipefail

WORKER="${WORKER:-hermes-worker}"
DEPOT="${DEPOT:-/opt/hermes/depots/yeba}"
ATELIERS="${ATELIERS:-/srv/ateliers}"

action="${1:-}"; tache="${2:-}"
[ -n "$action" ] && [ -n "$tache" ] || { echo "Usage: atelier.sh {creer|recolter|detruire} <tache>" >&2; exit 2; }
# Un nom de tache finit en nom de dossier : on refuse tout ce qui pourrait
# s'echapper de $ATELIERS.
[[ "$tache" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo "Nom de tache invalide : $tache" >&2; exit 2; }
atelier="$ATELIERS/$tache"

case "$action" in
  creer)
    [ -e "$atelier" ] && { echo "L'atelier $tache existe deja." >&2; exit 1; }
    git clone --no-hardlinks --quiet "$DEPOT" "$atelier"

    # Le point central : plus aucun distant. `git push` echoue par absence de
    # destination, pas par refus de permission.
    for r in $(git -C "$atelier" remote); do git -C "$atelier" remote remove "$r"; done

    # Et rien qui puisse servir d'identifiant ne doit avoir suivi.
    rm -f "$atelier/.env" "$atelier"/.env.* 2>/dev/null || true
    git -C "$atelier" config --unset-all credential.helper 2>/dev/null || true
    git -C "$atelier" config core.sshCommand "/bin/false"   # ceinture et bretelles

    chgrp -R "$WORKER" "$atelier"
    chmod -R g+rwX "$atelier"
    find "$atelier" -type d -exec chmod g+s {} +

    # git refuse d'operer dans un depot appartenant a un autre utilisateur
    # ("dubious ownership"). L'atelier appartient au lead, le worker y travaille :
    # on declare l'exception, pour CE chemin precis et pas pour tous.
    sudo -n -u "$WORKER" git config --global --add safe.directory "$atelier"

    echo "Atelier pret : $atelier"
    ;;

  recolter)
    [ -d "$atelier" ] || { echo "Atelier inconnu : $tache" >&2; exit 1; }
    # Le lead tire depuis l'atelier vers son depot. Le sens compte : c'est le
    # lead qui va chercher, le worker n'envoie jamais.
    git -C "$DEPOT" fetch --quiet "$atelier" "+refs/heads/*:refs/atelier/$tache/*"
    echo "Travail recupere sous refs/atelier/$tache/* :"
    git -C "$DEPOT" for-each-ref --format='  %(refname:short)  %(objectname:short)  %(contents:subject)' "refs/atelier/$tache"
    ;;

  detruire)
    [ -d "$atelier" ] || { echo "Atelier inconnu : $tache" >&2; exit 1; }
    rm -rf "$atelier"
    echo "Atelier $tache supprime."
    ;;

  *) echo "Action inconnue : $action" >&2; exit 2 ;;
esac
