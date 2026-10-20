import json
import math
import sys
import threading
from collections import deque
from datetime import datetime

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSplitter,
                               QTabWidget, QToolBar, QVBoxLayout, QWidget)

from spaghet import probes
from spaghet.graph_view import CLOUD, KINDS, PROTOCOLS, GraphView, kind_of, main_protocol, name_of
from spaghet.map_view import MapView
from spaghet.model import Network

HISTORY = 60  # points (1/s) du mini-graphe de débit


def fmt_bytes(n: float) -> str:
    units = ["o", "Ko", "Mo", "Go", "To"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}" if i else f"{n:.0f} o"


def fmt_bps(n: float) -> str:
    units = ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"]
    i = 0
    while n >= 1000 and i < len(units) - 1:
        n /= 1000
        i += 1
    return f"{n:.1f} {units[i]}" if i else f"{n:.0f} bit/s"


def fmt_time(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def swatch(color: str, round_: bool = False) -> QIcon:
    pixmap = QPixmap(14, 14)
    if round_:  # rond comme les nœuds du graphe
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(1, 1, 12, 12)
        p.end()
    else:
        pixmap.fill(QColor(color))
    return QIcon(pixmap)


def plain_label(text: str = "") -> QLabel:
    # Texte brut : les noms viennent du réseau et QLabel interpréterait du HTML.
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def set_text(label: QLabel, text: str) -> None:
    # Ne toucher au texte que s'il change : setText efface la sélection en cours.
    if label.text() != text:
        label.setText(text)


class Sparkline(QWidget):
    def __init__(self):
        super().__init__()
        self.values: list[float] = []
        self.setFixedHeight(64)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#111111"))
        top = max(self.values, default=0) or 1
        w, h = self.width(), self.height() - 14
        points = [QPointF(i / (HISTORY - 1) * w, h - v / top * (h - 4) - 2) for i, v in enumerate(self.values)]
        p.setPen(QPen(QColor("#4a9eff"), 1.5))
        p.drawPolyline(QPolygonF(points))
        p.setPen(QColor("#999999"))
        p.drawText(4, self.height() - 2, f"max {fmt_bps(max(self.values, default=0))} · 60 s")
        p.end()


class DetailPanel(QWidget):
    """Infos de l'appareil sélectionné, débit récent, traceroute et connexions."""

    nodeClicked = Signal(str)
    traceRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.node_id: str | None = None
        # Ce qui est affiché, pour ne reconstruire le formulaire et la liste que si leur structure change :
        # sinon le défilement, la sélection et un clic en cours seraient perdus à chaque seconde.
        self._row_titles: list[str] = []
        self._row_values: list[QLabel] = []
        self._connection_keys: list[tuple[str, str]] = []  # (nœud, protocole) de chaque ligne de la liste
        layout = QVBoxLayout(self)
        self.title = plain_label()
        self.title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.form = QFormLayout()
        self.spark = Sparkline()
        self.trace_button = QPushButton("Tracer la route")
        self.trace_button.clicked.connect(lambda: self.traceRequested.emit(self.node_id))
        self.trace_result = plain_label()
        self.connections_title = QLabel()
        self.connections = QListWidget()
        self.connections.itemClicked.connect(lambda item: self.nodeClicked.emit(item.data(Qt.UserRole)))
        layout.addWidget(self.title)
        layout.addLayout(self.form)
        layout.addWidget(QLabel("Débit"))
        layout.addWidget(self.spark)
        layout.addWidget(self.trace_button)
        layout.addWidget(self.trace_result)
        layout.addWidget(self.connections_title)
        layout.addWidget(self.connections, 1)

    def show_node(self, node: dict, connections: list[tuple[dict, dict]], history: list[float], trace: str | None) -> None:
        self.node_id = node["ip"]
        _color, kind_label = kind_of(node)
        set_text(self.title, name_of(node))
        rows = [
            ("Domaine" if node.get("kind") == "internet" else "IP", node["ip"]),
            ("MAC", node.get("mac")), ("Fabricant", node.get("vendor")), ("Type", kind_label),
            ("Latence", node.get("rtt_ms") is not None and f"{node['rtt_ms']:.1f} ms"),
            ("Sauts", node.get("hops") is not None and str(node["hops"])),
            ("Adresse", node.get("kind") == "internet" and node.get("addr")),
            ("Lieu", ", ".join(filter(None, [node.get("city"), node.get("country")]))),
            ("Services", ", ".join(node.get("services") or [])),
            ("Volume total", fmt_bytes(node.get("bytes_total", 0))),
            ("Vu depuis", fmt_time(node["first_seen"])), ("Dernière activité", fmt_time(node["last_seen"])),
        ]
        rows = [(title, value) for title, value in rows if value]
        if [title for title, _ in rows] != self._row_titles:
            while self.form.rowCount():
                self.form.removeRow(0)
            self._row_titles = [title for title, _ in rows]
            self._row_values = [plain_label() for _ in rows]
            for (title, _), label in zip(rows, self._row_values):
                self.form.addRow(QLabel(title), label)
        for (_, value), label in zip(rows, self._row_values):
            set_text(label, value)
        self.spark.values = history
        self.spark.update()
        self.trace_button.setEnabled(trace != "…")
        set_text(self.trace_result, "Traceroute en cours…" if trace == "…" else trace or "")
        set_text(self.connections_title, f"Connexions ({len(connections)})")

        keys = [(other["ip"], main_protocol(link)) for other, link in connections]
        texts = [f"{name_of(other)}\n{fmt_bytes(link['bytes_total'])} · {fmt_bps(link['bps'])}" for other, link in connections]
        if keys != self._connection_keys:
            self.connections.clear()
            for (other_id, proto), text in zip(keys, texts):
                item = QListWidgetItem(swatch(PROTOCOLS[proto][1]), text)
                item.setData(Qt.UserRole, other_id)
                item.setToolTip(PROTOCOLS[proto][0])
                self.connections.addItem(item)
            self._connection_keys = keys
        else:  # mêmes lignes : on ne change que les chiffres, la liste garde son défilement et sa sélection
            for row, text in enumerate(texts):
                if self.connections.item(row).text() != text:
                    self.connections.item(row).setText(text)


class Bridge(QObject):
    """Passe les messages des threads de fond au thread de l'interface."""
    status = Signal(str)
    traced = Signal(str, str)


class MainWindow(QMainWindow):
    def __init__(self, network: Network):
        super().__init__()
        self.network = network
        self.snapshot = {"now": 0, "nodes": [], "links": []}
        self.selected: str | None = None
        self.history: dict[str, deque] = {}
        self.traces: dict[str, str] = {}   # nœud → "…" (en cours) ou résultat
        self.status_message = "Démarrage…"
        self.bridge = Bridge()
        self.bridge.status.connect(self.set_status)
        self.bridge.traced.connect(self._on_traced)
        self.setWindowTitle("Spaghet")

        self.graph = GraphView()
        self.map = MapView()
        self.panel = DetailPanel()
        self.panel.hide()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.graph, "Graphe")
        self.tabs.addTab(self.map, "Carte")
        splitter = QSplitter()
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([960, 320])
        self.setCentralWidget(splitter)

        bar = QToolBar("Actions")
        bar.setMovable(False)
        internet = QCheckBox("Internet")
        internet.setChecked(True)
        internet.toggled.connect(self._toggle_internet)
        bar.addWidget(internet)
        bar.addAction("Recadrer", self.graph.fit)
        bar.addAction("Exporter PNG", self.export_png)
        bar.addAction("Exporter JSON", self.export_json)
        self.addToolBar(bar)

        self.addToolBarBreak()
        legend = QToolBar("Protocoles")
        legend.setMovable(False)
        legend.addWidget(QLabel("Protocoles : "))
        for key, (label, color) in PROTOCOLS.items():
            box = QCheckBox(label)
            box.setIcon(swatch(color))
            box.setChecked(True)
            box.toggled.connect(lambda on, k=key: self._toggle_protocol(k, on))
            legend.addWidget(box)
        self.addToolBar(legend)

        self.addToolBarBreak()
        legend = QToolBar("Appareils")
        legend.setMovable(False)
        legend.addWidget(QLabel("Appareils : "))
        for color, label in KINDS.values():
            kind = QLabel(label)
            row = QWidget()
            box = QHBoxLayout(row)
            box.setContentsMargins(4, 0, 4, 0)
            dot = QLabel()
            dot.setPixmap(swatch(color, round_=True).pixmap(12, 12))
            box.addWidget(dot)
            box.addWidget(kind)
            legend.addWidget(row)
        self.addToolBar(legend)

        self.status_label = plain_label()
        self.status_label.setWordWrap(False)
        # Ignored + étirement : un long message ne doit pas élargir la fenêtre ; il reste lisible en infobulle.
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.statusBar().addWidget(self.status_label, 1)

        self.graph.nodeClicked.connect(self.select)
        self.graph.traceRequested.connect(self.trace)
        self.map.pointClicked.connect(self.select)
        self.panel.nodeClicked.connect(self.select)
        self.panel.traceRequested.connect(self.trace)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    def set_status(self, message: str) -> None:
        print(message or "Prêt", flush=True)
        self.status_message = message
        self._update_status()

    def _update_status(self) -> None:
        lan = sum(1 for n in self.snapshot["nodes"] if n.get("kind") != "internet")
        internet = len(self.snapshot["nodes"]) - lan
        counts = f"{lan} appareils · {internet} destinations Internet"
        text = f"{counts} — {self.status_message}" if self.status_message else counts
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def _toggle_internet(self, on: bool) -> None:
        self.graph.show_internet = on
        self.graph.relayout()

    def _toggle_protocol(self, key: str, on: bool) -> None:
        (self.graph.hidden_protocols.discard if on else self.graph.hidden_protocols.add)(key)
        self.graph.update()

    def refresh(self) -> None:
        self.snapshot = self.network.snapshot()
        self.graph.update_data(self.snapshot)
        nodes = {n["ip"]: n for n in self.snapshot["nodes"]}

        # Débit par nœud, échantillonné chaque seconde pour le mini-graphe.
        rates = dict.fromkeys(nodes, 0.0)
        biggest: dict[str, dict] = {}
        for link in self.snapshot["links"]:
            for end in (link["a"], link["b"]):
                rates[end] = rates.get(end, 0.0) + link["bps"]
                if end not in biggest or link["bytes_total"] > biggest[end]["bytes_total"]:
                    biggest[end] = link
        for node_id, bps in rates.items():
            self.history.setdefault(node_id, deque(maxlen=HISTORY)).append(bps)
        for gone in self.history.keys() - nodes.keys():
            del self.history[gone]

        self.map.set_points([
            (n["ip"], n["lat"], n["lon"],
             PROTOCOLS[main_protocol(biggest[n["ip"]])][1] if n["ip"] in biggest else "#888888",
             4 + math.log10(1 + n.get("bytes_total", 0)),
             f"{name_of(n)}\n{', '.join(filter(None, [n.get('city'), n.get('country')]))} · {fmt_bytes(n.get('bytes_total', 0))}")
            for n in nodes.values() if n.get("kind") == "internet" and n.get("lat") is not None
        ])
        self._update_status()
        self._update_panel()

    def _update_panel(self) -> None:
        nodes = {n["ip"]: n for n in self.snapshot["nodes"]}
        node = nodes.get(self.selected)
        if node is None:
            self.selected = self.graph.selected = None
            self.panel.hide()
            return
        connections = []
        for link in self.snapshot["links"]:
            if self.selected in (link["a"], link["b"]):
                other = link["b"] if link["a"] == self.selected else link["a"]
                if other in nodes:
                    connections.append((nodes[other], link))
        connections.sort(key=lambda c: c[1]["bytes_total"], reverse=True)
        self.panel.show_node(node, connections, list(self.history.get(self.selected, [])), self.traces.get(self.selected))
        self.panel.show()

    def select(self, node_id: str | None) -> None:
        if node_id == CLOUD:
            return
        self.selected = self.graph.selected = node_id
        self._update_panel()
        self.graph.update()

    def trace(self, node_id: str) -> None:
        if self.traces.get(node_id) == "…":
            return
        self.traces[node_id] = "…"
        self.select(node_id)

        def run():
            try:
                hops = probes.traceroute(self.network, node_id)
                result = "La cible n'a pas répondu" if hops is None else f"{hops} sauts"
            except (RuntimeError, ValueError, KeyError, OSError) as e:
                result = f"Traceroute impossible : {e}"
            self.bridge.traced.emit(node_id, result)

        threading.Thread(target=run, name="traceroute", daemon=True).start()

    def _on_traced(self, node_id: str, result: str) -> None:
        self.traces[node_id] = result
        self._update_panel()

    def _ask_path(self, title: str, extension: str, kind: str) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        path, _ = QFileDialog.getSaveFileName(self, title, f"spaghet-{stamp}.{extension}", f"{kind} (*.{extension})")
        return path

    def export_png(self) -> None:
        on_map = self.tabs.currentWidget() is self.map
        path = self._ask_path("Exporter la carte" if on_map else "Exporter le graphe", "png", "Images PNG")
        if path and not (self.map.grab().save(path) if on_map else self.graph.export_png(path)):
            QMessageBox.warning(self, "Export PNG", f"Impossible d'écrire {path}")

    def export_json(self) -> None:
        path = self._ask_path("Exporter l'état du réseau", "json", "JSON")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.network.snapshot(), f, indent=2, ensure_ascii=False)
        except OSError as e:
            QMessageBox.warning(self, "Export JSON", f"Impossible d'écrire {path} : {e}")


def dark_palette() -> QPalette:
    palette = QPalette()
    for role, color in [(QPalette.Window, "#1b1b1b"), (QPalette.WindowText, "#eeeeee"), (QPalette.Base, "#141414"),
                        (QPalette.AlternateBase, "#222222"), (QPalette.Text, "#eeeeee"), (QPalette.Button, "#2a2a2a"),
                        (QPalette.ButtonText, "#eeeeee"), (QPalette.Highlight, "#4a9eff"),
                        (QPalette.HighlightedText, "#111111"), (QPalette.ToolTipBase, "#2a2a2a"),
                        (QPalette.ToolTipText, "#eeeeee")]:
        palette.setColor(role, QColor(color))
    return palette


def run(network: Network, backend) -> int:
    """Ouvre la fenêtre, puis lance backend(report) dans un thread : report(message) met à jour la barre d'état."""
    app = QApplication(sys.argv)
    app.setApplicationName("Spaghet")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    window = MainWindow(network)
    window.resize(1280, 800)
    window.show()
    threading.Thread(target=backend, args=(window.bridge.status.emit,), name="backend", daemon=True).start()
    return app.exec()
