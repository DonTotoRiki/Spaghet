import asyncio
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from spaghet import probes
from spaghet.model import Network

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PUSH_INTERVAL = 1.0  # secondes entre deux diffs envoyés au navigateur

network = Network()
app = FastAPI(title="Spaghet")


@app.get("/api/snapshot")
def snapshot():
    return network.snapshot()


_trace_lock = threading.Lock()  # un traceroute à la fois : chacun envoie jusqu'à 30 paquets


@app.post("/api/traceroute/{node_id:path}")
def traceroute(node_id: str):
    if node_id not in network.nodes:
        raise HTTPException(404, "Nœud inconnu")
    if not _trace_lock.acquire(blocking=False):
        raise HTTPException(409, "Un traceroute est déjà en cours")
    try:
        return {"hops": probes.traceroute(network, node_id)}
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        _trace_lock.release()


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    snap = network.snapshot()
    await socket.send_json({"type": "snapshot", **snap})
    since = snap["now"]
    try:
        while True:
            await asyncio.sleep(PUSH_INTERVAL)
            diff = network.diff_since(since)
            since = diff["now"]
            if diff["nodes"] or diff["links"] or diff["removed"]:
                await socket.send_json({"type": "diff", **diff})
    except WebSocketDisconnect:
        pass


# Monté en dernier : "/" attrape tout ce qui n'est pas une route d'API.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
