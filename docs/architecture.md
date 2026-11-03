# Spaghet — Architecture technique

Basé sur `recherche.md`. Stack : Python ≥ 3.12, scapy, PySide6 (Qt 6), numpy.

**État** : les 10 étapes sont codées, 63 tests (`pytest`). L'interface web d'origine a été remplacée par une **fenêtre Qt native** (voir `recherche.md`, « Stack retenue »). Spaghet a tourné sur le réseau réel, avec une capture de la fenêtre faite sans écran : scan avec proxy ARP filtré, noms, latence, trafic vers Internet. Pas encore vérifiés en réel : le traceroute, les lieux GeoIP, et la fenêtre sur un vrai écran. L'onglet Carte (tuiles OpenStreetMap) a été retiré : il n'apportait rien.

## Lancement

```
cd ~/Bureau/Spaghet
./setcap.sh     # une fois : droits réseau pour .venv/bin/python-net, une copie de Python réservée à Spaghet
./spaghet.sh    # ouvre la fenêtre
```

Options : `--iface` (défaut : interface de la route par défaut), `--geoip` (défaut : `$SPAGHET_GEOIP`, sinon `geoip.mmdb` dans le dossier du projet, posé par `spaghet.sh`).

Au démarrage : la fenêtre s'ouvre tout de suite, puis un thread fait scan ARP → identification → première mesure de latence → tâches de fond. La barre d'état suit l'avancement et signale les problèmes (droits manquants, proxy ARP, pas de base GeoIP).

| Tâche de fond | Fréquence |
|---|---|
| Mesure de latence (ARP) | 10 s |
| Scan ARP + identification des nouveaux + retrait des appareils muets depuis 5 min | 60 s |
| Calcul du débit des liens (`Network.tick`) | 1 s |
| Capture du trafic (`AsyncSniffer`) | continue |
| Relecture de l'état par l'interface (`Network.snapshot`) | 1 s |
| Disposition du graphe et particules | ~30 images/s |

## Arborescence

```
Spaghet/
├── README.md
├── pyproject.toml
├── docs/
│   ├── architecture.md    # ce fichier
│   └── recherche.md       # étude préalable, choix de la stack
├── setcap.sh              # droits réseau sur .venv/bin/python-net (refuse un Python partagé)
├── spaghet.sh             # lanceur : python-net -m spaghet
├── spaghet/
│   ├── __main__.py        # main() : options ; Backend : tâches de fond (every), surveillance réseau et capture
│   ├── model.py           # Node, Link, Network : état en mémoire, diffs, débit, retrait
│   ├── discovery.py       # sous-réseau, scan ARP, fabricant, DNS inverse, mDNS, NetBIOS
│   ├── classify.py        # type d'appareil, catégorie de trafic, domaine regroupé, domaines streaming
│   ├── capture.py         # sniff → trafic sur les liens, noms via DNS et SNI TLS
│   ├── probes.py          # latence par ARP, traceroute
│   ├── geo.py             # géolocalisation .mmdb (optionnelle)
│   ├── gui.py             # fenêtre : barres d'outils, légendes, panneau de détails, exports, traceroute
│   └── graph_view.py      # graphe : disposition à forces (numpy), dessin, particules, zoom, clics
└── tests/
    ├── test_model.py      # nœuds, liens, copie, retrait
    ├── test_discovery.py  # sous-réseau, proxy ARP, noms DNS échappés
    ├── test_classify.py   # type d'appareil
    ├── test_probes.py     # lissage de la latence, traceroute limité aux nœuds connus
    ├── test_capture.py    # ports, domaines, SNI, pcap rejoué, débit
    ├── test_geo.py        # lecture d'un enregistrement .mmdb
    ├── test_gui.py        # particules, fenêtre sans écran, export PNG, zoom, latence
    └── test_main.py       # tâches de fond : retrait, premier scan raté, coupure de capture, changement de réseau
```

Un seul processus, pas de serveur ni de base de données : l'état vit dans `Network`.

## Modèle de données (`model.py`)

