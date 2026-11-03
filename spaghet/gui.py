import json
import sys
import threading
from collections import deque
from datetime import datetime

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFormLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSplitter,
                               QToolBar, QVBoxLayout, QWidget)

from spaghet import probes
from spaghet.graph_view import CLOUD, KINDS, PROTOCOLS, GraphView, kind_of, main_protocol, name_of
from spaghet.model import Network

HISTORY = 60  # points (1/s) du mini-graphe de débit
ACCENT = "#4a9eff"
MUTED = "#8b919a"

# Thème sombre de toute la fenêtre. Les couleurs reprennent celles du graphe (fond, liserés, gris des étiquettes).
STYLE = f"""
QToolBar {{ background: #16181d; border: none; border-bottom: 1px solid #262a31; padding: 5px 8px; spacing: 2px; }}
QToolBar QToolButton, QToolBar QCheckBox {{ color: #c9ccd1; padding: 4px 9px; border-radius: 6px; }}
QToolBar QToolButton:hover, QToolBar QCheckBox:hover {{ background: #242830; }}
QToolBar::separator {{ background: #2c3139; width: 1px; margin: 4px 10px; }}
QCheckBox#chip::indicator {{ width: 0; height: 0; }}
QCheckBox#chip:!checked {{ color: #5c626b; }}
QLabel#muted, QLabel#section {{ color: {MUTED}; }}
QLabel#section {{ font-size: 8pt; font-weight: 600; letter-spacing: 1px; padding-top: 12px; }}
QLabel#title {{ font-size: 15pt; font-weight: 600; }}
QWidget#panel {{ background: #15171c; }}
QPushButton {{ background: #23272f; border: 1px solid #323843; border-radius: 6px; padding: 6px 12px; color: #e8eaed; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5c626b; }}
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ padding: 5px 4px; border-radius: 6px; }}
QListWidget::item:hover {{ background: #1e2127; }}
QListWidget::item:selected {{ background: #23324a; color: #e8eaed; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #2f343d; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSplitter::handle {{ background: #262a31; width: 1px; }}
QStatusBar {{ background: #16181d; border-top: 1px solid #262a31; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 2px 6px; }}
QToolTip {{ background: #23272f; color: #e8eaed; border: 1px solid #323843; padding: 4px; }}
"""


def _scaled(n: float, base: int, units: list[str]) -> str:
    i = 0
    # On compare la valeur arrondie telle qu'affichée : sinon 1 048 575 octets donnent « 1024.0 Ko ».
    while i < len(units) - 1 and round(n, 1 if i else 0) >= base:
        n /= base
        i += 1
    return f"{n:.1f} {units[i]}" if i else f"{n:.0f} {units[0]}"


def fmt_bytes(n: float) -> str:
    return _scaled(n, 1024, ["o", "Ko", "Mo", "Go", "To"])


