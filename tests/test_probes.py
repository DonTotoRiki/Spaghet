from spaghet.probes import smooth


def test_smooth_first_value_is_kept():
    assert smooth(None, 12.0) == 12.0


def test_smooth_dampens_spike():
    assert smooth(2.0, 102.0) == 32.0  # 2 + 0.3 * 100


def test_traceroute_only_to_known_nodes(monkeypatch):
    import pytest

    from spaghet import probes
    from spaghet.model import Network

    class FakeResult:
        def get_trace(self):
            # TTL → (IP du saut, cible atteinte)
            return {"142.250.1.1": {1: ("192.168.1.1", False), 2: ("10.0.0.1", False), 9: ("142.250.1.1", True)}}

    monkeypatch.setattr(probes, "scapy_traceroute", lambda *a, **k: (FakeResult(), None))
    net = Network()
    net.upsert_node("youtube.com", kind="internet", addr="142.250.1.1")
    net.upsert_node("(non résolu)", kind="internet")

    assert probes.traceroute(net, "youtube.com") == 9
    assert net.nodes["youtube.com"].hops == 9
    with pytest.raises(ValueError):
        probes.traceroute(net, "(non résolu)")
    with pytest.raises(KeyError):
        probes.traceroute(net, "8.8.8.8")  # jamais une IP arbitraire


def test_ping_all_skips_my_own_machine(monkeypatch):
    from spaghet import probes
    from spaghet.model import Network

    sent = []
    monkeypatch.setattr(probes, "srp", lambda packets, **k: (sent.extend(packets) or [], []))
    net = Network()
    net.upsert_node("192.168.1.10", mac="3c:22:fb:00:00:10", name="nom quelconque")  # ma machine
    net.upsert_node("192.168.1.20", mac="3c:22:fb:00:00:20")
    probes.ping_all(net, "eth0", my_ip="192.168.1.10")
    assert [p.pdst for p in sent] == ["192.168.1.20"]
