import argparse
import sys
import threading
import time

import uvicorn

from spaghet import capture, discovery, geo, probes
from spaghet.server import app, network

PROBE_INTERVAL = 10   # mesure de latence
RESCAN_INTERVAL = 60  # scan ARP pour trouver les nouveaux appareils
MAX_AGE = 300         # un appareil muet depuis 5 min est retiré


def every(seconds: float, fn, *args) -> None:
    """Lance fn(*args) toutes les `seconds` secondes dans un thread de fond."""
    def loop():
        while True:
            time.sleep(seconds)
            try:
                fn(*args)
            except Exception as e:  # une tournée ratée ne doit pas tuer la boucle
                print(f"{fn.__name__} : {e}")

    threading.Thread(target=loop, name=fn.__name__, daemon=True).start()


def rescan(iface: str) -> None:
    discovery.scan(network, iface)
    discovery.identify(network, only_new=True)
    network.prune(MAX_AGE)


parser = argparse.ArgumentParser(prog="spaghet")
parser.add_argument("--iface", help="interface réseau (par défaut : celle de la route par défaut)")
parser.add_argument("--geoip", help=f"base GeoIP .mmdb (par défaut : $SPAGHET_GEOIP ou {geo.DEFAULT_PATH})")
args = parser.parse_args()

print("Scan ARP du réseau local…")
try:
    iface, subnet, _my_ip, _gateway = discovery.local_network(args.iface)
    discovery.scan(network, iface)
except PermissionError:
    sys.exit("Le scan ARP demande les droits réseau : lance ./setcap.sh une fois (ou utilise sudo)")
except RuntimeError as e:
    sys.exit(str(e))
print(f"{len(network.nodes)} appareils trouvés, identification…")
discovery.identify(network)

print("Mesure de la latence…")
probes.ping_all(network, iface)
every(PROBE_INTERVAL, probes.ping_all, network, iface)
every(RESCAN_INTERVAL, rescan, iface)

geo_reader = geo.load(args.geoip)
print("Géolocalisation :", "activée" if geo_reader else f"désactivée (pas de base {args.geoip or geo.DEFAULT_PATH})")

print("Capture du trafic…")
capture.start(network, iface, subnet, geo_reader)
every(1, network.tick)

# 127.0.0.1 uniquement : l'outil expose des infos sur le réseau, il ne doit pas être joignable de l'extérieur.
uvicorn.run(app, host="127.0.0.1", port=8000)