```python
@dataclass
class Node:
    ip: str              # identifiant : l'IP sur le LAN, le domaine pour une destination Internet
    first_seen: float
    last_seen: float     # dernière preuve de présence (réponse ARP, trafic)
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
    snapshot()            # copie en dicts, lue par l'interface et exportée en JSON
```

Les threads de fond écrivent dans `Network`, l'interface le lit. Un `threading.Lock` protège l'état.

## Interface ↔ moteur

Tout se passe dans le même processus :

| Sens | Moyen |
|---|---|
| Moteur → interface | un `QTimer` appelle `Network.snapshot()` chaque seconde ; le graphe, l'historique de débit et le panneau se mettent à jour à partir de cette copie |
| Interface → moteur | traceroute : `probes.traceroute()` lancé dans un thread, résultat renvoyé par un signal Qt (`Bridge.traced`) |
| Démarrage → barre d'état | `Bridge.status`, appelé par le thread de démarrage |

Le graphe (`graph_view.py`) garde ses propres positions. Un nœud nouveau est posé près de la passerelle, ou près du cadre Internet ; la disposition à forces le place ensuite et se fige quand elle est stable.

## Étapes réalisées

| # | Étape | Vérifié par |
|---|---|---|
| 0 | Squelette | tests |
| 1 | Modèle | tests |
| 2 | Découverte ARP + graphe en étoile | tests + **réseau réel** (proxy ARP détecté, 10 vrais appareils) |
| 3 | Identification (fabricant, nom, type) | tests + **réseau réel** (noms DNS inverse, fabricants) ; NetBIOS sans résultat sur ce réseau |
| 4 | Latence | test du lissage + **réseau réel** |
| 5 | Temps réel | **réseau réel** : la fenêtre se met à jour chaque seconde |
| 6 | Capture du trafic | pcap rejoué + **réseau réel** (trafic vers Internet visible) |
| 7 | Destinations Internet | pcap rejoué (DNS, SNI, QUIC, non résolu) ; en réel, seulement « (non résolu) » pendant les 25 s de test |
| 8 | Particules, panneau, filtres | captures de la fenêtre sans écran |
| 9 | Traceroute, géoloc, carte | traceroute simulé ; carte avec tuiles réelles en capture ; **traceroute réel non testé** |
| 10 | Exports, setcap, `--iface` | test d'export PNG ; `setcap.sh` exécuté par toi (le Python du venv a les droits) |

## Écarts avec le plan initial

| Plan | Réalisé | Pourquoi |
|---|---|---|
| Latence par ping ICMP | Requête ARP unicast | Tout appareil du LAN répond à l'ARP ; beaucoup de téléphones et de PC Windows bloquent le ping |
| Scan unique au démarrage | Rescan toutes les 60 s + retrait à 5 min | Nécessaire pour voir arriver et partir les appareils |
| `diff_since` et `bps` dès l'étape 1 | Ajoutés aux étapes 5 et 6 | Inutiles avant |
| `fixtures/sample.pcap` | pcap généré dans le test | Lisible, pas de fichier binaire |
| Trafic Internet sur le lien PC ↔ passerelle (étape 6) | Nœud par domaine (étape 7) | Prévu par l'étape 7 |
| Vidéo reconnue par le port | Liste de domaines de streaming (`STREAMING_DOMAINS`) | La vidéo passe par le port 443 comme le web |
| Frontend web (FastAPI, WebSocket, Cytoscape.js, Leaflet) | Fenêtre Qt native (PySide6) | L'interface web ne fonctionnait pas ; une fenêtre native supprime serveur, WebSocket et dépendances chargées depuis Internet |
| `diff_since` (envoi des seuls changements) | Copie complète chaque seconde | Sans réseau entre moteur et interface, la copie suffit et c'est plus simple |
| Particules (plugin ou canvas) | Points dessinés par `QPainter` le long des liens | Dessin direct, sens et vitesse maîtrisés |
| Icône emoji par type d'appareil | Couleur + légende « Appareils » | Qt n'affiche pas les emojis de façon fiable (police de secours) |
| Historique de débit dans `Link` | Échantillonné par l'interface | N'alourdit pas le modèle ; vide au lancement |
| Actions par WebSocket (`rescan`, `traceroute`) | Pas de `rescan` ; traceroute dans un thread | Le rescan est automatique |
| `hops` dans une info-bulle | Dans le panneau de détails | Le panneau existait déjà |
| Toute réponse ARP = un appareil | Proxy ARP détecté et ignoré | Sur le réseau de test, une même MAC répondait pour les 512 adresses |
| `geoip2` + GeoLite2 | `maxminddb` + DB-IP City Lite ou GeoLite2 | Même format, moins de code ; DB-IP ne demande pas de compte |
| Internet replié par défaut (`recherche.md`) | Déplié par défaut | Les noms sont visibles tout de suite |
| `setcap` sur l'interpréteur du venv | venv recréé avec `--copies`, droits sur une copie dédiée `python-net` | Le venv pointait vers `/usr/bin/python3.14` : `setcap` aurait donné les droits à tout Python du système. Et sur le Python habituel du venv, `pip install` aurait exécuté le code des paquets avec ces droits |

