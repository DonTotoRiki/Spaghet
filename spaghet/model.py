import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field


@dataclass
class Node:
    ip: str  # identifiant : l'IP sur le LAN, le domaine pour une destination Internet
    first_seen: float
    last_seen: float  # dernière fois que l'appareil a répondu ou émis
    mac: str | None = None
    name: str | None = None
    vendor: str | None = None
    kind: str = "unknown"  # "router", "phone", "pc", "printer", "iot", "private", "internet", "unknown"
    rtt_ms: float | None = None
    hops: int | None = None
    bytes_total: int = 0
    services: list[str] = field(default_factory=list)  # services mDNS annoncés
    addr: str | None = None  # destination Internet : première IP vue, pour le traceroute et la géoloc
    lat: float | None = None
    lon: float | None = None
    city: str | None = None
    country: str | None = None


@dataclass
class Link:
    a: str
    b: str
    last_seen: float
    protocols: dict[str, int] = field(default_factory=dict)  # protocole → octets
    bytes_total: int = 0
    bytes_ab: int = 0  # part de bytes_total envoyée de a vers b, pour le sens des particules
    bps: float = 0.0     # débit en bits/s sur la fenêtre RATE_WINDOW, les deux sens confondus
    bps_ab: float = 0.0  # part de bps allant de a vers b


RATE_WINDOW = 5  # secondes


def link_key(x: str, y: str) -> tuple[str, str]:
    # Un lien n'a pas de sens : A→B et B→A sont le même lien.
    return (x, y) if x < y else (y, x)


class Network:
    """État du réseau en mémoire, partagé entre les threads de découverte, de capture et l'interface."""

    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.links: dict[tuple[str, str], Link] = {}
        self._samples: dict[tuple[str, str], deque] = {}  # lien → (date, bytes_total) des derniers ticks
        self._lock = threading.Lock()

    def _touch(self, ip: str, now: float, seen: bool = True) -> Node:
        node = self.nodes.get(ip)
        if node is None:
            node = self.nodes[ip] = Node(ip=ip, first_seen=now, last_seen=now)
        if seen:
            node.last_seen = now
        return node

    def upsert_node(self, ip: str, now: float | None = None, seen: bool = True, **fields) -> None:
        """Crée ou met à jour un nœud. Les champs à None n'écrasent pas une valeur connue.

        seen=False : mise à jour d'infos (nom, fabricant…) qui ne prouve pas que l'appareil est encore là.
        """
        with self._lock:
            now = time.time() if now is None else now
            node = self._touch(ip, now, seen)
            for name, value in fields.items():
                if not hasattr(node, name):
                    raise AttributeError(f"Node n'a pas de champ {name!r}")
                if value is not None:
                    setattr(node, name, value)

    def add_traffic(self, src: str, dst: str, proto: str, size: int, now: float | None = None) -> None:
        with self._lock:
            now = time.time() if now is None else now
            for ip in (src, dst):
                self._touch(ip, now).bytes_total += size
            key = link_key(src, dst)
            link = self.links.get(key)
            if link is None:
                link = self.links[key] = Link(a=key[0], b=key[1], last_seen=now)
            link.last_seen = now
            link.bytes_total += size
            if src == link.a:
                link.bytes_ab += size
            link.protocols[proto] = link.protocols.get(proto, 0) + size

    def prune(self, max_age: float, now: float | None = None) -> list[str]:
        """Supprime les nœuds muets depuis plus de max_age secondes, et leurs liens. Renvoie les nœuds retirés."""
        with self._lock:
            now = time.time() if now is None else now
            gone = [ip for ip, n in self.nodes.items() if now - n.last_seen > max_age]
            for ip in gone:
                del self.nodes[ip]
            for key, l in list(self.links.items()):
                if l.a in gone or l.b in gone:
                    del self.links[key]
                    self._samples.pop(key, None)
            return gone

    def tick(self, now: float | None = None) -> None:
        """À appeler chaque seconde : recalcule le débit de chaque lien sur les RATE_WINDOW dernières secondes."""
        with self._lock:
            now = time.time() if now is None else now
            for key, link in self.links.items():
                samples = self._samples.setdefault(key, deque(maxlen=RATE_WINDOW + 1))
                samples.append((now, link.bytes_total, link.bytes_ab))
                (t0, b0, ab0), (t1, b1, ab1) = samples[0], samples[-1]
                rate = lambda before, after: round((after - before) * 8 / (t1 - t0)) if t1 > t0 else 0.0
                link.bps, link.bps_ab = rate(b0, b1), rate(ab0, ab1)

    def snapshot(self) -> dict:
        """Copie de l'état complet, lisible sans verrou par l'interface et exportable en JSON."""
        with self._lock:
            return {
                "now": time.time(),
                "nodes": [asdict(n) for n in self.nodes.values()],
                "links": [asdict(l) for l in self.links.values()],
            }
