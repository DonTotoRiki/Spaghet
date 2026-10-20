import ipaddress
import time

from scapy.all import DNS, ICMP, IP, TCP, UDP, AsyncSniffer, Ether, IPv6, Raw
from scapy.arch import in6_getifaddr

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


def local_prefixes6(iface: str) -> list[ipaddress.IPv6Network]:
    """Préfixes /64 des adresses IPv6 globales de l'interface : ce qui est dedans est sur le LAN."""
    return [ipaddress.IPv6Network(f"{addr}/64", strict=False) for addr, scope, name in in6_getifaddr()
            if name == iface and scope == 0]


def make_handler(network: Network, subnet: ipaddress.IPv4Network, geo_reader=None,
                 prefixes6: list[ipaddress.IPv6Network] = ()):
    """Callback scapy : chaque paquet IPv4 ou IPv6 devient du trafic sur un lien du graphe.

    Les IP Internet deviennent un nœud par domaine, nommé grâce aux réponses DNS et au SNI TLS vus passer.
    Un appareil du LAN vu en IPv6 est rattaché à son nœud IPv4 grâce à son adresse MAC.
    """
    names: dict[str, str] = {}  # IP Internet → nom d'hôte
    by_mac: dict[str, str] = {}  # MAC → IP du nœud LAN, reconstruit au plus une fois par seconde
    by_mac_built = 0.0
    errors: set[str] = set()     # types d'erreur déjà signalés, pour ne pas inonder le terminal

    def learn(pkt, l3) -> None:
        if UDP in pkt and pkt[UDP].sport == 53 and DNS in pkt and pkt[DNS].qr == 1 and pkt[DNS].qd:
            # Le nom demandé, pas la fin de la chaîne CNAME : « youtube.com » plutôt que « youtube-ui.l.google.com ».
            asked = pkt[DNS].qd[0].qname.decode(errors="replace")
            for rr in pkt[DNS].an or []:
                if rr.type in (1, 28):  # A, AAAA
                    names[rr.rdata] = asked
        elif TCP in pkt and pkt[TCP].dport == 443 and Raw in pkt:
            if host := tls_sni(bytes(pkt[Raw].load)):
                names[l3.dst] = host

    def lan_node(mac: str | None) -> str | None:
        """Nœud LAN (clé IPv4) qui porte cette MAC, ou None s'il est inconnu."""
        nonlocal by_mac_built
        if mac is None:
            return None
        if mac not in by_mac and time.monotonic() - by_mac_built > 1:
            by_mac.clear()
            by_mac.update({n.mac: n.ip for n in list(network.nodes.values()) if n.mac})
            by_mac_built = time.monotonic()
        return by_mac.get(mac)

    def endpoint(ip: str, mac: str | None) -> str | None:
        addr = ipaddress.ip_address(ip)
        if addr.is_multicast or addr.is_unspecified or addr == subnet.broadcast_address or ip == "255.255.255.255":
            return None  # diffusion : personne en particulier à l'autre bout
        if addr.version == 4 and addr in subnet:
            return ip
        if addr.version == 6 and (addr.is_link_local or addr.is_private or any(addr in p for p in prefixes6)):
            # Les appareils du LAN ont plusieurs adresses IPv6 (lien local, temporaires) : on les retrouve par la MAC.
            # Inconnue (appareil jamais vu en IPv4) : paquet ignoré plutôt qu'un nœud de plus par adresse.
            return lan_node(mac)
        node_id = registrable_domain(names[ip]) if ip in names else UNRESOLVED
        if node_id not in network.nodes:
            # Créé avec son type avant le trafic, pour que l'interface ne le voie jamais sans.
            # shortcut: on garde la première IP du domaine (un CDN en a des dizaines) ; suffisant pour une position et un traceroute.
            located = {} if node_id == UNRESOLVED else {"addr": ip, **geo.locate(geo_reader, ip)}
            network.upsert_node(node_id, kind="internet", name=node_id, **located)
        return node_id

    def handle(pkt) -> None:
        # Une erreur ici arrêterait définitivement la capture de scapy : un paquet bizarre est ignoré, jamais fatal.
        try:
            count(pkt)
        except Exception as e:
            if type(e).__name__ not in errors:
                errors.add(type(e).__name__)
                print(f"capture : paquet ignoré ({type(e).__name__} : {e})", flush=True)

    def count(pkt) -> None:
        l3 = pkt[IP] if IP in pkt else pkt[IPv6] if IPv6 in pkt else None
        if l3 is None:
            return
        learn(pkt, l3)
        ether = pkt[Ether] if Ether in pkt else None
        src = endpoint(l3.src, ether.src if ether else None)
        dst = endpoint(l3.dst, ether.dst if ether else None)
        if src is None or dst is None or src == dst:
            return
        if src in STREAMING_DOMAINS or dst in STREAMING_DOMAINS:
            proto = "streaming"
        else:
            layer = pkt[TCP] if TCP in pkt else pkt[UDP] if UDP in pkt else None
            icmp = ICMP in pkt or (IPv6 in pkt and pkt[IPv6].nh == 58)  # 58 : ICMPv6
            proto = protocol(layer.sport if layer else None, layer.dport if layer else None, icmp=icmp)
        network.add_traffic(src, dst, proto, len(pkt))

    return handle


def start(network: Network, iface: str, subnet: ipaddress.IPv4Network, geo_reader=None) -> AsyncSniffer:
    """Capture IPv4 et IPv6 en tâche de fond. Nécessite root ou CAP_NET_RAW."""
    handler = make_handler(network, subnet, geo_reader, local_prefixes6(iface))
    sniffer = AsyncSniffer(iface=iface, filter="ip or ip6", store=False, prn=handler)
    sniffer.start()
    return sniffer
