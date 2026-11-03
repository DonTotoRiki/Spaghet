import ipaddress

import pytest

from spaghet.discovery import local_network

# Table de routage réelle de scapy (format : net, mask, gw, iface, addr, metric).
ROUTES = [
    (2130706432, 4278190080, "0.0.0.0", "lo", "127.0.0.1", 0),
    (3232268709, 4294967295, "0.0.0.0", "enp1s0", "192.168.129.165", 0),
    (0, 0, "192.168.128.1", "enp1s0", "192.168.129.165", 100),
    (3232268288, 4294966784, "0.0.0.0", "enp1s0", "192.168.129.165", 100),
    (3758096384, 4026531840, "0.0.0.0", "enp1s0", "192.168.129.165", 250),  # multicast 224/4
]


def test_local_network_from_default_route():
    iface, subnet, ip, gw = local_network(routes=ROUTES)
    assert iface == "enp1s0"
    assert subnet == ipaddress.IPv4Network("192.168.128.0/23")
    assert ip == "192.168.129.165"
    assert gw == "192.168.128.1"


def test_local_network_unknown_iface():
    with pytest.raises(RuntimeError):
        local_network("wlan0", routes=ROUTES)


def test_proxy_arp_replies_are_ignored(monkeypatch):
    from types import SimpleNamespace

    from spaghet import discovery
    from spaghet.model import Network

    # Comme sur un réseau à proxy ARP : la même MAC répond pour les 512 adresses, plus deux vrais appareils.
    proxy = "9a:55:9a:55:9a:55"
    replies = [(None, SimpleNamespace(psrc=str(ip), hwsrc=proxy)) for ip in ipaddress.IPv4Network("192.168.128.0/23")]
    replies[10] = (None, SimpleNamespace(psrc="192.168.128.10", hwsrc="3c:22:fb:00:00:01"))
    replies[20] = (None, SimpleNamespace(psrc="192.168.128.20", hwsrc="3c:22:fb:00:00:02"))
    monkeypatch.setattr(discovery, "arping", lambda *a, **k: (replies, []))
    monkeypatch.setattr(discovery, "get_if_hwaddr", lambda iface: "3c:22:fb:ff:ff:ff")
    monkeypatch.setattr(discovery.conf.route, "routes", ROUTES)

    net = Network()
    assert discovery.scan(net) == {proxy}
    assert set(net.nodes) == {"192.168.129.165", "192.168.128.1", "192.168.128.10", "192.168.128.20"}
    assert net.nodes["192.168.128.1"].kind == "router"


def test_unescape_dns():
    from spaghet.discovery import unescape_dns

    assert unescape_dns(r"04\058e3\0581a") == "04:e3:1a"
    assert unescape_dns("Living-Room-TV") == "Living-Room-TV"


def test_wifi_repeater_is_not_a_proxy():
    from spaghet.discovery import proxy_arp_macs

    # Répéteur qui traduit les MAC : 12 vrais clients derrière la même MAC, sur un /24.
    repeater = [(f"192.168.1.{i}", "aa:aa:aa:00:00:01") for i in range(20, 32)]
    assert proxy_arp_macs(repeater, 256) == set()
    # Vrai proxy ARP : la même MAC pour 200 des 256 adresses.
    proxy = [(f"192.168.1.{i}", "9a:55:9a:55:9a:55") for i in range(1, 201)]
    assert proxy_arp_macs(proxy + repeater, 256) == {"9a:55:9a:55:9a:55"}


def test_default_route_with_lowest_metric():
    # Wi-Fi listé en premier mais métrique 600, câble en 100 : le système passe par le câble.
    routes = [
        (0, 0, "192.168.1.1", "wlp2s0", "192.168.1.50", 600),
        (3232235776, 4294967040, "0.0.0.0", "wlp2s0", "192.168.1.50", 600),
        (0, 0, "192.168.1.1", "enp1s0", "192.168.1.40", 100),
        (3232235776, 4294967040, "0.0.0.0", "enp1s0", "192.168.1.40", 100),
    ]
    iface, _subnet, ip, _gw = local_network(routes=routes)
    assert (iface, ip) == ("enp1s0", "192.168.1.40")


