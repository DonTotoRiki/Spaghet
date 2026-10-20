import pytest

from spaghet.model import Network, link_key


def test_upsert_creates_then_updates():
    net = Network()
    net.upsert_node("192.168.1.10", now=1.0, mac="aa:bb:cc:dd:ee:ff")
    net.upsert_node("192.168.1.10", now=5.0, name="pc-salon", mac=None)

    node = net.nodes["192.168.1.10"]
    assert node.mac == "aa:bb:cc:dd:ee:ff"  # None n'efface pas la valeur connue
    assert node.name == "pc-salon"
    assert (node.first_seen, node.last_seen) == (1.0, 5.0)
    assert len(net.nodes) == 1


def test_upsert_rejects_unknown_field():
    with pytest.raises(AttributeError):
        Network().upsert_node("10.0.0.1", nom="faute de frappe")


def test_link_key_is_symmetric():
    assert link_key("10.0.0.2", "10.0.0.1") == link_key("10.0.0.1", "10.0.0.2")


def test_traffic_merges_both_directions():
    net = Network()
    net.add_traffic("10.0.0.1", "10.0.0.2", "http", 100, now=1.0)
    net.add_traffic("10.0.0.2", "10.0.0.1", "http", 50, now=2.0)
    net.add_traffic("10.0.0.1", "10.0.0.2", "dns", 10, now=3.0)

    assert len(net.links) == 1
    link = net.links[link_key("10.0.0.1", "10.0.0.2")]
    assert link.bytes_total == 160
    assert link.protocols == {"http": 150, "dns": 10}
    assert link.last_seen == 3.0
    # le trafic crée les nœuds inconnus et compte leurs octets
    assert net.nodes["10.0.0.1"].bytes_total == 160
    assert net.nodes["10.0.0.2"].bytes_total == 160


def test_snapshot_is_a_copy():
    net = Network()
    net.upsert_node("10.0.0.1", kind="router")
    net.add_traffic("10.0.0.1", "10.0.0.2", "dns", 10)

    snap = net.snapshot()
    assert {n["ip"] for n in snap["nodes"]} == {"10.0.0.1", "10.0.0.2"}
    assert (snap["links"][0]["a"], snap["links"][0]["b"]) == ("10.0.0.1", "10.0.0.2")
    snap["nodes"][0]["name"] = "modifié"  # une copie : l'interface la lit sans verrou
    assert all(n.name != "modifié" for n in net.nodes.values())


def test_info_update_does_not_mark_seen():
    net = Network()
    net.upsert_node("10.0.0.1", now=1.0)
    net.upsert_node("10.0.0.1", now=50.0, seen=False, name="pc")
    node = net.nodes["10.0.0.1"]
    assert (node.last_seen, node.name) == (1.0, "pc")


def test_prune_removes_silent_nodes_and_their_links():
    net = Network()
    net.upsert_node("10.0.0.1", now=1000.0)
    net.add_traffic("10.0.0.1", "10.0.0.2", "dns", 10, now=100.0)
    net.upsert_node("10.0.0.1", now=1000.0)

    assert net.prune(300, now=1000.0) == ["10.0.0.2"]
    assert list(net.nodes) == ["10.0.0.1"]
    assert net.links == {}
