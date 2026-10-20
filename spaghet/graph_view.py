import math
import random
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

# type → (couleur, libellé). Pas d'emoji : Qt ne les affiche pas partout de façon fiable.
KINDS = {
    "router":   ("#ffb347", "Routeur"),
    "pc":       ("#4a9eff", "Ordinateur"),
    "phone":    ("#7ed67e", "Téléphone"),
    "printer":  ("#c58cff", "Imprimante"),
    "iot":      ("#ffd84a", "Objet connecté"),
    "internet": ("#888888", "Internet"),
    "unknown":  ("#666666", "Inconnu"),
}
# Mêmes catégories que spaghet/classify.py : catégorie → (libellé, couleur)
PROTOCOLS = {
    "web":       ("Web", "#4a9eff"),
    "dns":       ("DNS", "#7ed67e"),
    "streaming": ("Streaming", "#ff5c8a"),
    "p2p":       ("P2P", "#ff8c42"),
    "files":     ("Fichiers", "#c58cff"),
    "remote":    ("Accès distant", "#ffd84a"),
    "mail":      ("Mail", "#4ad8d8"),
    "infra":     ("Réseau", "#999999"),
    "other":     ("Autre", "#666666"),
}
CLOUD = "@internet"   # pseudo-nœud « Internet » quand le groupe est replié
CLOUD_OFFSET = 450     # distance entre la passerelle et le groupe Internet, dans le graphe
STALE_AFTER = 60      # s sans réponse avant de griser un appareil (retiré à 5 min par le moteur)
ACTIVE_BPS = 1000     # en dessous, pas de particules
BACKGROUND = QColor("#111111")
LABEL_POINTS = 9      # taille des étiquettes à l'écran, quel que soit le zoom


def kind_of(node: dict) -> tuple[str, str]:
    return KINDS.get(node.get("kind"), KINDS["unknown"])


def name_of(node: dict) -> str:
    return node.get("name") or node.get("vendor") or node["ip"]


def main_protocol(link: dict) -> str:
    """Catégorie qui a fait passer le plus d'octets sur le lien."""
    protocols = link.get("protocols") or {}
    key = max(protocols, key=protocols.get, default="other")
    return key if key in PROTOCOLS else "other"


def edge_width(bps: float) -> float:
    # En log : 1 kbit/s et 100 Mbit/s restent sur le même écran.
    return 1 + max(0.0, math.log10(1 + bps) - 2) * 1.5


def ideal_length(rtt_ms: float | None) -> float:
    # En log : 0,5 ms (filaire) et 100 ms (téléphone en veille) restent lisibles.
    return 150 if rtt_ms is None else 60 + 50 * math.log2(1 + rtt_ms)


def particle_positions(length: float, bps: float, t: float, toward_b: bool) -> list[float]:
    """Distances (depuis a) des points qui défilent sur un lien : plus serrés et plus rapides si le débit monte."""
    if bps <= ACTIVE_BPS or length <= 0:
        return []
    gap = max(8.0, 40 - 4 * math.log10(bps))
    speed = 10 + 15 * math.log10(bps / ACTIVE_BPS)  # unités du graphe par seconde
    phase = (t * speed) % gap
    positions = [phase + i * gap for i in range(int(length / gap) + 1) if phase + i * gap <= length]
    return positions if toward_b else [length - p for p in positions]


