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


def test_ping_all_ignores_proxy_arp_answers(monkeypatch):
    """Sur un réseau à proxy ARP, le proxy répond aussi pour un appareil parti : ça ne prouve pas sa présence."""
    from types import SimpleNamespace

    from scapy.all import ARP, Ether

    from spaghet import probes
    from spaghet.model import Network

    def fake_srp(packets, **k):
        answers = []
        for p in packets:
            p.sent_time = 100.0
            hwsrc = "9a:55:9a:55:9a:55" if p[ARP].pdst == "192.168.1.30" else p[Ether].dst  # .30 est parti
            answers.append((p, SimpleNamespace(hwsrc=hwsrc, time=100.002)))
        return answers, []

    monkeypatch.setattr(probes, "srp", fake_srp)
    net = Network()
    net.upsert_node("192.168.1.20", mac="3c:22:fb:00:00:20", now=0.0)
    net.upsert_node("192.168.1.30", mac="3c:22:fb:00:00:30", now=0.0)
    probes.ping_all(net, "eth0", my_ip="192.168.1.10")
    assert net.nodes["192.168.1.20"].rtt_ms == 2.0
    assert net.nodes["192.168.1.30"].rtt_ms is None
    assert net.nodes["192.168.1.30"].last_seen == 0.0  # toujours considéré muet : il sera grisé puis retiré


def test_traceroute_intercepted_at_first_hop(monkeypatch):
    import pytest

    from spaghet import probes
    from spaghet.model import Network

    class Intercepted:
        def get_trace(self):
            return {"185.15.58.224": {1: ("185.15.58.224", True)}}  # « atteint » dès le premier saut

    monkeypatch.setattr(probes, "scapy_traceroute", lambda *a, **k: (Intercepted(), None))
    net = Network()
    net.upsert_node("wikipedia.org", kind="internet", addr="185.15.58.224")
    with pytest.raises(ValueError, match="interceptée"):
        probes.traceroute(net, "wikipedia.org")
    assert net.nodes["wikipedia.org"].hops is None


def test_ping_all_keeps_the_real_answer_after_the_proxy(monkeypatch):
    """Le proxy ARP répond avant l'appareil présent : la vraie réponse, arrivée en second, doit compter."""
    from types import SimpleNamespace

    from scapy.all import ARP

    from spaghet import probes
    from spaghet.model import Network

    def fake_srp(packets, **k):
        assert k.get("multi")  # sinon scapy ne garde que la première réponse, celle du proxy
        p = packets[0]
        p.sent_time = 100.0
        return [(p, SimpleNamespace(hwsrc="9a:55:9a:55:9a:55", time=100.001)),
                (p, SimpleNamespace(hwsrc="3c:22:fb:00:00:20", time=100.004)),
                (p, SimpleNamespace(hwsrc="3c:22:fb:00:00:20", time=100.009))], []  # réponse répétée : ignorée

    monkeypatch.setattr(probes, "srp", fake_srp)
    net = Network()
    net.upsert_node("192.168.1.20", mac="3c:22:fb:00:00:20", now=0.0)
    probes.ping_all(net, "eth0", my_ip="192.168.1.10")
    assert net.nodes["192.168.1.20"].rtt_ms == 4.0
    assert net.nodes["192.168.1.20"].last_seen > 0.0


def test_failed_traceroute_clears_the_old_hop_count(monkeypatch):
    import pytest

    from spaghet import probes
    from spaghet.model import Network

    traces = iter([
        {"142.250.1.1": {9: ("142.250.1.1", True)}},          # 1er essai : 9 sauts
        {"142.250.1.1": {1: ("192.168.1.1", False)}},         # 2e : la cible ne répond plus
        {"142.250.1.1": {9: ("142.250.1.1", True)}},
        {"142.250.1.1": {1: ("142.250.1.1", True)}},          # 4e : intercepté au premier saut
    ])
    result = lambda: type("R", (), {"get_trace": lambda self, t=next(traces): t})()
    monkeypatch.setattr(probes, "scapy_traceroute", lambda *a, **k: (result(), None))
    net = Network()
    net.upsert_node("youtube.com", kind="internet", addr="142.250.1.1")

    assert probes.traceroute(net, "youtube.com") == 9
    assert probes.traceroute(net, "youtube.com") is None
    assert net.nodes["youtube.com"].hops is None  # plus de « Sauts : 9 » à côté de « La cible n'a pas répondu »
    probes.traceroute(net, "youtube.com")
    with pytest.raises(ValueError, match="interceptée"):
        probes.traceroute(net, "youtube.com")
    assert net.nodes["youtube.com"].hops is None
