import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # pas besoin d'écran pour tester

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from spaghet.graph_view import CLOUD, main_protocol, particle_positions
from spaghet.gui import MainWindow
from spaghet.map_view import project
from spaghet.model import Network

GW, PC, TV = "192.168.1.1", "192.168.1.10", "192.168.1.20"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def sample_network() -> Network:
    net = Network()
    net.upsert_node(GW, kind="router", rtt_ms=0.4)
    net.upsert_node(PC, kind="pc", name="<b>pc</b>", rtt_ms=3.0)
    net.upsert_node(TV, kind="iot", name="tv", rtt_ms=40.0)
    net.upsert_node("googlevideo.com", kind="internet", name="googlevideo.com", lat=37.4, lon=-122.1, city="Mountain View")
    net.upsert_node("youtube.com", kind="internet", name="youtube.com")
    net.add_traffic("googlevideo.com", PC, "streaming", 5_000_000)
    net.add_traffic(PC, "youtube.com", "web", 20_000)
    net.add_traffic(PC, TV, "streaming", 300_000)
    net.tick(now=0.0)
    net.add_traffic("googlevideo.com", PC, "streaming", 2_000_000)
    net.tick(now=2.0)
    return net


def test_particles():
    assert particle_positions(100, 500, 0, True) == []  # trop peu de débit
    toward_b = particle_positions(100, 1e6, 1.0, True)
    toward_a = particle_positions(100, 1e6, 1.0, False)
    assert toward_b and all(0 <= p <= 100 for p in toward_b)
    assert sorted(100 - p for p in toward_b) == sorted(toward_a)
    assert len(particle_positions(100, 1e8, 0, True)) > len(particle_positions(100, 1e4, 0, True))  # plus serrés


def test_main_protocol():
    assert main_protocol({"protocols": {"web": 10, "streaming": 50}}) == "streaming"
    assert main_protocol({"protocols": {}}) == "other"


def test_project_web_mercator():
    assert project(0, 0, 0) == pytest.approx((128, 128))
    x, y = project(48.85, 2.35, 2)  # Paris : moitié droite, moitié haute
    assert 512 < x < 540 and y < 512


def test_window_shows_network(app, tmp_path):
    window = MainWindow(sample_network())
    window.resize(1200, 800)
    window.refresh()
    graph = window.graph
    assert set(graph.pos) >= {GW, PC, TV, "googlevideo.com"}
    assert window.map.points[0][0] == "googlevideo.com"

    window.select(PC)
    assert window.panel.isVisibleTo(window)
    assert window.panel.title.text() == "<b>pc</b>"  # affiché tel quel, jamais interprété en HTML
    assert window.panel.connections.count() == 3

    graph.toggle_collapse()
    ids, edges = graph._visible()
    assert CLOUD in ids and "googlevideo.com" not in ids
    agg = next(e for e in edges if e[3] == "agg")
    assert agg[0] == PC and agg[2]["bps"] > 0

    assert graph.export_png(str(tmp_path / "graphe.png"))
    assert (tmp_path / "graphe.png").stat().st_size > 1000


def test_map_picks_the_closest_point(app):
    from spaghet.map_view import MapView

    m = MapView()
    m.resize(800, 600)
    small = m._screen(*project(0, 0, m.zoom))
    shift = 12 / (256 * 2 ** m.zoom) * 360  # 12 px vers l'est
    m.points = [("petit", 0.0, 0.0, "#f00", 4, ""), ("gros", 0.0, shift, "#0f0", 14, "")]
    assert m.point_at(small)[0] == "petit"
    assert m.point_at(small + QPointF(12, 0))[0] == "gros"
    assert m.point_at(small + QPointF(200, 0)) is None


def test_panel_keeps_its_rows_between_refreshes(app):
    window = MainWindow(sample_network())
    window.refresh()
    window.select(PC)
    first_item = window.panel.connections.item(0)
    first_label = window.panel._row_values[0]
    window.network.add_traffic(PC, TV, "streaming", 10)  # les chiffres changent, pas la liste
    window.refresh()
    assert window.panel.connections.item(0) is first_item  # pas reconstruite : le défilement est conservé
    assert window.panel._row_values[0] is first_label


def test_idle_graph_is_not_redrawn(app):
    from spaghet.graph_view import GraphView

    graph = GraphView()
    graph.isVisible = lambda: True
    calls = []
    graph.update = lambda: calls.append(1)
    graph.temperature, graph._animating = 0.0, False
    graph._frame()
    assert calls == []  # figé, sans particules : pas de dessin
    graph._animating = True
    graph._frame()
    assert calls == [1]


