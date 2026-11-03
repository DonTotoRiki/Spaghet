import ipaddress

from scapy.all import DNS, DNSQR, DNSRR, ICMP, IP, TCP, UDP, Ether, ICMPv6EchoRequest, IPv6, Raw, sniff, wrpcap

from spaghet.capture import UNRESOLVED, make_handler, tls_sni
from spaghet.classify import protocol, registrable_domain
from spaghet.model import Network, link_key

SUBNET = ipaddress.IPv4Network("192.168.1.0/24")
GW, PC, TV = "192.168.1.1", "192.168.1.10", "192.168.1.20"
YT_VIDEO, YT_PAGE, UNKNOWN = "142.250.1.1", "142.250.2.2", "8.8.4.4"


def client_hello(host: str) -> bytes:
    """ClientHello TLS minimal (RFC 8446 §4.1.2), avec une extension avant le SNI pour tester le saut."""
    name = host.encode()
    sni = (len(name) + 3).to_bytes(2) + b"\x00" + len(name).to_bytes(2) + name
    exts = b"\x00\x0b\x00\x02\x01\x00" + b"\x00\x00" + len(sni).to_bytes(2) + sni  # ec_point_formats, server_name
    body = (b"\x03\x03" + b"\x00" * 32                    # version, random
            + b"\x20" + b"\x11" * 32                      # session id
            + b"\x00\x02\x13\x01" + b"\x01\x00"           # suites, compression
            + len(exts).to_bytes(2) + exts)
    handshake = b"\x01" + len(body).to_bytes(3) + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2) + handshake


def dns_answer(asked: str, ip: str) -> Ether:
    return Ether() / IP(src=GW, dst=PC) / UDP(sport=53, dport=40000) / DNS(
        qr=1, qd=[DNSQR(qname=asked)],
        an=[DNSRR(rrname=asked, type="CNAME", rdata="edge.l.google.com"),
            DNSRR(rrname="edge.l.google.com", type="A", rdata=ip)])


def test_protocol_by_port():
    assert protocol(51000, 443) == "web"
    assert protocol(53, 40000) == "dns"  # réponse : le port connu est la source
    assert protocol(40000, 40001) == "other"
    assert protocol(None, None, icmp=True) == "infra"


