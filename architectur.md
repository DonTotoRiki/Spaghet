# Spaghet — Architecture technique

Basé sur `Recherche.md`. Stack : Python ≥ 3.12, scapy, FastAPI, Cytoscape.js, Leaflet.

**État** : les 10 étapes sont codées et couvertes par 26 tests (`pytest`). Le frontend a été exécuté sans affichage (Cytoscape headless dans Node). **Rien n'a encore tourné sur un vrai réseau** : la capture, l'ARP et le traceroute demandent des droits réseau (voir « Lancement »).

## Lancement

```
cd ~/Bureau/Spaghet
./setcap.sh                    # une fois : droits réseau pour le Python du venv
.venv/bin/python -m spaghet    # puis http://localhost:8000
```

Options : `--iface` (défaut : interface de la route par défaut), `--geoip` (défaut : `$SPAGHET_GEOIP` ou `geoip.mmdb`).

Au démarrage : scan ARP → identification → première mesure de latence → tâches de fond → serveur web.

| Tâche de fond | Fréquence |
|---|---|
| Mesure de latence (ARP) | 10 s |
| Scan ARP + identification des nouveaux + retrait des appareils muets depuis 5 min | 60 s |
| Calcul du débit des liens (`Network.tick`) | 1 s |
| Capture du trafic (`AsyncSniffer`) | continue |
| Envoi des changements au navigateur | 1 s |

## Arborescence

```
Spaghet/
├── pyproject.toml
├── setcap.sh              # droits réseau sur le Python du venv (refuse un Python partagé)
├── spaghet/
│   ├── __main__.py        # point d'entrée, options, tâches de fond (every)
│   ├── model.py           # Node, Link, Network : état en mémoire, diffs, débit, retrait
│   ├── discovery.py       # sous-réseau, scan ARP, fabricant, DNS inverse, mDNS, NetBIOS
│   ├── classify.py        # type d'appareil, catégorie de trafic, domaine regroupé, domaines streaming
│   ├── capture.py         # sniff → trafic sur les liens, noms via DNS et SNI TLS
│   ├── probes.py          # latence par ARP, traceroute
│   ├── geo.py             # géolocalisation .mmdb (optionnelle)
│   └── server.py          # FastAPI : REST + WebSocket + fichiers statiques
├── web/
│   ├── index.html
│   ├── app.js             # graphe, temps réel, particules, panneau, filtres, carte, exports
│   └── style.css
└── tests/
    ├── test_model.py      # nœuds, liens, diffs, retrait
    ├── test_discovery.py  # détection du sous-réseau
    ├── test_classify.py   # type d'appareil
    ├── test_probes.py     # lissage de la latence
    ├── test_capture.py    # ports, domaines, SNI, pcap rejoué, débit
    ├── test_geo.py        # lecture d'un enregistrement .mmdb
    └── test_server.py     # page, WebSocket, API traceroute
```

Un seul processus, pas de base de données : l'état vit dans `Network`.

## Modèle de données (`model.py`)

```python
@dataclass
class Node:
    ip: str              # identifiant : l'IP sur le LAN, le domaine pour une destination Internet
    first_seen: float
    last_seen: float     # dernière preuve de présence (réponse ARP, trafic)
    updated: float       # dernière modification, sert aux diffs
    mac, name, vendor: str | None
    kind: str            # "router", "phone", "pc", "printer", "iot", "internet", "unknown"
    rtt_ms: float | None
    hops: int | None
    bytes_total: int
    services: list[str]  # services mDNS
    addr: str | None     # destination Internet : première IP vue (traceroute, géoloc)
    lat, lon: float | None
    city, country: str | None

@dataclass
class Link:
    a: str               # clé triée : a < b
    b: str
    last_seen: float
    updated: float
    protocols: dict[str, int]   # catégorie → octets
    bytes_total: int
    bytes_ab: int        # part envoyée de a vers b (sens des particules)
    bps: float           # débit sur 5 s, deux sens confondus
    bps_ab: float        # part de bps allant de a vers b

class Network:
    upsert_node(ip, seen=True, **champs)  # seen=False : info qui ne prouve pas la présence
    add_traffic(src, dst, proto, octets)
    tick()                # débit, appelé chaque seconde
    prune(max_age)        # retire les nœuds muets et leurs liens
    snapshot() / diff_since(t)
```

Tous les modules écrivent dans `Network`, le serveur le lit. Un `threading.Lock` protège l'état.

## Protocole GUI ↔ serveur

| Canal | Message | Contenu |
|---|---|---|
| `GET /api/snapshot` | état complet | `{now, nodes, links}` |
| `WS /ws` à la connexion | état complet | `{type: "snapshot", now, nodes, links}` |
| `WS /ws` toutes les 1 s, si changement | diff | `{type: "diff", now, nodes, links, removed}` |
| `POST /api/traceroute/{id}` | traceroute | `{hops}` ; 404 nœud inconnu, 409 déjà en cours, 422 pas d'IP |

Format Cytoscape (`{data: {id, ...}}`) : le frontend n'a rien à convertir. `now` sert au navigateur à caler son horloge sur celle du serveur.

## Étapes réalisées