class GraphView(QWidget):
    """Graphe du réseau dessiné à la main : disposition à forces (numpy), particules, panoramique et zoom."""

    nodeClicked = Signal(object)   # id du nœud, ou None pour désélectionner
    traceRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.nodes: dict[str, dict] = {}
        self.links: dict[tuple[str, str], dict] = {}
        self.pos: dict[str, list[float]] = {}
        self.selected: str | None = None
        self.collapsed = False
        self.show_internet = True
        self.hidden_protocols: set[str] = set()
        self.offset = QPointF(0, 0)
        self.zoom = 1.0
        self.temperature = 0.0     # déplacement max par pas de layout ; 0 = graphe figé
        self._fitted = False
        self._drag: tuple[str, str | None, QPointF] | None = None  # (mode "node"/"pan", id, point de départ)
        self._moved = False
        self._cloud_label = QRectF()
        self._animating = False  # au moins un lien a des particules : il faut redessiner à chaque image
        self.font = QFont()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._frame)
        self._timer.start(33)  # ~30 images/s

    # --- données -----------------------------------------------------------------------------

    def router_id(self) -> str | None:
        return next((i for i, n in self.nodes.items() if n.get("kind") == "router"), None)

    def update_data(self, snapshot: dict) -> None:
        nodes = {n["ip"]: n for n in snapshot["nodes"]}
        changed = nodes.keys() != self.nodes.keys() or {(l["a"], l["b"]) for l in snapshot["links"]} != self.links.keys()
        self.nodes = nodes
        self.links = {(l["a"], l["b"]): l for l in snapshot["links"]}
        for gone in self.pos.keys() - nodes.keys() - {CLOUD}:
            del self.pos[gone]
        router = self.pos.get(self.router_id(), [0.0, 0.0])
        internet = [self.pos[i] for i, n in nodes.items() if n.get("kind") == "internet" and i in self.pos]
        cloud = np.mean(internet, axis=0).tolist() if internet else [router[0] + 300, router[1]]
        for node_id, node in nodes.items():
            if node_id not in self.pos:
                # Nouveau nœud posé près de son groupe : le layout l'écarte sans bouleverser le reste.
                x, y = cloud if node.get("kind") == "internet" else router
                self.pos[node_id] = [x + random.uniform(-30, 30), y + random.uniform(-30, 30)]
        if self.selected not in nodes:
            self.selected = None
        self.update()  # débits, latences, étiquettes : une fois par seconde suffit
        if changed:
            self.temperature = max(self.temperature, 20.0)
            if not self._fitted and nodes:
                for _ in range(300):  # premier affichage : on pose le graphe d'un coup puis on cadre
                    self._layout_step()
                self.fit()
                self._fitted = True

    def _visible(self) -> tuple[list[str], list[tuple[str, str, dict, str]]]:
        """Nœuds et liens à disposer et dessiner. Type de lien : "traffic", "star" (pointillé vers la passerelle) ou "agg"."""
        internet = {i for i, n in self.nodes.items() if n.get("kind") == "internet"}
        hide_internet = self.collapsed or not self.show_internet
        ids = [i for i in self.nodes if not (hide_internet and i in internet)]
        if self.collapsed and self.show_internet and internet:
            ids.append(CLOUD)
            if CLOUD not in self.pos:
                self.pos[CLOUD] = np.mean([self.pos[i] for i in internet], axis=0).tolist()
        shown = set(ids)
        edges = []
        totals: dict[str, dict] = {}
        for (a, b), link in self.links.items():
            if a in shown and b in shown:
                edges.append((a, b, link, "traffic"))
            elif self.collapsed and self.show_internet and (a in internet) != (b in internet):
                lan, toward_cloud = (b, link["bps"] - link["bps_ab"]) if a in internet else (a, link["bps_ab"])
                t = totals.setdefault(lan, {"bps": 0.0, "bps_ab": 0.0, "bytes_total": 0, "protocols": {}})
                t["bps"] += link["bps"]
                t["bps_ab"] += toward_cloud  # sens appareil → Internet
                t["bytes_total"] += link["bytes_total"]
                for p, n in link["protocols"].items():
                    t["protocols"][p] = t["protocols"].get(p, 0) + n
        edges += [(lan, CLOUD, t, "agg") for lan, t in totals.items() if lan in shown]
        router = self.router_id()
        if router:
            with_router = {a if b == router else b for a, b, _, kind in edges if router in (a, b) and kind == "traffic"}
            for i in ids:
                if i not in internet and i not in (router, CLOUD) and i not in with_router:
                    edges.append((router, i, {}, "star"))
        return ids, edges

    # --- disposition ---------------------------------------------------------------------------

    def _length(self, a: str, b: str) -> float:
        router = self.router_id()
        other = b if a == router else a
        return ideal_length(self.nodes.get(other, {}).get("rtt_ms"))

    def _layout_step(self) -> None:
        ids, edges = self._visible()
        if len(ids) < 2:
            return
        index = {i: k for k, i in enumerate(ids)}
        p = np.array([self.pos[i] for i in ids], dtype=float)
        delta = p[:, None, :] - p[None, :, :]
        dist2 = (delta ** 2).sum(-1) + 1.0
        disp = (delta * (1600.0 / dist2)[:, :, None]).sum(1)  # répulsion k²/d, k = 40
        if edges:
            src = np.array([index[a] for a, b, *_ in edges])
            dst = np.array([index[b] for a, b, *_ in edges])
            ideal = np.array([self._length(a, b) for a, b, *_ in edges])
            # Ressorts souples vers Internet : c'est l'ancrage du groupe qui décide de sa place, pas tes connexions.
            stiffness = np.array([0.05 if CLOUD in (a, b) or "internet" in (self.nodes.get(a, {}).get("kind"),
                                                                            self.nodes.get(b, {}).get("kind"))
                                  else 0.5 for a, b, *_ in edges])
            d = p[dst] - p[src]
            dist = np.sqrt((d ** 2).sum(-1)) + 0.01
            force = d * ((dist - ideal) / dist * stiffness)[:, None]  # ressort vers la longueur idéale
            np.add.at(disp, src, force)
            np.add.at(disp, dst, -force)
        internet = np.array([self.nodes.get(i, {}).get("kind") == "internet" for i in ids])
        if internet.sum() > 1:
            # Le groupe Internet se forme d'un seul côté, à droite de la passerelle : attiré par son propre centre,
            # il se refermerait autour de ta machine, qui est reliée à toutes les destinations.
            router = self.router_id()
            anchor = p[index[router]] + (CLOUD_OFFSET, 0) if router in index else p[internet].mean(0)
            disp[internet] += (anchor - p[internet]) * 0.05
            center = p[internet].mean(0)
            # Les appareils du LAN sont repoussés hors du groupe Internet : sinon ta machine, reliée à toutes
            # les destinations, se place en leur milieu et semble faire partie d'Internet.
            radius = np.sqrt(((p[internet] - center) ** 2).sum(-1)).max() + 80
            away = p[~internet] - center
            dist = np.sqrt((away ** 2).sum(-1)) + 0.01
            push = np.clip(radius - dist, 0, None) * 0.5
            disp[~internet] += away / dist[:, None] * push[:, None]
        disp -= p * 0.005  # légère gravité vers le centre
        length = np.sqrt((disp ** 2).sum(-1)) + 1e-9
        p += disp * (np.minimum(length, self.temperature) / length)[:, None]
        dragged = self._drag[1] if self._drag and self._drag[0] == "node" else None
        for i, k in index.items():
            if i != dragged:
                self.pos[i] = p[k].tolist()
        self.temperature *= 0.97
        if self.temperature < 0.3:
            self.temperature = 0.0

    def _frame(self) -> None:
        # Graphe figé et sans particules : rien à redessiner (les données, la souris et le zoom redessinent d'eux-mêmes).
        moving = bool(self.temperature)
        if moving:
            self._layout_step()
        if (moving or self._animating) and self.isVisible():
            self.update()

    def relayout(self) -> None:
        self.temperature = max(self.temperature, 20.0)

    # --- dessin --------------------------------------------------------------------------------

    def _bounds(self, ids: list[str]) -> QRectF:
        if not ids:
            return QRectF(-100, -100, 200, 200)
        xs, ys = zip(*(self.pos[i] for i in ids))
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(-80, -50, 80, 70)

    def cloud_box(self, internet: list[str], line_height: float = 2 * LABEL_POINTS) -> QRectF:
        """Cadre « Internet » dessiné autour des destinations, avec la place de leurs étiquettes."""
        xs, ys = zip(*(self.pos[i] for i in internet))
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(
            -6 * line_height, -2 * line_height, 6 * line_height, 2 * line_height)

    def fit(self) -> None:
        ids, _ = self._visible()
        r = self._bounds(ids)
        self.zoom = max(0.2, min(2.0, min(self.width() / r.width(), self.height() / r.height()) * 0.95))
        self.offset = -r.center() * self.zoom
        self.update()

    def draw(self, p: QPainter, now: float, zoom: float = 1.0) -> None:
        """Dessine le graphe en coordonnées du graphe (la transformation est faite par l'appelant).

        zoom : échelle appliquée par l'appelant, pour garder des étiquettes lisibles quand on dézoome.
        """
        p.setRenderHint(QPainter.Antialiasing)
        self.font.setPointSizeF(LABEL_POINTS / min(zoom, 1.0))
        p.setFont(self.font)
        line_height = 2 * LABEL_POINTS / min(zoom, 1.0)
        ids, edges = self._visible()

        internet = [i for i in ids if self.nodes.get(i, {}).get("kind") == "internet"]
        self._cloud_label = QRectF()
        animating = False
        if internet:
            box = self.cloud_box(internet, line_height)
            p.setPen(QPen(QColor("#555555"), 1))
            p.setBrush(QColor(255, 255, 255, 12))
            p.drawRoundedRect(box, 12, 12)
            self._cloud_label = QRectF(box.left() + 8, box.top() + 4, 30 * line_height, line_height)
            p.setPen(QColor("#bbbbbb"))
            p.drawText(self._cloud_label, Qt.AlignLeft | Qt.AlignVCenter, f"Internet ({len(internet)}) — clic pour replier")

        for a, b, link, kind in edges:
            pa, pb = QPointF(*self.pos[a]), QPointF(*self.pos[b])
            if kind == "star":
                p.setPen(QPen(QColor("#555555"), 1, Qt.DashLine))
                p.drawLine(pa, pb)
                continue
            proto = main_protocol(link)
            if proto in self.hidden_protocols:
                continue
            color = QColor(PROTOCOLS[proto][1])
            bps = link.get("bps", 0)
            active = bps > ACTIVE_BPS
            line = QColor(color)
            line.setAlphaF(0.35 if active else 0.8)
            p.setPen(QPen(line, edge_width(bps), Qt.DotLine if kind == "agg" else Qt.SolidLine, Qt.RoundCap))
            p.drawLine(pa, pb)
            if active:
                animating = True
                d = pb - pa
                length = math.hypot(d.x(), d.y())
                p.setPen(Qt.NoPen)
                p.setBrush(color.lighter(130))
                r = edge_width(bps) / 2 + 1
                for s in particle_positions(length, bps, now, link.get("bps_ab", 0) >= bps / 2):
                    p.drawEllipse(pa + d * (s / length), r, r)

        for node_id in ids:
            x, y = self.pos[node_id]
            if node_id == CLOUD:
                color, radius = "#888888", 18
                label = f"Internet ({sum(1 for n in self.nodes.values() if n.get('kind') == 'internet')})"
                stale = False
            else:
                node = self.nodes[node_id]
                color, _ = kind_of(node)
                radius = 14 if node.get("kind") == "router" else 9
                label = name_of(node)
                if len(label) > 28:
                    label = label[:27] + "…"
                if node.get("rtt_ms") is not None:
                    label += f" · {node['rtt_ms']:.1f} ms"
                stale = now - node.get("last_seen", now) > STALE_AFTER
            p.setOpacity(0.3 if stale else 1.0)
            fill = QColor(color)
            p.setPen(QPen(QColor("#ffffff"), 3) if node_id == self.selected else Qt.NoPen)
            p.setBrush(fill)
            p.drawEllipse(QPointF(x, y), radius, radius)
            p.setPen(QColor("#eeeeee"))
            p.drawText(QRectF(x - 20 * line_height, y + radius + 2, 40 * line_height, line_height),
                       Qt.AlignHCenter | Qt.AlignTop, label)
            p.setOpacity(1.0)
        self._animating = animating

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), BACKGROUND)
        p.translate(self.width() / 2 + self.offset.x(), self.height() / 2 + self.offset.y())
        p.scale(self.zoom, self.zoom)
        self.draw(p, time.time(), self.zoom)
        p.end()

    def export_png(self, path: str) -> bool:
        """Image du graphe entier (pas seulement la partie visible), en double résolution."""
        ids, _ = self._visible()
        r = self._bounds(ids).adjusted(-20, -20, 20, 20)
        scale = min(2.0, 8000 / max(r.width(), r.height()))
        image = QImage(int(r.width() * scale), int(r.height() * scale), QImage.Format_ARGB32)
        image.fill(BACKGROUND)
        p = QPainter(image)
        p.scale(scale, scale)
        p.translate(-r.topLeft())
        self.draw(p, time.time())
        p.end()
        return image.save(path)

    # --- souris --------------------------------------------------------------------------------

    def _to_graph(self, point: QPointF) -> QPointF:
        return (point - QPointF(self.width() / 2, self.height() / 2) - self.offset) / self.zoom

    def node_at(self, point: QPointF) -> str | None:
        g = self._to_graph(point)
        ids, _ = self._visible()
        best, best_d = None, 18.0 / self.zoom  # 18 pixels à l'écran, quel que soit le zoom
        for i in ids:
            d = math.hypot(self.pos[i][0] - g.x(), self.pos[i][1] - g.y())
            if d < best_d:
                best, best_d = i, d
        return best

    def mousePressEvent(self, event) -> None:
        node = self.node_at(event.position())
        if event.button() == Qt.RightButton:
            if node and node != CLOUD:
                self.traceRequested.emit(node)
            return
        self._drag = ("node", node, event.position()) if node else ("pan", None, event.position())
        self._moved = False

    def mouseMoveEvent(self, event) -> None:
        if not self._drag:
            return
        mode, node, last = self._drag
        delta = event.position() - last
        if abs(delta.x()) + abs(delta.y()) > 2:
            self._moved = True
        if not self._moved:
            return
        if mode == "node":
            g = self._to_graph(event.position())
            self.pos[node] = [g.x(), g.y()]
        else:
            self.offset += delta
        self._drag = (mode, node, event.position())
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        drag, self._drag = self._drag, None
        if not drag or self._moved:
            return
        node = drag[1]
        if node == CLOUD or (node is None and self._cloud_label.contains(self._to_graph(event.position()))):
            self.toggle_collapse()
        else:
            self.selected = node
            self.nodeClicked.emit(node)

    def toggle_collapse(self) -> None:
        self.collapsed = not self.collapsed
        self.pos.pop(CLOUD, None)
        self.relayout()

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = max(0.1, min(5.0, self.zoom * factor))
        g = self._to_graph(event.position())  # le point sous la souris reste fixe
        self.offset += g * (self.zoom - new_zoom)
        self.zoom = new_zoom
        self.update()
