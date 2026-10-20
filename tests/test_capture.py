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