## Raccourcis connus

Marqués `shortcut:` dans le code :

- **Type d'appareil** (`classify.py`) : mots-clés de fabricant, se trompent sur les marques multi-produits (Samsung, HP, TP-Link).
- **Catégorie de trafic** (`classify.py`) : par port ; seuls les domaines de `STREAMING_DOMAINS` sortent du « web ».
- **Domaine regroupé** (`classify.py`) : approximation de la Public Suffix List ; `tldextract` si les regroupements sont faux.
- **IP d'une destination** (`capture.py`) : première IP vue seulement ; le lieu affiché pour un CDN est souvent faux.

## Limites à connaître

- **Vue partielle** : sur un réseau avec switch, seul le trafic de cette machine est visible. Pour tout voir : lancer sur la passerelle ou utiliser un port miroir.
- **DNS sécurisé (DoH)** : les réponses DNS deviennent invisibles ; QUIC (vidéo YouTube) chiffre aussi le SNI → destinations « (non résolu) ».
- **ClientHello en deux segments** (Chrome) : SNI perdu, pas de réassemblage TCP.
- **Performance de la capture** : scapy plafonne vers quelques milliers de paquets/s ; au-delà, le débit est sous-estimé. Piste : capture en Rust ou `pyshark`.
- **Taille du graphe** : la disposition calcule toutes les paires de nœuds (n²) ; au-delà de quelques centaines de nœuds elle ralentit. Le regroupement par domaine et le retrait à 5 min limitent la casse.
- **Animation** : 30 images/s, toutes les particules redessinées ; peut ramer au-delà d'environ 100 liens actifs.
- **Disposition** : relancée quand un nœud ou un lien arrive ou part, ou quand une latence change assez pour que la longueur idéale bouge de plus de 15 unités (`RELAYOUT_LENGTH`) ; les petites variations ne la relancent pas.
- **Proxy ARP** : sur un tel réseau, un appareil silencieux qui n'a pas sa propre réponse ARP reste invisible.
- **Premier lancement** : la première identification a été lente (plus de 45 s) lors d'un essai, 9 s ensuite ; cause non confirmée (caches DNS et mDNS froids ?).
- **Table IP → nom** : grandit sans limite pendant la session.

## Sécurité

- Pas de serveur : rien n'écoute sur le réseau.
- Le traceroute ne vise qu'un nœud déjà présent dans le graphe, un seul à la fois (verrou dans `probes.py`).
- Les noms venant du réseau (mDNS, DNS, SNI) sont affichés en texte brut (`Qt.PlainText`).
- Scan ARP refusé au-delà de 1024 adresses.
- `setcap.sh` refuse un Python partagé, ne donne les droits qu'à `python-net` (jamais au Python de `pip`), et le restreint à ton compte (`chmod 700`).
