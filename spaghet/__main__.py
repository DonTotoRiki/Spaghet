import argparse
import os
import sys
import threading
import time

from spaghet import capture, discovery, geo, gui, probes
from spaghet.model import Network

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


parser = argparse.ArgumentParser(prog="spaghet")
parser.add_argument("--iface", help="interface réseau (par défaut : celle de la route par défaut)")
parser.add_argument("--geoip", help=f"base GeoIP .mmdb (par défaut : $SPAGHET_GEOIP ou {geo.DEFAULT_PATH})")
args = parser.parse_args()

try:
    iface, subnet, my_ip, _gateway = discovery.local_network(args.iface)
except RuntimeError as e:
    sys.exit(str(e))
geo_reader = geo.load(args.geoip)
network = Network()
identified: set[str] = set()  # chaque appareil n'est identifié qu'une fois, même s'il reste sans nom


def identify_new() -> None:
    new = set(network.nodes) - identified
    discovery.identify(network, new)
    identified.update(new)


def rescan() -> None:
    discovery.scan(network, iface)
    identify_new()
    for ip in network.prune(MAX_AGE):
        identified.discard(ip)  # s'il revient, il sera identifié à nouveau


def backend(report) -> None:
    """Découverte puis tâches de fond, lancées une fois la fenêtre ouverte. report(message) : barre d'état."""
    try:
        report("Scan ARP du réseau local…")
        notes = []
        if discovery.scan(network, iface):
            notes.append("Proxy ARP détecté : seuls les appareils en contact avec cette machine apparaissent")
        report(f"{len(network.nodes)} appareils trouvés, identification…")
        identify_new()
        report("Mesure de la latence…")
        probes.ping_all(network, iface, my_ip)
        every(PROBE_INTERVAL, probes.ping_all, network, iface, my_ip)
        every(RESCAN_INTERVAL, rescan)
        capture.start(network, iface, subnet, geo_reader)
        every(1, network.tick)
        if not geo_reader:
            notes.append(f"Carte sans lieux : pas de base {args.geoip or geo.DEFAULT_PATH}")
        report(" · ".join(notes))
    except PermissionError:
        report("Droits réseau manquants : ferme Spaghet, lance ./setcap.sh une fois, puis relance")
    except Exception as e:
        report(f"Erreur : {e}")


# os._exit : ne pas attendre les threads de fond (recherches DNS en cours…) une fois la fenêtre fermée.
os._exit(gui.run(network, backend))
