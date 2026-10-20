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
