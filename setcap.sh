#!/bin/sh
# Donne les droits réseau (capture, ARP, traceroute) au Python du venv, pour lancer Spaghet sans sudo.
# À relancer après chaque recréation du venv ou mise à jour de Python.
set -eu
cd "$(dirname "$0")"
py=$(readlink -f .venv/bin/python)

# Jamais sur un Python partagé : tous les scripts Python du système recevraient ces droits.
case "$py" in
  "$PWD"/.venv/*) ;;
  *) echo "Refusé : $py n'est pas propre au projet. Recrée le venv avec : python3 -m venv --copies .venv" >&2; exit 1 ;;
esac

chmod 700 "$py"  # seul ton compte peut lancer cet interpréteur privilégié
sudo setcap cap_net_raw,cap_net_admin+eip "$py"
getcap "$py"
echo "OK : lance maintenant  .venv/bin/python -m spaghet  (sans sudo)"
