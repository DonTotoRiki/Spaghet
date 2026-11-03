#!/bin/sh
# Donne les droits réseau (capture, ARP, traceroute) à une copie de Python réservée à Spaghet : .venv/bin/python-net.
# Le Python habituel du venv reste sans droits : pip install et tes scripts n'en profitent pas.
# À relancer après chaque recréation du venv ou mise à jour de Python.
set -eu
cd "$(dirname "$0")"
py=$(readlink -f .venv/bin/python)

# Jamais à partir d'un Python partagé : ce serait le binaire du système qui serait copié puis modifié par erreur.
case "$py" in
  "$PWD"/.venv/*) ;;
  *) echo "Refusé : $py n'est pas propre au projet. Recrée le venv avec : python3 -m venv --copies .venv" >&2; exit 1 ;;
esac

cp "$py" .venv/bin/python-net
chmod 700 .venv/bin/python-net  # seul ton compte peut lancer cet interpréteur privilégié
sudo setcap cap_net_raw,cap_net_admin+eip .venv/bin/python-net
# Anciennes versions de ce script : les droits étaient sur le Python habituel, on les retire.
if [ -n "$(getcap "$py")" ]; then
  sudo setcap -r "$py"
fi
getcap .venv/bin/python-net
echo "OK : lance maintenant  ./spaghet.sh  (sans sudo)"
