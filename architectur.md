# Spaghet — Plan de construction technique

Basé sur `Recherche.md`. Stack : Python 3.12, scapy, FastAPI, Cytoscape.js.

## Arborescence cible

```
Spaghet/
├── pyproject.toml
├── spaghet/
│   ├── __main__.py        # point d'entrée : python -m spaghet --iface eth0
│   ├── model.py           # Node, Link, Network (état en mémoire)
│   ├── discovery.py       # scan ARP, OUI, DNS inverse, mDNS
│   ├── capture.py         # sniff pcap → flux agrégés
│   ├── probes.py          # ping, traceroute
│   ├── classify.py        # port/SNI/DNS → protocole, type d'appareil
│   ├── geo.py             # GeoIP (optionnel)
│   └── server.py          # FastAPI : REST + WebSocket + fichiers statiques
├── web/
│   ├── index.html
│   ├── app.js             # Cytoscape, WebSocket, panneau de détails
│   └── style.css
└── tests/
    ├── test_model.py
    ├── test_classify.py
    └── fixtures/sample.pcap
```

Un seul processus. Pas de base de données au départ : l'état vit dans `Network`.

## Modèle de données (`model.py`)

```python
@dataclass
class Node:
    ip: str
    mac: str | None
    name: str | None
    vendor: str | None
    kind: str            # "router", "phone", "pc", "printer", "iot", "internet", "unknown"
    rtt_ms: float | None
    hops: int | None
    first_seen: float
    last_seen: float
    bytes_total: int

@dataclass
class Link:
    a: str               # ip
    b: str               # ip
    protocols: dict[str, int]   # protocole → octets
    bps: float           # débit sur la fenêtre glissante
    bytes_total: int
    last_seen: float

class Network:
    nodes: dict[str, Node]
    links: dict[tuple[str, str], Link]   # clé triée (a < b)
    def upsert_node(...) / add_traffic(...) / snapshot() -> dict / diff_since(t) -> dict
```

Tous les modules écrivent dans `Network`. Le serveur ne fait que le lire. Un `threading.Lock` suffit (scapy tourne dans des threads).

## Protocole GUI ↔ serveur

| Canal | Message | Contenu |
|---|---|---|
| `GET /api/snapshot` | état complet | `{nodes: [...], links: [...]}` |
| `WS /ws` serveur → client | toutes les 1 s | `{type: "diff", nodes: [...], links: [...], removed: [...]}` |
| `WS /ws` client → serveur | action | `{type: "rescan"}`, `{type: "traceroute", ip}` |

Format des nœuds et liens = celui de Cytoscape (`{data: {id, ...}}`), pour que le frontend n'ait rien à convertir.

## Étapes de construction (dans l'ordre)

Chaque étape produit quelque chose qui tourne et se vérifie avant de passer à la suivante.

### Étape 0 — Squelette
- `pyproject.toml` (dépendances : scapy, fastapi, uvicorn, zeroconf, manuf).
- `python -m spaghet` démarre FastAPI et sert `web/index.html`.
- **Fini quand** : la page s'ouvre sur `http://localhost:8000`.

### Étape 1 — Modèle
- `model.py` avec `Node`, `Link`, `Network`, `snapshot()`.
- Tests unitaires : ajout de nœud, fusion de trafic sur un lien, clé de lien symétrique.
- **Fini quand** : `pytest` passe.

### Étape 2 — Découverte ARP → premier graphe
- `discovery.py` : `arping` du sous-réseau de l'interface, remplissage de `Network`.
- Passerelle = route par défaut (`conf.route`), marquée `kind="router"`.
- `/api/snapshot` + `app.js` : graphe Cytoscape en étoile autour de la passerelle.
- **Fini quand** : les appareils réels du LAN apparaissent dans le navigateur.

> C'est le premier livrable utile. Tout ce qui suit l'enrichit.

### Étape 3 — Identification des appareils
- Fabricant via l'OUI (`manuf`).
- Nom : DNS inverse, puis mDNS (`zeroconf`), puis NetBIOS.
- `classify.py` : fabricant + services mDNS → `kind` (règles simples dans un dict).
- Icônes par `kind` dans le frontend.
- **Fini quand** : la plupart des appareils ont un nom ou un fabricant et la bonne icône.

