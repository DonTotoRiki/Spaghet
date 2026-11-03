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
    iface, subnet, my_ip, gateway = discovery.local_network(args.iface)
except RuntimeError as e:
    sys.exit(str(e))
notes: list[str] = []  # remarques durables affichées dans la barre d'état
try:
    geo_reader = geo.load(args.geoip)
    if not geo_reader:
        notes.append(f"Carte sans lieux : pas de base {args.geoip or geo.DEFAULT_PATH}")
except ValueError as e:
    geo_reader = None
    notes.append(f"Carte sans lieux : {e}")
started_on = (iface, subnet, my_ip, gateway), set(capture.local_prefixes6(iface))  # set : l'ordre des adresses peut varier
network = Network()
identified: set[str] = set()  # chaque appareil n'est identifié qu'une fois, même s'il reste sans nom
report = print                # remplacé par la barre d'état une fois la fenêtre ouverte


def status(message: str = "") -> None:
    report(" · ".join([message, *notes] if message else notes))


def identify_new() -> None:
    new = set(network.nodes) - identified
    discovery.identify(network, new)
    identified.update(new)


def rescan() -> None:
    discovery.scan(network, iface)
    identify_new()
    for ip in network.prune(MAX_AGE):
        identified.discard(ip)  # s'il revient, il sera identifié à nouveau


def watch_network() -> None:
    """Interface, sous-réseau, adresses et passerelle sont fixés au démarrage : s'ils changent, tout devient faux."""
    try:
        now = discovery.current_network(args.iface), set(capture.local_prefixes6(iface))
    except RuntimeError:
        now = None  # plus de réseau du tout (Wi-Fi coupé) : la surveillance de la capture le signale déjà
    note = "Réseau changé depuis le lancement : relance Spaghet pour le cartographier"
    if now and now != started_on and note not in notes:
        notes.insert(0, note)
        status()


def watch_capture() -> None:
    global sniffer
    sniffer, error = capture.ensure_running(sniffer)
    if isinstance(error, PermissionError):
        status("Droits réseau manquants : ferme Spaghet, lance ./setcap.sh une fois, puis relance avec ./spaghet.sh")
    elif error:
        status(f"Capture relancée après une coupure ({error})")


def backend(report_to_window) -> None:
    """Démarrage, une fois la fenêtre ouverte. La capture d'abord : le reste n'est qu'un enrichissement."""
    global report, sniffer
    report = report_to_window
    sniffer = capture.start(network, iface, subnet, geo_reader, gateway)
    every(1, network.tick)
    every(5, watch_capture)
    every(30, watch_network)
    try:
        status("Scan ARP du réseau local…")
        if discovery.scan(network, iface):
            notes.insert(0, "Proxy ARP détecté : seuls les appareils en contact avec cette machine apparaissent")
        every(PROBE_INTERVAL, probes.ping_all, network, iface, my_ip)
        every(RESCAN_INTERVAL, rescan)
        status(f"{len(network.nodes)} appareils trouvés, identification…")
        identify_new()
        status("Mesure de la latence…")
        probes.ping_all(network, iface, my_ip)
        status()
    except PermissionError:
        status("Droits réseau manquants : ferme Spaghet, lance ./setcap.sh une fois, puis relance avec ./spaghet.sh")
    except Exception as e:
        status(f"Découverte incomplète : {e}")


# os._exit : ne pas attendre les threads de fond (recherches DNS en cours…) une fois la fenêtre fermée.
os._exit(gui.run(network, backend))
