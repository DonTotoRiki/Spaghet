import ipaddress
import re
import socket
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from manuf import manuf
from scapy.all import IP, UDP, RandShort, arping, conf, get_if_hwaddr, sr
from scapy.layers.netbios import NBNSHeader, NBNSNodeStatusRequest, NBNSNodeStatusResponse
from zeroconf import ServiceBrowser, Zeroconf, ZeroconfServiceTypes

from spaghet.classify import classify
from spaghet.model import Network

_oui = manuf.MacParser()

# Au-delà, un scan ARP prend trop longtemps et inonde le réseau.
MAX_HOSTS = 1024
# Proxy ARP (VM, box, Wi-Fi d'entreprise) : une même MAC répond pour plus de la moitié des adresses du sous-réseau,
# et au moins pour ce nombre. Un répéteur Wi-Fi qui traduit les MAC ne répond, lui, que pour ses vrais clients.
PROXY_ARP_THRESHOLD = 8


def local_network(iface: str | None = None, routes=None) -> tuple[str, ipaddress.IPv4Network, str, str | None]:
    """Renvoie (interface, sous-réseau, ip locale, passerelle) à partir de la table de routage de scapy."""
    routes = conf.route.routes if routes is None else routes
    # Plusieurs routes par défaut (câble + Wi-Fi) : la plus petite métrique est celle qu'utilise le système.
    default = min((r for r in routes if r[1] == 0 and (iface is None or r[3] == iface)), key=lambda r: r[5], default=None)
    if iface is None:
        if default is None:
            raise RuntimeError("Pas de route par défaut : précise l'interface avec --iface")
        iface = default[3]
    gateway = default[2] if default else None

    # Route directe (sans passerelle) la plus précise qui contient notre propre adresse, hors /32.
    candidates = []
    for net, mask, gw, r_iface, addr, _metric in routes:
        if r_iface != iface or gw != "0.0.0.0" or mask in (0, 0xFFFFFFFF):
            continue
        subnet = ipaddress.IPv4Network((net, bin(mask).count("1")))
        if ipaddress.IPv4Address(addr) in subnet:
            candidates.append((subnet, addr))
    if not candidates:
        raise RuntimeError(f"Aucun sous-réseau IPv4 trouvé sur {iface}")
    subnet, addr = max(candidates, key=lambda c: c[0].prefixlen)
    return iface, subnet, addr, gateway


def current_network(iface: str | None = None) -> tuple[str, ipaddress.IPv4Network, str, str | None]:
    """Comme local_network, mais relit la table de routage du système (scapy la garde en mémoire depuis son import)."""
    conf.route.resync()
    return local_network(iface)


