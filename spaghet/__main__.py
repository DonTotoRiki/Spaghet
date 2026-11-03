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
NO_RIGHTS = "Droits réseau manquants : ferme Spaghet, lance ./setcap.sh une fois, puis relance avec ./spaghet.sh"


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


class Backend:
    """Tâches de fond : capture, scan, identification, latence, surveillance du réseau et de la capture."""

    def __init__(self, network: Network, iface_arg: str | None, iface: str, subnet, my_ip: str, gateway: str | None,
                 geo_reader=None, notes: list[str] | None = None):
        self.network = network
        self.iface_arg = iface_arg  # tel que donné par --iface : None = suivre la route par défaut
        self.iface, self.subnet, self.my_ip, self.gateway = iface, subnet, my_ip, gateway
        self.geo_reader = geo_reader
        self.notes = notes if notes is not None else []  # remarques durables affichées dans la barre d'état
        # set : l'ordre des adresses IPv6 peut varier
        self.started_on = (iface, subnet, my_ip, gateway), set(capture.local_prefixes6(iface))
        self.identified: set[str] = set()  # chaque appareil n'est identifié qu'une fois, même s'il reste sans nom
        self.report = print                # remplacé par la barre d'état une fois la fenêtre ouverte
        self.sniffer = None
        self.capture_paused = False  # le message de coupure est affiché : à effacer dès que la capture tourne à nouveau

    def status(self, message: str = "") -> None:
        self.report(" · ".join([message, *self.notes] if message else self.notes))

    def identify_new(self) -> None:
        new = set(self.network.nodes) - self.identified
        discovery.identify(self.network, new)
        self.identified.update(new)

    def rescan(self) -> None:
        discovery.scan(self.network, self.iface)
        # Retrait d'abord : une erreur d'identification ne doit pas laisser les appareils partis à l'écran.
        for ip in self.network.prune(MAX_AGE):
            self.identified.discard(ip)  # s'il revient, il sera identifié à nouveau
        self.identify_new()

    def watch_network(self) -> None:
        """Interface, sous-réseau, adresses et passerelle sont fixés au démarrage : s'ils changent, tout devient faux."""
        try:
            now = discovery.current_network(self.iface_arg), set(capture.local_prefixes6(self.iface))
        except RuntimeError:
            now = None  # plus de réseau du tout (Wi-Fi coupé) : la surveillance de la capture le signale déjà
        note = "Réseau changé depuis le lancement : relance Spaghet pour le cartographier"
        if now and now != self.started_on and note not in self.notes:
            self.notes.insert(0, note)
            self.status()

    def watch_capture(self) -> None:
        self.sniffer, error = capture.ensure_running(self.sniffer)
        if isinstance(error, PermissionError):
            self.status(NO_RIGHTS)
        elif error:
            # Interface coupée (veille, Wi-Fi) : chaque relance échoue jusqu'à son retour, le message reste le même.
            self.capture_paused = True
            self.status(f"Capture en pause, nouvel essai toutes les 5 s ({error})")
        elif self.capture_paused:
            self.capture_paused = False  # la relance précédente a tenu
            self.status()

    def start(self, report_to_window) -> None:
        """Démarrage, une fois la fenêtre ouverte. La capture d'abord : le reste n'est qu'un enrichissement."""
        self.report = report_to_window
        self.sniffer = capture.start(self.network, self.iface, self.subnet, self.geo_reader, self.gateway)
        every(1, self.network.tick)
        every(5, self.watch_capture)
        every(30, self.watch_network)
        # Avant le premier scan : s'il échoue (Wi-Fi pas encore prêt après la veille), le rescan prendra le relais.
        every(PROBE_INTERVAL, probes.ping_all, self.network, self.iface, self.my_ip)
        every(RESCAN_INTERVAL, self.rescan)
        try:
            self.status("Scan ARP du réseau local…")
            if discovery.scan(self.network, self.iface):
                self.notes.insert(0, "Proxy ARP détecté : seuls les appareils en contact avec cette machine apparaissent")
            self.status(f"{len(self.network.nodes)} appareils trouvés, identification…")
            self.identify_new()
            self.status("Mesure de la latence…")
            probes.ping_all(self.network, self.iface, self.my_ip)
            self.status()
        except PermissionError:
            self.status(NO_RIGHTS)
        except Exception as e:
            self.status(f"Découverte incomplète : {e}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="spaghet")
    parser.add_argument("--iface", help="interface réseau (par défaut : celle de la route par défaut)")
    parser.add_argument("--geoip", help=f"base GeoIP .mmdb (par défaut : $SPAGHET_GEOIP ou {geo.DEFAULT_PATH})")
    args = parser.parse_args()

    try:
        iface, subnet, my_ip, gateway = discovery.local_network(args.iface)
    except RuntimeError as e:
        sys.exit(str(e))
    notes: list[str] = []
    try:
        geo_reader = geo.load(args.geoip)
        if not geo_reader:
            notes.append(f"Lieux inconnus : pas de base {geo.path_for(args.geoip)}")
    except ValueError as e:
        geo_reader = None
        notes.append(f"Lieux inconnus : {e}")
    backend = Backend(Network(), args.iface, iface, subnet, my_ip, gateway, geo_reader, notes)
    # os._exit : ne pas attendre les threads de fond (recherches DNS en cours…) une fois la fenêtre fermée.
    os._exit(gui.run(backend.network, backend.start))


if __name__ == "__main__":
    main()
