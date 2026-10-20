import threading

from scapy.all import ARP, Ether, srp, traceroute as scapy_traceroute
from scapy.layers.inet6 import traceroute6 as scapy_traceroute6

from spaghet.model import Network

ALPHA = 0.3  # poids de la nouvelle mesure dans la moyenne glissante


def smooth(old: float | None, new: float) -> float:
    """Moyenne glissante exponentielle : lisse les pics ponctuels du Wi-Fi."""
    return new if old is None else old + ALPHA * (new - old)


def ping_all(network: Network, iface: str, my_ip: str, timeout: float = 2.0) -> None:
    """Mesure le RTT de chaque appareil du LAN par requête ARP unicast.

    ARP plutôt qu'ICMP : tout appareil IPv4 du LAN doit répondre à l'ARP, alors que beaucoup
    de téléphones et de PC Windows bloquent le ping. Nécessite root ou CAP_NET_RAW.
    """
    targets = [n for n in list(network.nodes.values()) if n.mac and n.ip != my_ip]  # pas soi-même
    if not targets:
        return
    packets = [Ether(dst=n.mac) / ARP(pdst=n.ip) for n in targets]
    answered, _ = srp(packets, iface=iface, timeout=timeout, verbose=False)
    for sent, reply in answered:
        rtt_ms = (reply.time - sent.sent_time) * 1000
        node = network.nodes.get(sent[ARP].pdst)
        old = node.rtt_ms if node else None
        network.upsert_node(sent[ARP].pdst, rtt_ms=round(smooth(old, rtt_ms), 2))


_trace_lock = threading.Lock()  # un traceroute à la fois : chacun envoie jusqu'à 30 paquets


def traceroute(network: Network, node_id: str, max_ttl: int = 30) -> int | None:
    """Nombre de sauts jusqu'à un nœud connu (SYN TCP vers le port 443), ou None s'il ne répond pas.

    Seulement vers un nœud déjà dans le graphe : jamais vers une IP arbitraire.
    """
    if not _trace_lock.acquire(blocking=False):
        raise RuntimeError("Un traceroute est déjà en cours")
    try:
        return _traceroute(network, node_id, max_ttl)
    finally:
        _trace_lock.release()


def _traceroute(network: Network, node_id: str, max_ttl: int) -> int | None:
    node = network.nodes.get(node_id)
    if node is None:
        raise KeyError(node_id)
    target = node.addr if node.kind == "internet" else node.ip
    if not target:
        raise ValueError(f"{node_id} n'a pas d'adresse IP connue")
    trace_fn = scapy_traceroute6 if ":" in target else scapy_traceroute  # destination Internet vue en IPv6
    answered, _ = trace_fn(target, dport=443, maxttl=max_ttl, timeout=2, verbose=False)
    trace = answered.get_trace().get(target, {})
    hops = min((ttl for ttl, (_hop, reached) in trace.items() if reached), default=None)
    network.upsert_node(node_id, seen=False, hops=hops)
    return hops
