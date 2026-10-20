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


def test_snapshot_is_cytoscape_format():
    net = Network()
    net.upsert_node("10.0.0.1", kind="router")
    net.add_traffic("10.0.0.1", "10.0.0.2", "dns", 10)

    snap = net.snapshot()
    ids = {n["data"]["id"] for n in snap["nodes"]}
    assert ids == {"10.0.0.1", "10.0.0.2"}
    edge = snap["links"][0]["data"]
    assert (edge["source"], edge["target"]) == ("10.0.0.1", "10.0.0.2")


def test_diff_since_returns_only_changes():
    net = Network()
    net.upsert_node("10.0.0.1", now=1.0)
    net.upsert_node("10.0.0.2", now=2.0)
    net.add_traffic("10.0.0.1", "10.0.0.3", "dns", 10, now=3.0)

    diff = net.diff_since(2.5)
    assert {n["data"]["id"] for n in diff["nodes"]} == {"10.0.0.1", "10.0.0.3"}
    assert [l["data"]["id"] for l in diff["links"]] == ["10.0.0.1-10.0.0.3"]


def test_info_update_does_not_mark_seen():
    net = Network()
    net.upsert_node("10.0.0.1", now=1.0)
    net.upsert_node("10.0.0.1", now=50.0, seen=False, name="pc")
    node = net.nodes["10.0.0.1"]
    assert (node.last_seen, node.updated) == (1.0, 50.0)


def test_prune_removes_silent_nodes_and_their_links():
    net = Network()
    net.upsert_node("10.0.0.1", now=1000.0)
    net.add_traffic("10.0.0.1", "10.0.0.2", "dns", 10, now=100.0)
    net.upsert_node("10.0.0.1", now=1000.0)

    removed = net.prune(300, now=1000.0)
    assert set(removed) == {"10.0.0.2", "10.0.0.1-10.0.0.2"}
    assert list(net.nodes) == ["10.0.0.1"]
    assert net.links == {}
    assert set(net.diff_since(999.0)["removed"]) == set(removed)