| # | Étape | Vérifié par |
|---|---|---|
| 0 | Squelette | page servie, test |
| 1 | Modèle | tests |
| 2 | Découverte ARP + graphe en étoile | test du sous-réseau ; **scan réel non testé** |
| 3 | Identification (fabricant, nom, type, icônes) | tests + DNS inverse/mDNS réels sans root ; NetBIOS non testé sur une vraie machine |
| 4 | Latence | test du lissage ; **mesure réelle non testée** |
| 5 | Temps réel | tests WebSocket et diffs ; page exécutée sans affichage |
| 6 | Capture du trafic | pcap rejoué par scapy ; **capture réelle non testée** |
| 7 | Destinations Internet | pcap rejoué (DNS, SNI, QUIC, non résolu) ; page exécutée sans affichage |
| 8 | Particules, panneau, filtres | page exécutée sans affichage ; **rendu visuel non vérifié** |
| 9 | Traceroute, géoloc, carte | API testée avec un traceroute simulé ; **carte non vue à l'écran** |
| 10 | Exports, setcap, `--iface` | syntaxe ; **`setcap.sh` non exécuté** |

## Écarts avec le plan initial

| Plan | Réalisé | Pourquoi |
|---|---|---|
| Latence par ping ICMP | Requête ARP unicast | Tout appareil du LAN répond à l'ARP ; beaucoup de téléphones et de PC Windows bloquent le ping |
| Scan unique au démarrage | Rescan toutes les 60 s + retrait à 5 min | Nécessaire pour voir arriver et partir les appareils |
| `diff_since` et `bps` dès l'étape 1 | Ajoutés aux étapes 5 et 6 | Inutiles avant |
| `fixtures/sample.pcap` | pcap généré dans le test | Lisible, pas de fichier binaire |
| Trafic Internet sur le lien PC ↔ passerelle (étape 6) | Nœud par domaine (étape 7) | Prévu par l'étape 7 |
| Vidéo reconnue par le port | Liste de domaines de streaming (`STREAMING_DOMAINS`) | La vidéo passe par le port 443 comme le web |
| Particules (plugin ou canvas) | Tirets ronds animés (`line-dash-offset`) | Fonction native de Cytoscape, rien à ajouter |
| Historique de débit dans `Link` | Échantillonné dans le navigateur | Plus simple, n'alourdit pas les diffs ; vide à l'ouverture de la page |
| Actions par WebSocket (`rescan`, `traceroute`) | Pas de `rescan` ; traceroute en `POST` | Le rescan est automatique ; un POST est plus simple |
| `hops` dans une info-bulle | Dans le panneau de détails | Le panneau existait déjà |
| `geoip2` + GeoLite2 | `maxminddb` + DB-IP City Lite ou GeoLite2 | Même format, moins de code ; DB-IP ne demande pas de compte |
| Internet replié par défaut (`Recherche.md`) | Déplié par défaut | Les noms sont visibles tout de suite |
| `setcap` sur l'interpréteur du venv | venv recréé avec `--copies`, puis `setcap.sh` | Le venv pointait vers `/usr/bin/python3.14` : `setcap` aurait donné les droits à tout Python du système |

## Raccourcis connus

Marqués `shortcut:` dans le code :

- **Type d'appareil** (`classify.py`) : mots-clés de fabricant, se trompent sur les marques multi-produits (Samsung, HP, TP-Link).
- **Catégorie de trafic** (`classify.py`) : par port ; seuls les domaines de `STREAMING_DOMAINS` sortent du « web ».
- **Domaine regroupé** (`classify.py`) : approximation de la Public Suffix List ; `tldextract` si les regroupements sont faux.
- **IP d'une destination** (`capture.py`) : première IP vue seulement ; un CDN est souvent mal placé sur la carte.

## Limites à connaître

- **Vue partielle** : sur un réseau avec switch, seul le trafic de cette machine est visible. Pour tout voir : lancer sur la passerelle ou utiliser un port miroir.
- **DNS sécurisé (DoH)** : les réponses DNS deviennent invisibles ; QUIC (vidéo YouTube) chiffre aussi le SNI → destinations « (non résolu) ».
- **ClientHello en deux segments** (Chrome) : SNI perdu, pas de réassemblage TCP.
- **Performance de la capture** : scapy plafonne vers quelques milliers de paquets/s ; au-delà, le débit est sous-estimé. Piste : capture en Rust ou `pyshark`.
- **Taille du graphe** : Cytoscape ralentit vers 300 nœuds ; le regroupement par domaine et le retrait à 5 min limitent la casse.
- **Animation** : particules recalculées à chaque image, peut ramer au-delà d'environ 100 liens actifs.
- **Layout** : relancé seulement quand un nœud arrive ou part ; les longueurs ne suivent pas chaque nouvelle mesure de latence.
- **Carte** : les tuiles viennent d'OpenStreetMap, qui voit donc ton IP.
- **Table IP → nom** : grandit sans limite pendant la session.

## Sécurité

- Le serveur écoute sur `127.0.0.1` uniquement.
- Le traceroute ne vise qu'un nœud déjà présent dans le graphe, un seul à la fois.
- Les noms venant du réseau (mDNS, DNS, SNI) sont échappés avant tout `innerHTML` et dans les infobulles Leaflet.
- Scan ARP refusé au-delà de 1024 adresses.
- `setcap.sh` refuse un Python partagé et restreint l'interpréteur privilégié à ton compte (`chmod 700`).
