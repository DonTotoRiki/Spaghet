import ipaddress
import socket
import time
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


def local_network(iface: str | None = None, routes=None) -> tuple[str, ipaddress.IPv4Network, str, str | None]:
    """Renvoie (interface, sous-réseau, ip locale, passerelle) à partir de la table de routage de scapy."""
    routes = conf.route.routes if routes is None else routes
    default = next((r for r in routes if r[1] == 0 and (iface is None or r[3] == iface)), None)
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


def scan(network: Network, iface: str | None = None, timeout: float = 2.0) -> None:
    """Scan ARP du sous-réseau local. Nécessite root ou CAP_NET_RAW."""
    iface, subnet, my_ip, gateway = local_network(iface)
    if subnet.num_addresses > MAX_HOSTS:
        raise RuntimeError(f"{subnet} est trop grand ({subnet.num_addresses} adresses, max {MAX_HOSTS})")

    network.upsert_node(my_ip, mac=get_if_hwaddr(iface), name="cette machine")
    if gateway:
        network.upsert_node(gateway, kind="router")

    answered, _ = arping(str(subnet), iface=iface, timeout=timeout, verbose=False)
    for _sent, reply in answered:
        network.upsert_node(reply.psrc, mac=reply.hwsrc)


def _reverse_dns(ip: str) -> str | None:
    try:
        name = socket.gethostbyaddr(ip)[0]
    except OSError:
        return None
    # Nom synthétique de systemd-resolved pour la passerelle, n'apprend rien.
    return None if name == "_gateway" else name


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


def identify(network: Network, timeout: float = 3.0, only_new: bool = False) -> None:
    """Fabricant, nom et type des nœuds déjà découverts. Le premier nom trouvé gagne : DNS inverse, mDNS, NetBIOS.

    only_new : seulement les nœuds sans fabricant ni nom, pour les rescans périodiques.
    """
    nodes = [n for n in list(network.nodes.values()) if not only_new or (n.vendor is None and n.name is None)]
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