def fmt_bps(n: float) -> str:
    return _scaled(n, 1000, ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"])


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"  # en français, 0 et 1 restent au singulier


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
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(1, 1, 12, 12, 3, 3)
        p.end()
    return QIcon(pixmap)


def app_icon() -> QIcon:
    """Icône de la fenêtre : trois liens courbes entre deux nœuds, aux couleurs du graphe."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#191c22"))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    for bend, color in [(-18, "#ff5c8a"), (0, ACCENT), (18, "#7ed67e")]:
        path = QPainterPath(QPointF(16, 46))
        path.cubicTo(QPointF(24 + bend, 10), QPointF(40 - bend, 54), QPointF(48, 18))
        p.setPen(QPen(QColor(color), 4, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffb347"))
    p.drawEllipse(QPointF(16, 46), 6, 6)
    p.drawEllipse(QPointF(48, 18), 6, 6)
    p.end()
    return QIcon(pixmap)


def strike(box: QCheckBox, on: bool) -> None:
    """Pastille désactivée : texte barré, pour un état lisible sans la couleur (la feuille de style ne sait pas le faire)."""
    font = box.font()
    font.setStrikeOut(not on)
    box.setFont(font)


def section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("section")
    return label


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
        self.setFixedHeight(72)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#101216"))
        p.drawRoundedRect(self.rect(), 6, 6)
        top = max(self.values, default=0) or 1
        w, h = self.width(), self.height() - 16
        points = [QPointF(i / (HISTORY - 1) * w, h - v / top * (h - 6) + 2) for i, v in enumerate(self.values)]
        if len(points) > 1:
            fill = QLinearGradient(0, 0, 0, h)
            fill.setColorAt(0, QColor(74, 158, 255, 110))
            fill.setColorAt(1, QColor(74, 158, 255, 0))
            p.setBrush(fill)
            p.drawPolygon(QPolygonF([QPointF(points[0].x(), h + 2), *points, QPointF(points[-1].x(), h + 2)]))
            p.setPen(QPen(QColor(ACCENT), 1.6))
            p.drawPolyline(QPolygonF(points))
        p.setPen(QColor(MUTED))
        p.drawText(8, self.height() - 4, f"max {fmt_bps(max(self.values, default=0))} · 60 s")
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
        self.setObjectName("panel")
        self.setAttribute(Qt.WA_StyledBackground)  # sinon la feuille de style ne peint pas le fond d'un QWidget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 12, 10)
        self.title = plain_label()
        self.title.setObjectName("title")
        self.kind = QLabel()
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(5)
        self.spark = Sparkline()
        self.trace_button = QPushButton("Tracer la route")
        self.trace_button.clicked.connect(lambda: self.traceRequested.emit(self.node_id))
        self.trace_result = plain_label()
        self.connections_title = section("")
        self.connections = QListWidget()
        self.connections.itemClicked.connect(lambda item: self.nodeClicked.emit(item.data(Qt.UserRole)))
        layout.addWidget(self.title)
        layout.addWidget(self.kind)
        layout.addWidget(section("Infos"))
        layout.addLayout(self.form)
        layout.addWidget(section("Débit"))
        layout.addWidget(self.spark)
        layout.addWidget(self.trace_button)
        layout.addWidget(self.trace_result)
        layout.addWidget(self.connections_title)
        layout.addWidget(self.connections, 1)

    def show_node(self, node: dict, connections: list[tuple[dict, dict]], history: list[float], trace: str | None) -> None:
        self.node_id = node["ip"]
        color, kind_label = kind_of(node)
        set_text(self.title, name_of(node))
        set_text(self.kind, f'<span style="color:{color}">●</span>&nbsp; {kind_label}')
        rows = [
            ("Domaine" if node.get("kind") == "internet" else "IP", node["ip"]),
            ("MAC", node.get("mac")), ("Fabricant", node.get("vendor")),
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
                name = QLabel(title)
                name.setObjectName("muted")
                self.form.addRow(name, label)
        for (_, value), label in zip(rows, self._row_values):
            set_text(label, value)
        self.spark.values = history
        self.spark.update()
        self.trace_button.setEnabled(trace != "…")
        set_text(self.trace_result, "Traceroute en cours…" if trace == "…" else trace or "")
        set_text(self.connections_title, f"CONNEXIONS · {len(connections)}")

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
        self.setWindowTitle("Spaghet — carte du réseau local")
        self.setWindowIcon(app_icon())

        self.graph = GraphView()
        self.panel = DetailPanel()
        self.panel.hide()
        splitter = QSplitter()
        splitter.addWidget(self.graph)
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([960, 320])
        self.setCentralWidget(splitter)

        bar = QToolBar("Actions")
        bar.setMovable(False)
        internet = QCheckBox("Internet")
        internet.setObjectName("chip")
        internet.setIcon(swatch(KINDS["internet"][0], round_=True))
        internet.setToolTip("Afficher ou masquer les destinations Internet")
        internet.setAccessibleName("Destinations Internet")
        internet.setChecked(True)
        internet.toggled.connect(self._toggle_internet)
        bar.addWidget(internet)
        bar.addAction("Recadrer", self.graph.fit)
        bar.addAction("Exporter PNG", self.export_png)
        bar.addAction("Exporter JSON", self.export_json)
        bar.addSeparator()
        for key, (label, color) in PROTOCOLS.items():
            box = QCheckBox(label)
            box.setObjectName("chip")  # case cachée : la pastille de couleur s'éteint quand le protocole est masqué
            box.setToolTip(f"Afficher ou masquer les liens « {label} »")
            box.setAccessibleName(f"Liens {label}")
            box.setIcon(swatch(color))
            box.setChecked(True)
            box.toggled.connect(lambda on, k=key, b=box, c=color: self._toggle_protocol(k, on, b, c))
            bar.addWidget(box)
        self.addToolBar(bar)

        self.status_label = plain_label()
        self.status_label.setWordWrap(False)
        # Ignored + étirement : un long message ne doit pas élargir la fenêtre ; il reste lisible en infobulle.
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.statusBar().addWidget(self.status_label, 1)

        self.graph.nodeClicked.connect(self.select)
        self.graph.traceRequested.connect(self.trace)
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
        kinds = [n.get("kind") for n in self.snapshot["nodes"]]
        internet, private = kinds.count("internet"), kinds.count("private")
        counts = f"{plural(len(kinds) - internet - private, 'appareil')} · {plural(internet, 'destination')} Internet"
        if private:
            counts += f" · {plural(private, 'adresse')} {'privées' if private > 1 else 'privée'}"
        text = f"{counts} — {self.status_message}" if self.status_message else counts
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def _toggle_internet(self, on: bool) -> None:
        strike(self.sender(), on)
        self.graph.show_internet = on
        self.graph.relayout()

    def _toggle_protocol(self, key: str, on: bool, box: QCheckBox, color: str) -> None:
        box.setIcon(swatch(color if on else "#3a404a"))
        strike(box, on)
        (self.graph.hidden_protocols.discard if on else self.graph.hidden_protocols.add)(key)
        self.graph.update()

    def refresh(self) -> None:
        self.snapshot = self.network.snapshot()
        self.graph.update_data(self.snapshot)
        nodes = {n["ip"]: n for n in self.snapshot["nodes"]}

        # Débit par nœud, échantillonné chaque seconde pour le mini-graphe.
        rates = dict.fromkeys(nodes, 0.0)
        for link in self.snapshot["links"]:
            for end in (link["a"], link["b"]):
                rates[end] = rates.get(end, 0.0) + link["bps"]
        for node_id, bps in rates.items():
            self.history.setdefault(node_id, deque(maxlen=HISTORY)).append(bps)
        for gone in self.history.keys() - nodes.keys():
            del self.history[gone]

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
                result = "La cible n'a pas répondu" if hops is None else plural(hops, "saut")
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
        path = self._ask_path("Exporter le graphe", "png", "Images PNG")
        if path and not self.graph.export_png(path):
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
    for role, color in [(QPalette.Window, "#16181d"), (QPalette.WindowText, "#e8eaed"), (QPalette.Base, "#121418"),
                        (QPalette.AlternateBase, "#1c1f25"), (QPalette.Text, "#e8eaed"), (QPalette.Button, "#23272f"),
                        (QPalette.ButtonText, "#e8eaed"), (QPalette.Highlight, ACCENT),
                        (QPalette.HighlightedText, "#101216"), (QPalette.ToolTipBase, "#23272f"),
                        (QPalette.ToolTipText, "#e8eaed")]:
        palette.setColor(role, QColor(color))
    return palette


def run(network: Network, backend) -> int:
    """Ouvre la fenêtre, puis lance backend(report) dans un thread : report(message) met à jour la barre d'état."""
    app = QApplication(sys.argv)
    app.setApplicationName("Spaghet")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    window = MainWindow(network)
    window.resize(1280, 800)
    window.show()
    threading.Thread(target=backend, args=(window.bridge.status.emit,), name="backend", daemon=True).start()
    return app.exec()
