import ipaddress

import pytest

from spaghet import __main__ as app
from spaghet.model import Network

SUBNET = ipaddress.IPv4Network("192.168.1.0/24")


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(app.capture, "local_prefixes6", lambda iface: [])
    b = app.Backend(Network(), None, "eth0", SUBNET, "192.168.1.10", "192.168.1.1")
    b.messages = []
    b.report = b.messages.append
    return b


def test_rescan_prunes_even_if_identification_fails(backend, monkeypatch):
    def fail(network, ips):
        raise OSError("No such device")

    monkeypatch.setattr(app.discovery, "scan", lambda network, iface: set())
    monkeypatch.setattr(app.discovery, "identify", fail)
    backend.network.upsert_node("192.168.1.30", now=0.0)  # parti depuis longtemps
    with pytest.raises(OSError):
        backend.rescan()
    assert "192.168.1.30" not in backend.network.nodes


def test_failed_first_scan_still_schedules_latency_and_rescan(backend, monkeypatch):
    scheduled = []

    def fail(network, iface):
        raise OSError("Network is down")  # Wi-Fi pas encore prêt après la veille

    monkeypatch.setattr(app, "every", lambda seconds, fn, *args: scheduled.append(fn))
    monkeypatch.setattr(app.capture, "start", lambda *a: "capture")
    monkeypatch.setattr(app.discovery, "scan", fail)
    backend.start(backend.messages.append)
    assert app.probes.ping_all in scheduled and backend.rescan in scheduled
    assert backend.messages[-1].startswith("Découverte incomplète")


def test_capture_pause_message_is_cleared_when_it_runs_again(backend, monkeypatch):
    results = iter([OSError("Network is down"), OSError("Network is down"), None, None])
    monkeypatch.setattr(app.capture, "ensure_running", lambda sniffer: (sniffer, next(results)))
    for _ in range(4):
        backend.watch_capture()
    assert backend.messages == ["Capture en pause, nouvel essai toutes les 5 s (Network is down)"] * 2 + [""]


def test_network_change_is_noted_once(backend, monkeypatch):
    other = ("eth0", SUBNET, "192.168.1.99", "192.168.1.1")  # nouvelle adresse après un changement de box
    monkeypatch.setattr(app.discovery, "current_network", lambda iface: other)
    backend.watch_network()
    backend.watch_network()
    assert backend.notes == ["Réseau changé depuis le lancement : relance Spaghet pour le cartographier"]
    assert len(backend.messages) == 1


def test_identification_never_runs_twice_at_once(backend, monkeypatch):
    calls = []

    def identify(network, ips):
        calls.append(set(ips))
        backend.identify_new()  # le rescan tombe pendant cette identification : il ne doit rien relancer

    monkeypatch.setattr(app.discovery, "identify", identify)
    backend.network.upsert_node("192.168.1.20")
    backend.identify_new()
    assert calls == [{"192.168.1.20"}]
    backend.network.upsert_node("192.168.1.21")
    monkeypatch.setattr(app.discovery, "identify", lambda network, ips: calls.append(set(ips)))
    backend.identify_new()  # le verrou a bien été rendu
    assert calls[-1] == {"192.168.1.21"}


def test_proxy_note_appears_when_only_a_rescan_succeeds(backend, monkeypatch):
    monkeypatch.setattr(app.discovery, "scan", lambda network, iface: {"9a:55:9a:55:9a:55"})
    monkeypatch.setattr(app.discovery, "identify", lambda network, ips: None)
    backend.rescan()
    backend.rescan()
    assert backend.notes == [app.PROXY_NOTE]  # une seule fois
    assert backend.messages == [app.PROXY_NOTE]  # affichée tout de suite


def test_repeated_error_is_printed_once(monkeypatch, capsys):
    class Stop(Exception):
        pass

    rounds = iter(range(5))

    def sleep(_seconds):
        if next(rounds) == 4:
            raise Stop

    class InlineThread:  # exécute la boucle tout de suite, dans le test
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    def ping():
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(app.time, "sleep", sleep)
    monkeypatch.setattr(app.threading, "Thread", InlineThread)
    with pytest.raises(Stop):
        app.every(10, ping)
    assert capsys.readouterr().out.count("Operation not permitted") == 1