### Étape 4 — Latence
- `probes.py` : ping ICMP de chaque nœud toutes les 10 s, RTT lissé (moyenne glissante).
- Frontend : layout `cose` avec `idealEdgeLength` proportionnel au RTT.
- **Fini quand** : un appareil en Wi-Fi lointain est visiblement plus loin qu'un appareil filaire.

### Étape 5 — Temps réel
- WebSocket `/ws` : envoi de `diff_since()` toutes les secondes.
- Frontend : applique les diffs sans recalculer tout le layout.
- Nœuds absents depuis plus de 5 min : grisés, puis retirés.
- **Fini quand** : brancher un appareil le fait apparaître sans recharger la page.

### Étape 6 — Capture du trafic
- `capture.py` : `AsyncSniffer` scapy (filtre BPF `ip`), callback → `Network.add_traffic(src, dst, proto, size)`.
- Débit calculé sur une fenêtre de 5 s.
- `classify.py` : port → protocole (dict). Tests sur `fixtures/sample.pcap`.
- Frontend : épaisseur = débit, couleur = protocole, légende.
- **Fini quand** : lancer une vidéo épaissit le lien PC ↔ passerelle en couleur « streaming ».

### Étape 7 — Destinations Internet
- IP hors du sous-réseau → nœud `kind="internet"`.
- Nom via les réponses DNS capturées (table IP → nom) et le SNI des TLS ClientHello.
- Regroupement : un nœud par domaine (`*.googlevideo.com` → `googlevideo.com`), le tout dans un nœud parent « Internet » repliable (compound node Cytoscape).
- **Fini quand** : on voit « youtube.com » et pas une IP brute.

### Étape 8 — Animation et détails
- Particules le long des liens actifs (plugin ou animation canvas), vitesse/densité ∝ débit.
- Panneau latéral au clic : infos du nœud, connexions triées par volume, mini-graphe de débit (historique de 60 points gardé dans `Link`).
- Filtres : protocole, LAN seul / avec Internet.

### Étape 9 — Sauts et géographie (optionnel)
- Traceroute à la demande (clic droit → « tracer »), `hops` dans l'info-bulle.
- `geo.py` : GeoLite2, vue carte Leaflet dans un second onglet.

### Étape 10 — Finitions
- Export PNG (`cy.png()`) et JSON (snapshot).
- Lancement avec capacités plutôt que root : `setcap cap_net_raw,cap_net_admin+eip` sur l'interpréteur du venv.
- Choix de l'interface en argument, avec une valeur par défaut = interface de la route par défaut.

## Ordre et dépendances

```
0 Squelette ─▶ 1 Modèle ─▶ 2 ARP + graphe ─┬─▶ 3 Identification
                                           ├─▶ 4 Latence
                                           └─▶ 5 Temps réel ─▶ 6 Capture ─▶ 7 Internet ─▶ 8 Animation
                                                                                        └─▶ 9 Géo
                                                                                 tout ─▶ 10 Finitions
```

Les étapes 3 et 4 sont indépendantes et peuvent se faire dans n'importe quel ordre. La 6 dépend de la 5 parce que le trafic n'a de sens qu'en temps réel.

## Points techniques à surveiller

- **Performance de la capture** : scapy plafonne vers quelques milliers de paquets/s. Si ça décroche, ne garder que les en-têtes (`store=False`, filtre BPF serré), puis passer la capture en Rust ou en `pyshark` seulement si nécessaire.
- **Taille du graphe** : au-delà d'environ 300 nœuds, Cytoscape ralentit ; d'où le regroupement Internet par domaine dès l'étape 7.
- **Stabilité du layout** : ne pas relancer le layout complet à chaque diff, seulement positionner les nouveaux nœuds.
- **Sécurité** : le serveur écoute sur `127.0.0.1` uniquement ; pas d'API qui lance des scans sur des IP arbitraires hors du sous-réseau.
