#!/bin/sh
# Lance Spaghet avec l'interpréteur qui a les droits réseau (voir setcap.sh).
dir=$(dirname "$0")
py="$dir/.venv/bin/python-net"
[ -x "$py" ] || py="$dir/.venv/bin/python"  # setcap.sh pas encore lancé : Spaghet dira ce qui manque
# Base GeoIP cherchée dans le dossier du projet, pas dans le dossier d'où on lance le script.
export SPAGHET_GEOIP="${SPAGHET_GEOIP:-$dir/geoip.mmdb}"
exec "$py" -m spaghet "$@"
