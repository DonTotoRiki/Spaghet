import html
import math

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QToolTip, QWidget

TILE = 256
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
# La politique d'OpenStreetMap exige un User-Agent qui identifie l'application.
USER_AGENT = b"Spaghet/0.1 (cartographie locale de reseau)"
MAX_TILES = 400  # tuiles gardées en mémoire


def project(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Latitude/longitude → pixels de la carte Web Mercator au niveau de zoom donné."""
    size = TILE * 2 ** zoom
    lat = max(-85.0511, min(85.0511, lat))
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360 * size, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * size


class MapView(QWidget):
    """Carte des destinations Internet sur fond OpenStreetMap. Les tuiles ne sont téléchargées qu'à l'affichage."""

    pointClicked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.zoom = 2
        self.center = QPointF(*project(30, 0, self.zoom))
        self.points: list[tuple[str, float, float, str, float, str]] = []  # id, lat, lon, couleur, rayon, infobulle
        self._tiles: dict[tuple[int, int, int], QPixmap] = {}
        self._pending: set[tuple[int, int, int]] = set()
        self._net: QNetworkAccessManager | None = None
        self._drag: QPointF | None = None
        self._moved = False

    def set_points(self, points) -> None:
        self.points = points
        if self.isVisible():
            self.update()

    def _tile(self, z: int, x: int, y: int) -> QPixmap | None:
        key = (z, x, y)
        if key in self._tiles:
            return self._tiles[key]
        if key not in self._pending:
            if self._net is None:
                self._net = QNetworkAccessManager(self)
            request = QNetworkRequest(QUrl(TILE_URL.format(z=z, x=x, y=y)))
            request.setRawHeader(b"User-Agent", USER_AGENT)
            request.setTransferTimeout(10_000)  # une requête bloquée échoue : la tuile sera redemandée
            reply = self._net.get(request)
            reply.finished.connect(lambda: self._on_tile(key, reply))
            self._pending.add(key)
        return None

    def _on_tile(self, key, reply: QNetworkReply) -> None:
        self._pending.discard(key)  # en cas d'erreur, la tuile sera redemandée au prochain dessin
        if reply.error() == QNetworkReply.NoError:
            pixmap = QPixmap()
            if pixmap.loadFromData(reply.readAll()):
                if len(self._tiles) >= MAX_TILES:
                    self._tiles.clear()
                self._tiles[key] = pixmap
                self.update()
        reply.deleteLater()

    def _screen(self, x: float, y: float) -> QPointF:
        return QPointF(x, y) - self.center + QPointF(self.width() / 2, self.height() / 2)

    def _point_screen(self, lat: float, lon: float) -> QPointF:
        """Position à l'écran d'un point, dans la copie du monde la plus proche du centre (la carte se répète)."""
        x, y = project(lat, lon, self.zoom)
        size = TILE * 2 ** self.zoom
        return self._screen(x + round((self.center.x() - x) / size) * size, y)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1a1d21"))
        p.setRenderHint(QPainter.Antialiasing)
        n = 2 ** self.zoom
        top_left = self.center - QPointF(self.width() / 2, self.height() / 2)
        for ty in range(max(0, int(top_left.y() // TILE)), min(n, int((top_left.y() + self.height()) // TILE) + 1)):
            for tx in range(int(top_left.x() // TILE), int((top_left.x() + self.width()) // TILE) + 1):
                pixmap = self._tile(self.zoom, tx % n, ty)  # tx % n : la carte se répète en longitude
                if pixmap:
                    p.drawPixmap(self._screen(tx * TILE, ty * TILE), pixmap)

        for _id, lat, lon, color, radius, _tip in self.points:
            c = QColor(color)
            p.setPen(QPen(c, 1))
            c.setAlphaF(0.6)
            p.setBrush(c)
            p.drawEllipse(self._point_screen(lat, lon), radius, radius)

        text = "© contributeurs OpenStreetMap"
        box = QRectF(self.width() - 200, self.height() - 18, 200, 18)
        p.fillRect(box, QColor(255, 255, 255, 180))
        p.setPen(QColor("#333333"))
        p.drawText(box, Qt.AlignCenter, text)
        p.end()

    def point_at(self, pos: QPointF):
        """Point le plus proche de la souris, parmi ceux qu'elle touche (rayon du point + 2 px, au moins 10 px)."""
        best, best_d = None, math.inf
        for point in self.points:
            s = self._point_screen(point[1], point[2])
            d = math.hypot(s.x() - pos.x(), s.y() - pos.y())
            if d <= max(10.0, point[4] + 2) and d < best_d:
                best, best_d = point, d
        return best

    def mousePressEvent(self, event) -> None:
        self._drag, self._moved = event.position(), False

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            delta = event.position() - self._drag
            if abs(delta.x()) + abs(delta.y()) > 2:
                self._moved = True
            if self._moved:
                self.center -= delta
                self._drag = event.position()
                self.update()
            return
        point = self.point_at(event.position())
        # QToolTip interprète le HTML : les noms viennent du réseau, on les échappe.
        QToolTip.showText(event.globalPosition().toPoint(), html.escape(point[5]).replace("\n", "<br>") if point else "", self)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None and not self._moved:
            point = self.point_at(event.position())
            if point:
                self.pointClicked.emit(point[0])
        self._drag = None

    def wheelEvent(self, event) -> None:
        new_zoom = max(1, min(18, self.zoom + (1 if event.angleDelta().y() > 0 else -1)))
        if new_zoom == self.zoom:
            return
        # Le point sous la souris reste fixe.
        mouse = event.position() - QPointF(self.width() / 2, self.height() / 2)
        factor = 2 ** (new_zoom - self.zoom)
        self.center = (self.center + mouse) * factor - mouse
        self.zoom = new_zoom
        self.update()
