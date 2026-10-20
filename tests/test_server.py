from fastapi.testclient import TestClient

from spaghet.server import app


def test_index_served():
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "<title>Spaghet</title>" in r.text


def test_websocket_sends_snapshot_then_diff():
    from spaghet import server
    server.PUSH_INTERVAL = 0.05
    server.network.upsert_node("10.9.9.1", kind="router")
    with TestClient(app).websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot"
        assert "10.9.9.1" in {n["data"]["id"] for n in first["nodes"]}
        server.network.upsert_node("10.9.9.2")
        diff = ws.receive_json()
        assert diff["type"] == "diff"
        assert [n["data"]["id"] for n in diff["nodes"]] == ["10.9.9.2"]


def test_traceroute_api(monkeypatch):
    from spaghet import probes, server

    class FakeResult:
        def get_trace(self):
            # TTL → (IP du saut, cible atteinte)
            return {"142.250.1.1": {1: ("192.168.1.1", False), 2: ("10.0.0.1", False), 9: ("142.250.1.1", True)}}

    monkeypatch.setattr(probes, "scapy_traceroute", lambda *a, **k: (FakeResult(), None))
    server.network.upsert_node("youtube.com", kind="internet", addr="142.250.1.1")
    server.network.upsert_node("(non résolu)", kind="internet")
    client = TestClient(app)

    assert client.post("/api/traceroute/youtube.com").json() == {"hops": 9}
    assert server.network.nodes["youtube.com"].hops == 9
    assert client.post("/api/traceroute/(non résolu)").status_code == 422
    assert client.post("/api/traceroute/8.8.8.8").status_code == 404  # pas une IP arbitraire