def test_my_machine_stays_out_of_the_internet_box(app):
    import random

    from spaghet.graph_view import GraphView

    # Positions de départ tirées au hasard : un seul tirage avait laissé passer une correction qui ratait 39 fois sur 40.
    for seed in range(20):
        random.seed(seed)
        net = Network()
        net.upsert_node(GW, kind="router", rtt_ms=0.4)
        net.upsert_node(PC, kind="pc", rtt_ms=0.4)
        net.upsert_node(TV, kind="iot", rtt_ms=40.0)
        domains = [f"site{i}.com" for i in range(1 + seed % 12)]
        for d in domains:
            net.upsert_node(d, kind="internet", name=d)
            net.add_traffic(PC, d, "web", 1000)  # ta machine est reliée à toutes les destinations
        graph = GraphView()
        graph.update_data(net.snapshot())  # pose le graphe (300 pas)
        box = graph.cloud_box(domains)
        for lan in (GW, PC, TV):
            assert not box.contains(QPointF(*graph.pos[lan])), (seed, lan)


def test_click_radius_follows_zoom(app):
    from spaghet.graph_view import GraphView

    graph = GraphView()
    graph.resize(800, 600)
    graph.update_data(sample_network().snapshot())
    target = graph.pos[TV]
    for zoom in (0.2, 1.0, 5.0):
        graph.zoom, graph.offset = zoom, QPointF(0, 0)
        on_screen = QPointF(400 + target[0] * zoom, 300 + target[1] * zoom)
        assert graph.node_at(on_screen + QPointF(10, 0)) == TV      # 10 px à côté : touché
        assert graph.node_at(on_screen + QPointF(40, 0)) != TV      # 40 px à côté : raté


def test_map_points_follow_the_repeated_world(app):
    from spaghet.map_view import MapView

    m = MapView()
    m.resize(800, 600)
    m.points = [("paris", 48.85, 2.35, "#f00", 6, "")]
    near = m._point_screen(48.85, 2.35)
    m.center += QPointF(256 * 2 ** m.zoom, 0)  # un tour complet vers l'est
    assert m._point_screen(48.85, 2.35) == near  # le point est toujours au même endroit à l'écran
    assert m.point_at(near)[0] == "paris"


def test_sizes_and_plurals_read_well():
    from spaghet.gui import fmt_bps, fmt_bytes, plural

    assert fmt_bytes(1_048_575) == "1.0 Mo"   # et pas « 1024.0 Ko »
    assert fmt_bytes(1023.97) == "1.0 Ko"     # et pas « 1024 o »
    assert fmt_bytes(512) == "512 o"
    assert fmt_bps(999_999) == "1.0 Mbit/s"   # et pas « 1000.0 kbit/s »
    assert (plural(0, "saut"), plural(1, "saut"), plural(9, "saut")) == ("0 saut", "1 saut", "9 sauts")


def test_view_refits_when_layout_settles_until_user_takes_over(app):
    from PySide6.QtCore import QRectF

    from spaghet.graph_view import GraphView

    def all_on_screen(graph):
        screen = QRectF(0, 0, graph.width(), graph.height())
        return all(screen.contains(QPointF(graph.width() / 2 + graph.offset.x() + x * graph.zoom,
                                           graph.height() / 2 + graph.offset.y() + y * graph.zoom))
                   for x, y in graph.pos.values())

    def settle(graph):
        while graph.temperature:
            graph._frame()

    net = Network()
    net.upsert_node(PC, kind="pc")
    graph = GraphView()
    graph.resize(800, 600)
    graph.update_data(net.snapshot())  # au lancement : un seul nœud, cadrage très serré
    for i in range(12):                # puis le scan trouve le reste
        net.upsert_node(f"192.168.1.{100 + i}", rtt_ms=50.0)
    net.upsert_node(GW, kind="router")
    graph.update_data(net.snapshot())
    settle(graph)
    assert all_on_screen(graph)

    graph._auto_fit = False            # l'utilisateur a zoomé : on ne touche plus à sa vue
    zoom = graph.zoom
    net.upsert_node("192.168.1.200")
    graph.update_data(net.snapshot())
    settle(graph)
    assert graph.zoom == zoom


def test_only_the_busiest_links_are_animated(app, monkeypatch):
    """Au-delà de MAX_ANIMATED liens animés, le dessin dépassait le budget d'une image (57 ms pour 250 liens)."""
    import time

    from PySide6.QtGui import QImage, QPainter

    from spaghet import graph_view

    net = Network()
    net.upsert_node(GW, kind="router")
    net.upsert_node(PC, kind="pc")
    for i in range(60):
        net.upsert_node(f"site{i}.com", kind="internet", name=f"site{i}.com")
        net.add_traffic(PC, f"site{i}.com", "web", 1000, now=0.0)
    net.tick(now=0.0)
    for i, link in enumerate(net.links.values()):
        link.bytes_total += 10_000 * (i + 1)  # débits tous différents
    net.tick(now=1.0)
    graph = graph_view.GraphView()
    graph.update_data(net.snapshot())
    animated = []
    real = graph_view.particle_positions
    monkeypatch.setattr(graph_view, "particle_positions",
                        lambda length, bps, *a: animated.append(bps) or real(length, bps, *a))
    image = QImage(400, 300, QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    graph.draw(painter, time.time())
    painter.end()
    assert len(animated) == graph_view.MAX_ANIMATED
    assert min(animated) > max(l.bps for l in net.links.values() if l.bps not in animated)  # les plus actifs
