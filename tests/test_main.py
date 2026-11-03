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
