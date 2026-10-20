import ipaddress

from scapy.all import DNS, ICMP, IP, TCP, UDP, AsyncSniffer, Raw

from spaghet import geo
from spaghet.classify import STREAMING_DOMAINS, protocol, registrable_domain
from spaghet.model import Network

UNRESOLVED = "(non résolu)"  # nœud qui regroupe les IP Internet dont on n'a pas vu le nom


def tls_sni(payload: bytes) -> str | None:
    """Nom de serveur (SNI) d'un TLS ClientHello, ou None si ce n'en est pas un ou s'il est coupé."""
    try:
        if len(payload) < 6 or payload[0] != 0x16 or payload[5] != 0x01:
            return None
        p = 5 + 4 + 2 + 32                               # en-tête record, en-tête handshake, version, random
        p += 1 + payload[p]                              # session id
        p += 2 + int.from_bytes(payload[p:p + 2])        # suites de chiffrement
        p += 1 + payload[p]                              # compression
        end = p + 2 + int.from_bytes(payload[p:p + 2])
        p += 2
        while p + 4 <= min(end, len(payload)):
            ext_type, ext_len = int.from_bytes(payload[p:p + 2]), int.from_bytes(payload[p + 2:p + 4])
            p += 4
            if ext_type == 0:                            # server_name : longueur liste (2), type (1), longueur (2), nom
                name_len = int.from_bytes(payload[p + 3:p + 5])
                name = payload[p + 5:p + 5 + name_len]
                return name.decode("ascii") if len(name) == name_len and name_len else None
            p += ext_len
    except (IndexError, UnicodeDecodeError):
        pass
    return None


def make_handler(network: Network, subnet: ipaddress.IPv4Network, geo_reader=None):
    """Callback scapy : chaque paquet IP devient du trafic sur un lien du graphe.

    Les IP Internet deviennent un nœud par domaine, nommé grâce aux réponses DNS et au SNI TLS vus passer.
    """
    names: dict[str, str] = {}  # IP Internet → nom d'hôte

    def learn(pkt) -> None:
        if UDP in pkt and pkt[UDP].sport == 53 and DNS in pkt and pkt[DNS].qr == 1 and pkt[DNS].qd:
            # Le nom demandé, pas la fin de la chaîne CNAME : « youtube.com » plutôt que « youtube-ui.l.google.com ».
            asked = pkt[DNS].qd[0].qname.decode(errors="replace")
            for rr in pkt[DNS].an or []:
                if rr.type == 1:  # A
                    names[rr.rdata] = asked
        elif TCP in pkt and pkt[TCP].dport == 443 and Raw in pkt:
            if host := tls_sni(bytes(pkt[Raw].load)):
                names[pkt[IP].dst] = host

    def endpoint(ip: str) -> str | None:
        addr = ipaddress.IPv4Address(ip)
        if addr.is_multicast or addr.is_unspecified or addr == subnet.broadcast_address or ip == "255.255.255.255":
            return None  # diffusion : personne en particulier à l'autre bout
        if addr in subnet:
            return ip
        node_id = registrable_domain(names[ip]) if ip in names else UNRESOLVED
        if node_id not in network.nodes:
            # Créé avec son type avant le trafic, pour que le navigateur ne le voie jamais sans.
            # shortcut: on garde la première IP du domaine (un CDN en a des dizaines) ; suffisant pour une position et un traceroute.
            located = {} if node_id == UNRESOLVED else {"addr": ip, **geo.locate(geo_reader, ip)}
            network.upsert_node(node_id, kind="internet", name=node_id, **located)
        return node_id

    def handle(pkt) -> None:
        if IP not in pkt:
            return
        learn(pkt)
        src, dst = endpoint(pkt[IP].src), endpoint(pkt[IP].dst)
        if src is None or dst is None or src == dst:
            return
        if src in STREAMING_DOMAINS or dst in STREAMING_DOMAINS:
            proto = "streaming"
        else:
            layer = pkt[TCP] if TCP in pkt else pkt[UDP] if UDP in pkt else None
            proto = protocol(layer.sport if layer else None, layer.dport if layer else None, icmp=ICMP in pkt)
        network.add_traffic(src, dst, proto, len(pkt))

    return handle


def start(network: Network, iface: str, subnet: ipaddress.IPv4Network, geo_reader=None) -> AsyncSniffer:
    """Capture en tâche de fond. Nécessite root ou CAP_NET_RAW."""
    sniffer = AsyncSniffer(iface=iface, filter="ip", store=False, prn=make_handler(network, subnet, geo_reader))
    sniffer.start()
    return sniffer
