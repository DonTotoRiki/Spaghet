import math
import random
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QRadialGradient
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
CLOUD_PAD = (100, 40)  # marge du cadre Internet autour des destinations (largeur, hauteur), dans le graphe
CLOUD_GAP = 40         # distance minimale entre un appareil du LAN et le cadre Internet
STALE_AFTER = 60      # s sans réponse avant de griser un appareil (retiré à 5 min par le moteur)
ACTIVE_BPS = 1000     # en dessous, pas de particules
MAX_ANIMATED = 40     # particules seulement sur les liens les plus actifs : au-delà, le dessin dépasse 33 ms par image
BACKGROUND = QColor("#101216")
BACKGROUND_CENTER = QColor("#191c22")  # dégradé radial : le centre du graphe ressort légèrement
LABEL_BG = QColor(12, 13, 17, 200)     # pastille sous les étiquettes : lisibles même posées sur un lien
LABEL_TEXT, LABEL_DIM = QColor("#e8eaed"), QColor("#8b919a")
LABEL_POINTS = 9      # taille des étiquettes à l'écran, quel que soit le zoom
RELAYOUT_LENGTH = 15  # écart de longueur idéale (unités du graphe) qui relance la disposition ; en dessous, invisible


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
        # Cadrage automatique à chaque stabilisation, jusqu'à ce que l'utilisateur zoome ou déplace la vue :
        # au lancement, les premiers nœuds arrivent avant le scan et le reste sortirait de l'écran.
        self._auto_fit = True
        self._drag: tuple[str, str | None, QPointF] | None = None  # (mode "node"/"pan", id, point de départ)
        self._moved = False
        self._cloud_label = QRectF()
        self._animating = False  # au moins un lien a des particules : il faut redessiner à chaque image
        self._lengths: dict[str, float] = {}  # longueur idéale de chaque nœud à la dernière relance de la disposition
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
        # Longueur = latence : une latence qui a nettement changé depuis la dernière disposition la relance aussi.
        # Comparé à la dernière relance, pas à la seconde précédente : une dérive lente finit par compter.
        lengths = {i: ideal_length(n.get("rtt_ms")) for i, n in nodes.items()}
        changed = changed or any(abs(length - self._lengths.get(i, length)) > RELAYOUT_LENGTH
                                 for i, length in lengths.items())
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
            self._lengths = lengths
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
            disp[internet] += (anchor - p[internet]) * 0.15
        disp -= p * 0.005  # légère gravité vers le centre
        length = np.sqrt((disp ** 2).sum(-1)) + 1e-9
        p += disp * (np.minimum(length, self.temperature) / length)[:, None]
        if internet.any() and (~internet).any():
            # Contrainte stricte : aucun appareil du LAN dans le cadre Internet (le rectangle dessiné, marge comprise).
            # Sinon ta machine, reliée à toutes les destinations, se place en leur milieu. Une simple force ne suffit
            # pas : la disposition se fige avant de l'avoir sortie.
            pad = np.array(CLOUD_PAD) + CLOUD_GAP
            low, high = p[internet].min(0) - pad, p[internet].max(0) + pad
            lan = p[~internet]
            inside = np.all((lan > low) & (lan < high), axis=1)
            if inside.any():
                depth = np.concatenate([lan - low, high - lan], axis=1)  # vers la gauche, le haut, la droite, le bas
                side = depth.argmin(1)                                    # sortie par le bord le plus proche
                rows = np.flatnonzero(inside)
                axis = side[rows] % 2
                lan[rows, axis] = np.where(side[rows] < 2, low[axis] - 1, high[axis] + 1)
                p[~internet] = lan
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
            if not self.temperature and self._auto_fit:  # vient de se stabiliser
                self.fit()
        if (moving or self._animating) and self.isVisible():
            self.update()

    def relayout(self) -> None:
        self.temperature = max(self.temperature, 20.0)

    # --- dessin --------------------------------------------------------------------------------

    def _bounds(self, ids: list[str]) -> QRectF:
        if not ids:
            return QRectF(-100, -100, 200, 200)
        xs, ys = zip(*(self.pos[i] for i in ids))
        # Marge : au moins celle du cadre Internet (CLOUD_PAD), sinon le cadrage le coupe au bord de l'écran.
        mx = CLOUD_PAD[0] + 20
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(-mx, -80, mx, 70)

    def cloud_box(self, internet: list[str]) -> QRectF:
        """Cadre « Internet » dessiné autour des destinations, avec la place de leurs étiquettes."""
        xs, ys = zip(*(self.pos[i] for i in internet))
        px, py = CLOUD_PAD
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(-px, -py, px, py)

    def fit(self) -> None:
        """Cadre tout le graphe, et reprend le cadrage automatique (bouton « Recadrer »)."""
        self._auto_fit = True
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
            box = self.cloud_box(internet)
            p.setPen(QPen(QColor("#2c3139"), 1.5))
            p.setBrush(QColor(255, 255, 255, 8))
            p.drawRoundedRect(box, 16, 16)
            # Titre au-dessus du cadre : à l'intérieur, il recouvrait les destinations du haut.
            self._cloud_label = QRectF(box.left() + 6, box.top() - line_height - 4, 30 * line_height, line_height)
            p.setPen(LABEL_DIM)
            p.drawText(self._cloud_label, Qt.AlignLeft | Qt.AlignVCenter, f"INTERNET · {len(internet)}   — clic pour replier")

        busy = [k for k, e in enumerate(edges) if e[3] != "star" and e[2].get("bps", 0) > ACTIVE_BPS]
        animated = set(sorted(busy, key=lambda k: edges[k][2]["bps"], reverse=True)[:MAX_ANIMATED])
        for k, (a, b, link, kind) in enumerate(edges):
            pa, pb = QPointF(*self.pos[a]), QPointF(*self.pos[b])
            if kind == "star":
                p.setPen(QPen(QColor("#3a404a"), 1, Qt.DashLine))
                p.drawLine(pa, pb)
                continue
            proto = main_protocol(link)
            if proto in self.hidden_protocols:
                continue
            color = QColor(PROTOCOLS[proto][1])
            bps = link.get("bps", 0)
            active = k in animated
            line = QColor(color)
            line.setAlphaF(0.35 if active else 0.8)
            # Bouts plats : les bouts ronds doublent le temps de tracé des traits épais lissés.
            p.setPen(QPen(line, edge_width(bps), Qt.DotLine if kind == "agg" else Qt.SolidLine, Qt.FlatCap))
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

        metrics = QFontMetricsF(self.font, p.device())
        pad = metrics.height() * 0.4
        for node_id in ids:
            x, y = self.pos[node_id]
            if node_id == CLOUD:
                color, radius = "#888888", 18
                label = f"Internet ({sum(1 for n in self.nodes.values() if n.get('kind') == 'internet')})"
                detail = ""
                stale = False
            else:
                node = self.nodes[node_id]
                color, _ = kind_of(node)
                radius = 15 if node.get("kind") == "router" else 10
                label = name_of(node)
                if len(label) > 28:
                    label = label[:27] + "…"
                detail = f"  {node['rtt_ms']:.1f} ms" if node.get("rtt_ms") is not None else ""
                stale = now - node.get("last_seen", now) > STALE_AFTER
            p.setOpacity(0.3 if stale else 1.0)
            center = QPointF(x, y)
            fill = QColor(color)
            halo = QColor(fill)
            halo.setAlpha(110 if node_id == self.selected else 45)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(center, radius + (9 if node_id == self.selected else 5), radius + (9 if node_id == self.selected else 5))
            # Liseré couleur du fond : le nœud se détache des liens qui y arrivent.
            p.setPen(QPen(QColor("#ffffff"), 2.5) if node_id == self.selected else QPen(BACKGROUND, 2))
            p.setBrush(fill)
            p.drawEllipse(center, radius, radius)

            # Étiquette : nom en clair, latence en gris, sur une pastille sombre.
            name_w, detail_w = metrics.horizontalAdvance(label), metrics.horizontalAdvance(detail)
            box = QRectF(x - (name_w + detail_w) / 2 - pad, y + radius + 4, name_w + detail_w + 2 * pad, metrics.height() + 2)
            p.setPen(Qt.NoPen)
            p.setBrush(LABEL_BG)
            p.drawRoundedRect(box, box.height() / 2, box.height() / 2)
            baseline = box.top() + 1 + metrics.ascent()
            p.setPen(LABEL_TEXT)
            p.drawText(QPointF(box.left() + pad, baseline), label)
            if detail:
                p.setPen(LABEL_DIM)
                p.drawText(QPointF(box.left() + pad + name_w, baseline), detail)
            p.setOpacity(1.0)
        self._animating = animating

    def _draw_legend(self, p: QPainter) -> None:
        """Légende des types d'appareils, en bas à gauche de la vue (coordonnées de l'écran)."""
        font = QFont(self.font)
        font.setPointSizeF(LABEL_POINTS)
        p.setFont(font)
        metrics = QFontMetricsF(font, p.device())
        row = metrics.height() + 4
        width = 26 + max(metrics.horizontalAdvance(label) for _, label in KINDS.values()) + 12
        box = QRectF(12, self.height() - 12 - row * len(KINDS) - 12, width, row * len(KINDS) + 12)
        p.setPen(QPen(QColor("#2c3139"), 1))
        p.setBrush(QColor(16, 18, 22, 215))
        p.drawRoundedRect(box, 8, 8)
        for k, (color, label) in enumerate(KINDS.values()):
            y = box.top() + 6 + k * row + row / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(box.left() + 14, y), 5, 5)
            p.setPen(LABEL_DIM)
            p.drawText(QPointF(box.left() + 26, y + metrics.ascent() / 2 - 1), label)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        gradient = QRadialGradient(QPointF(self.width() / 2, self.height() / 2), max(self.width(), self.height()) * 0.7)
        gradient.setColorAt(0, BACKGROUND_CENTER)
        gradient.setColorAt(1, BACKGROUND)
        p.fillRect(self.rect(), gradient)
        p.save()
        p.translate(self.width() / 2 + self.offset.x(), self.height() / 2 + self.offset.y())
        p.scale(self.zoom, self.zoom)
        self.draw(p, time.time(), self.zoom)
        p.restore()
        self._draw_legend(p)
        p.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._auto_fit:  # fenêtre agrandie ou panneau ouvert : le graphe garde toute la place disponible
            self.fit()

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
            self._auto_fit = False  # l'utilisateur a choisi sa vue
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
        dy = event.angleDelta().y()
        if not dy:
            return  # défilement horizontal : pas un zoom
        factor = 1.15 ** (dy / 120)  # proportionnel : un pavé tactile envoie beaucoup de petits crans
        new_zoom = max(0.1, min(5.0, self.zoom * factor))
        g = self._to_graph(event.position())  # le point sous la souris reste fixe
        self.offset += g * (self.zoom - new_zoom)
        self._auto_fit = False
        self.zoom = new_zoom
        self.update()