def test_identify_skips_internet_nodes(monkeypatch):
    from spaghet import discovery
    from spaghet.model import Network

    asked = []
    monkeypatch.setattr(discovery, "_reverse_dns", lambda ip: asked.append(ip) or None)
    monkeypatch.setattr(discovery, "_mdns", lambda timeout: {})
    monkeypatch.setattr(discovery, "_netbios", lambda ips, timeout: asked.extend(ips) or {})
    net = Network()
    net.upsert_node("192.168.1.20")
    net.upsert_node("youtube.com", kind="internet", name="youtube.com")
    discovery.identify(net, {"192.168.1.20", "youtube.com", "parti.depuis"})
    assert set(asked) == {"192.168.1.20"}  # jamais de requête vers une destination Internet ni un nœud disparu


def test_big_subnet_scans_only_the_neighbourhood(monkeypatch):
    from spaghet import discovery
    from spaghet.model import Network

    scanned = []
    monkeypatch.setattr(discovery, "arping", lambda net, **k: scanned.append(net) or ([], []))
    monkeypatch.setattr(discovery, "get_if_hwaddr", lambda iface: "3c:22:fb:ff:ff:ff")
    monkeypatch.setattr(discovery.conf.route, "routes", [
        (0, 0, "10.0.0.1", "eth0", "10.0.42.7", 100),
        (167772160, 4294901760, "0.0.0.0", "eth0", "10.0.42.7", 100),  # 10.0.0.0/16
    ])
    discovery.scan(Network())
    assert scanned == ["10.0.42.0/24"]


def test_identify_survives_mdns_and_netbios_failures(monkeypatch):
    from spaghet import discovery
    from spaghet.model import Network

    def fail(*_args):
        raise OSError("No such device")

    monkeypatch.setattr(discovery, "_reverse_dns", lambda ip: None)
    monkeypatch.setattr(discovery, "_mdns", fail)
    monkeypatch.setattr(discovery, "_netbios", fail)
    net = Network()
    net.upsert_node("192.168.1.10", mac="00:1b:21:aa:bb:cc")  # OUI Intel
    discovery.identify(net, ["192.168.1.10"])
    assert net.nodes["192.168.1.10"].vendor  # le fabricant, calculé localement, n'est pas perdu


def test_scan_keeps_the_real_mac_answered_after_the_proxy(monkeypatch):
    from types import SimpleNamespace

    from spaghet import discovery
    from spaghet.model import Network

    # Le proxy répond pour toutes les adresses, y compris celles des appareils présents, et souvent avant eux.
    proxy = "9a:55:9a:55:9a:55"
    replies = [(None, SimpleNamespace(psrc=str(ip), hwsrc=proxy)) for ip in ipaddress.IPv4Network("192.168.128.0/23")]
    replies += [(None, SimpleNamespace(psrc="192.168.128.10", hwsrc="3c:22:fb:00:00:01")),
                (None, SimpleNamespace(psrc="192.168.128.1", hwsrc="04:e3:1a:c2:3d:42"))]  # vraie MAC de la passerelle
    monkeypatch.setattr(discovery, "arping", lambda *a, **k: (replies if k.get("multi") else replies[:512], []))
    monkeypatch.setattr(discovery, "get_if_hwaddr", lambda iface: "3c:22:fb:ff:ff:ff")
    monkeypatch.setattr(discovery.conf.route, "routes", ROUTES)

    net = Network()
    assert discovery.scan(net) == {proxy}
    assert net.nodes["192.168.128.10"].mac == "3c:22:fb:00:00:01"
    assert net.nodes["192.168.128.1"].mac == "04:e3:1a:c2:3d:42"


def test_identify_skips_private_addresses_of_other_networks(monkeypatch):
    from spaghet import discovery
    from spaghet.model import Network

    asked = []
    monkeypatch.setattr(discovery, "_reverse_dns", lambda ip: asked.append(ip) or None)
    monkeypatch.setattr(discovery, "_mdns", lambda timeout: {})
    monkeypatch.setattr(discovery, "_netbios", lambda ips, timeout: {})
    net = Network()
    net.upsert_node("10.8.0.1", kind="private", name="10.8.0.1 (réseau privé)")
    discovery.identify(net, {"10.8.0.1"})
    assert asked == []