def proxy_arp_macs(replies: list[tuple[str, str]], num_addresses: int) -> set[str]:
    """MAC qui répondent pour la majorité des adresses (ip, mac) : leurs réponses ne prouvent pas qu'un appareil existe."""
    counts = Counter(mac for _ip, mac in replies)
    limit = max(PROXY_ARP_THRESHOLD, num_addresses // 2)
    return {mac for mac, n in counts.items() if n > limit}


def scan(network: Network, iface: str | None = None, timeout: float = 2.0) -> set[str]:
    """Scan ARP du sous-réseau local. Nécessite root ou CAP_NET_RAW. Renvoie les MAC de proxy ARP ignorées."""
    iface, subnet, my_ip, gateway = local_network(iface)
    if subnet.num_addresses > MAX_HOSTS:
        # Trop grand pour tout interroger (/16 : 65 536 adresses) : seulement le voisinage de la machine.
        subnet = ipaddress.ip_interface(f"{my_ip}/24").network

    network.upsert_node(my_ip, mac=get_if_hwaddr(iface), name="cette machine")
    if gateway:
        network.upsert_node(gateway, kind="router")

    answered, _ = arping(str(subnet), iface=iface, timeout=timeout, verbose=False)
    replies = [(reply.psrc, reply.hwsrc) for _sent, reply in answered]
    proxies = proxy_arp_macs(replies, subnet.num_addresses)
    for ip, mac in replies:
        if mac not in proxies or ip == gateway:
            network.upsert_node(ip, mac=mac)
    return proxies


def _reverse_dns(ip: str) -> str | None:
    try:
        name = socket.gethostbyaddr(ip)[0]
    except OSError:
        return None
    # Nom synthétique de systemd-resolved pour la passerelle, n'apprend rien.
    return None if name == "_gateway" else unescape_dns(name)


def unescape_dns(name: str) -> str:
    """Échappements DNS \\DDD (décimal) : « 04\\05800 » → « 04:00 »."""
    return re.sub(r"\\(\d{3})", lambda m: chr(int(m[1])), name)


def _mdns(timeout: float) -> dict[str, tuple[str, set[str]]]:
    """Écoute mDNS pendant `timeout` s. Renvoie ip → (nom, types de services)."""
    found: dict[str, tuple[str, set[str]]] = {}
    zc = Zeroconf()
    try:
        types = ZeroconfServiceTypes.find(zc=zc, timeout=timeout)
        seen: set[tuple[str, str]] = set()
        browsers = [
            ServiceBrowser(zc, t, handlers=[lambda zeroconf, service_type, name, state_change:
                                            seen.add((service_type, name))])
            for t in types
        ]
        time.sleep(timeout)
        for b in browsers:
            b.cancel()
        for service_type, name in seen:
            info = zc.get_service_info(service_type, name, timeout=1000)
            if not info:
                continue
            host = (info.server or "").removesuffix(".").removesuffix(".local")
            for ip in info.parsed_addresses():
                old_host, services = found.get(ip, (host, set()))
                services.add(service_type.removesuffix(".local."))
                found[ip] = (old_host or host, services)
    finally:
        zc.close()
    return found


def _netbios(ips: list[str], timeout: float) -> dict[str, str]:
    """Requête NetBIOS « node status » : nom des machines Windows/Samba."""
    packets = [IP(dst=ip) / UDP(sport=RandShort(), dport=137) / NBNSHeader() / NBNSNodeStatusRequest() for ip in ips]
    answered, _ = sr(packets, timeout=timeout, verbose=False)
    names = {}
    for sent, reply in answered:
        if NBNSNodeStatusResponse in reply and reply[NBNSNodeStatusResponse].NODE_NAME:
            raw = reply[NBNSNodeStatusResponse].NODE_NAME[0].NETBIOS_NAME
            names[sent[IP].dst] = raw.decode(errors="replace").strip("\x00 ")
    return names


def identify(network: Network, ips, timeout: float = 3.0) -> None:
    """Fabricant, nom et type des appareils du LAN donnés. Le premier nom trouvé gagne : DNS inverse, mDNS, NetBIOS."""
    nodes = [n for ip in ips if (n := network.nodes.get(ip)) and n.kind != "internet"]
    if not nodes:
        return
    names: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=32) as pool:
        for node, name in zip(nodes, pool.map(_reverse_dns, [n.ip for n in nodes])):
            if name:
                names[node.ip] = name

    mdns = _mdns(timeout)
    for ip, (host, _services) in mdns.items():
        if host:
            names.setdefault(ip, host)

    missing = [n.ip for n in nodes if n.ip not in names and n.name is None]
    if missing:
        for ip, name in _netbios(missing, timeout).items():
            names.setdefault(ip, name)

    for node in nodes:
        vendor = _oui.get_manuf_long(node.mac) if node.mac else None
        services = sorted(mdns.get(node.ip, ("", set()))[1])
        fields = {"vendor": vendor, "services": services or None}
        if node.name is None:
            fields["name"] = names.get(node.ip)
        if node.kind == "unknown":
            fields["kind"] = classify(vendor, services, node.mac)
        network.upsert_node(node.ip, seen=False, **fields)