def test_registrable_domain():
    assert registrable_domain("rr3---sn-abc.googlevideo.com.") == "googlevideo.com"
    assert registrable_domain("www.YouTube.com") == "youtube.com"
    assert registrable_domain("news.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("localhost") == "localhost"


def test_tls_sni():
    assert tls_sni(client_hello("www.youtube.com")) == "www.youtube.com"
    assert tls_sni(client_hello("www.youtube.com")[:60]) is None  # ClientHello coupé entre deux segments
    assert tls_sni(b"GET / HTTP/1.1\r\n") is None


def test_pcap_replay(tmp_path):
    """Rejoue un pcap par le vrai chemin scapy (sniff offline), comme la capture en direct."""
    packets = [
        dns_answer("rr1.googlevideo.com", YT_VIDEO),
        Ether() / IP(src=YT_VIDEO, dst=PC) / UDP(sport=443, dport=50001) / (b"x" * 1000),  # QUIC : nommé par le DNS
        Ether() / IP(src=PC, dst=YT_PAGE) / TCP(sport=50000, dport=443) / client_hello("www.youtube.com"),
        Ether() / IP(src=PC, dst=UNKNOWN) / TCP(sport=50003, dport=443),                     # jamais nommé
        Ether() / IP(src=PC, dst=TV) / TCP(sport=50002, dport=8009),                         # Chromecast
        Ether() / IP(src=PC, dst=GW) / ICMP(),
        Ether() / IP(src=PC, dst="224.0.0.251") / UDP(sport=5353, dport=5353),               # multicast ignoré
        Ether() / IP(src=PC, dst="192.168.1.255") / UDP(sport=137, dport=137),              # broadcast ignoré
    ]
    pcap = tmp_path / "sample.pcap"
    wrpcap(str(pcap), packets)

    net = Network()
    sniff(offline=str(pcap), prn=make_handler(net, SUBNET), store=False)

    assert set(net.links) == {link_key(PC, GW), link_key(PC, TV), link_key(PC, "googlevideo.com"),
                              link_key(PC, "youtube.com"), link_key(PC, UNRESOLVED)}
    assert net.links[link_key(PC, "googlevideo.com")].protocols == {"streaming": len(packets[1])}
    assert net.links[link_key(PC, "youtube.com")].protocols == {"web": len(packets[2])}
    assert net.links[link_key(PC, TV)].protocols == {"streaming": len(packets[4])}
    assert net.links[link_key(PC, GW)].protocols == {"dns": len(packets[0]), "infra": len(packets[5])}
    for domain in ("googlevideo.com", "youtube.com", UNRESOLVED):
        assert net.nodes[domain].kind == "internet"
    assert not any(ip in net.nodes for ip in (YT_VIDEO, YT_PAGE, UNKNOWN))


def test_tick_computes_bitrate():
    net = Network()
    net.add_traffic(PC, GW, "web", 1000, now=0.0)
    net.tick(now=0.0)
    net.add_traffic(PC, GW, "web", 4000, now=1.5)
    net.add_traffic(GW, PC, "web", 1000, now=1.5)
    net.tick(now=2.0)
    link = net.links[link_key(PC, GW)]
    assert link.bps == 5000 * 8 / 2.0
    assert (link.a, link.bps_ab) == (GW, 1000 * 8 / 2.0)  # a = GW (« 192.168.1.1 » < « 192.168.1.10 »)


PC_MAC, GW_MAC = "3c:22:fb:00:00:10", "6c:e3:1a:c2:3d:42"
PREFIX6 = ipaddress.IPv6Network("2a02:a03f:6487:ae01::/64")
PC6, PC6_TEMP, GW6 = "2a02:a03f:6487:ae01::10", "2a02:a03f:6487:ae01:1be6:105f:d7d1:1f44", "fe80::6e3:1aff:fec2:3d42"
YT6 = "2a00:1450:4007:80c::200e"


def ipv6_network() -> Network:
    net = Network()
    net.upsert_node(PC, mac=PC_MAC)
    net.upsert_node(GW, mac=GW_MAC, kind="router")
    return net


def test_ipv6_replay(tmp_path):
    """Trafic IPv6 réel : nommé par un AAAA, rattaché aux nœuds IPv4 par la MAC, multicast ignoré."""
    to_router, to_pc = Ether(src=PC_MAC, dst=GW_MAC), Ether(src=GW_MAC, dst=PC_MAC)
    packets = [
        to_pc / IPv6(src=GW6, dst=PC6) / UDP(sport=53, dport=40000) / DNS(
            qr=1, qd=[DNSQR(qname="rr1.googlevideo.com", qtype="AAAA")],
            an=[DNSRR(rrname="rr1.googlevideo.com", type="AAAA", rdata=YT6)]),
        to_pc / IPv6(src=YT6, dst=PC6_TEMP) / UDP(sport=443, dport=50001) / (b"x" * 1200),  # adresse temporaire du PC
        to_router / IPv6(src=PC6, dst="2606:4700::1") / TCP(sport=50002, dport=443),          # jamais nommé
        to_router / IPv6(src="fe80::1:2:3:4", dst=GW6) / ICMPv6EchoRequest(),               # lien local, par la MAC
        Ether(src="aa:bb:cc:00:00:01", dst=GW_MAC) / IPv6(src="fe80::9", dst=GW6) / ICMPv6EchoRequest(),  # MAC inconnue
        to_router / IPv6(src=PC6, dst="ff02::fb") / UDP(sport=5353, dport=5353),            # multicast ignoré
    ]
    pcap = tmp_path / "ipv6.pcap"
    wrpcap(str(pcap), packets)

    net = ipv6_network()
    sniff(offline=str(pcap), prn=make_handler(net, SUBNET, prefixes6=[PREFIX6]), store=False)

    assert set(net.links) == {link_key(PC, GW), link_key(PC, "googlevideo.com"), link_key(PC, UNRESOLVED)}
    assert net.links[link_key(PC, "googlevideo.com")].protocols == {"streaming": len(packets[1])}
    assert net.links[link_key(PC, GW)].protocols == {"dns": len(packets[0]), "infra": len(packets[3])}
    assert net.nodes["googlevideo.com"].addr == YT6
    assert not any(":" in node_id for node_id in net.nodes)  # aucun nœud par adresse IPv6


def test_bad_packets_never_stop_the_capture():
    """Un paquet DNS illisible faisait planter le callback, ce qui arrête la capture de scapy pour toujours."""
    net = Network()
    handle = make_handler(net, SUBNET)
    garbage = Ether() / IP(src=GW, dst=PC) / UDP(sport=53, dport=40000) / Raw(b"\x12\x34\x81\x80\x00\x01\x00\x01" + b"\xff" * 8)
    handle(Ether(bytes(garbage)))  # ne doit pas lever d'exception
    handle(Ether() / IP(src=PC, dst=TV) / TCP(sport=50002, dport=8009))
    assert link_key(PC, TV) in net.links  # la suite est toujours comptée


def test_router_real_mac_is_learned_from_internet_traffic():
    """Proxy ARP : le routeur est connu par la MAC du proxy, mais son IPv6 de lien local utilise sa vraie MAC."""
    router_real_mac = "04:e3:1a:c2:3d:42"
    net = ipv6_network()  # passerelle enregistrée avec GW_MAC (celle vue en ARP)
    handle = make_handler(net, SUBNET, prefixes6=[PREFIX6], gateway=GW)
    handle(Ether(src=PC_MAC, dst=router_real_mac) / IPv6(src=PC6, dst=YT6) / TCP(sport=50000, dport=443))
    handle(Ether(src=router_real_mac, dst=PC_MAC) / IPv6(src=GW6, dst=PC6) / UDP(sport=53, dport=40000))
    assert link_key(PC, GW) in net.links  # le DNS IPv6 du routeur n'est plus perdu


def test_dead_capture_is_restarted(tmp_path):
    import time

    from scapy.all import AsyncSniffer

    from spaghet.capture import ensure_running

    pcap = tmp_path / "vide.pcap"
    wrpcap(str(pcap), [Ether() / IP(src=PC, dst=GW)])
    seen = []
    sniffer = AsyncSniffer(offline=str(pcap), prn=seen.append, store=False)  # se termine tout de suite, comme une coupure
    sniffer.start()
    sniffer.join()
    restarted, error = ensure_running(sniffer)
    assert restarted is not sniffer and error is not None
    restarted.join()
    assert len(seen) == 2  # même gestionnaire, relancé
    assert ensure_running(restarted)[0] is not restarted  # toujours relancée tant qu'elle s'arrête

    alive = AsyncSniffer(offline=str(pcap), prn=lambda p: time.sleep(1), store=False)
    alive.start()
    assert ensure_running(alive) == (alive, None)  # en marche : rien ne change
    alive.join()


def test_own_name_lookups_are_not_traffic():
    """L'identification interroge chaque appareil (NetBIOS, et LLMNR via systemd-resolved pour le DNS inverse) :
    vu sur le réseau réel, ça reliait la machine à tous les appareils du LAN."""
    net = Network()
    handle = make_handler(net, SUBNET)
    handle(Ether() / IP(src=PC, dst=TV) / UDP(sport=51234, dport=137))
    handle(Ether() / IP(src=TV, dst=PC) / UDP(sport=137, dport=51234))
    handle(Ether() / IP(src=PC, dst=TV) / TCP(sport=37638, dport=5355))
    handle(Ether() / IP(src=TV, dst=PC) / UDP(sport=5353, dport=5353))
    assert net.links == {}


def test_traceroute_answers_are_not_traffic():
    """Les « délai dépassé » renvoyés par chaque routeur traversé remplissaient le nœud « (non résolu) »."""
    from scapy.all import ICMPv6TimeExceeded

    net = Network()
    handle = make_handler(net, SUBNET)
    handle(Ether() / IP(src="80.10.20.30", dst=PC) / ICMP(type=11) / IP(src=PC, dst=YT_VIDEO) / TCP(dport=443))
    handle(Ether() / IP(src="80.10.20.31", dst=PC) / ICMP(type=3, code=3))
    handle(Ether() / IPv6(src="2001:db8::1", dst=PC6) / ICMPv6TimeExceeded())
    assert net.links == {}
    handle(Ether() / IP(src=PC, dst=GW) / ICMP())  # un vrai ping, lui, compte
    assert link_key(PC, GW) in net.links


def test_name_table_is_bounded(monkeypatch):
    from spaghet import capture

    monkeypatch.setattr(capture, "MAX_NAMES", 3)
    net = Network()
    handle = make_handler(net, SUBNET)
    for i in range(10):
        handle(dns_answer(f"site{i}.com", f"93.184.0.{i}"))
    handle(Ether() / IP(src=PC, dst="93.184.0.9") / TCP(sport=50000, dport=443))
    assert "site9.com" in net.nodes  # toujours nommé : seul l'ancien contenu a été oublié


def test_unreadable_dns_still_counts_as_traffic():
    net = Network()
    handle = make_handler(net, SUBNET)
    garbage = Ether() / IP(src=GW, dst=PC) / UDP(sport=53, dport=40000) / Raw(b"\x12\x34\x81\x80\x00\x01\x00\x01" + b"\xff" * 8)
    handle(Ether(bytes(garbage)))
    assert net.links[link_key(PC, GW)].protocols == {"dns": len(garbage)}


def test_private_addresses_outside_the_subnet_are_not_internet():
    net = Network()
    handle = make_handler(net, SUBNET, gateway=GW)
    for other in ("10.8.0.1", "100.64.3.4", "192.168.50.2", "169.254.10.10"):  # VPN, CGNAT, autre box, lien local
        handle(Ether() / IP(src=PC, dst=other) / TCP(sport=50000, dport=22))
        assert link_key(PC, other) in net.links
        assert net.nodes[other].kind == "private"
        assert "réseau privé" in net.nodes[other].name
    assert UNRESOLVED not in net.nodes  # rien n'a été mélangé au vrai Internet
    handle(Ether() / IP(src=PC, dst=UNKNOWN) / TCP(sport=50001, dport=443))
    assert net.nodes[UNRESOLVED].kind == "internet"  # une IP publique reste une destination Internet
