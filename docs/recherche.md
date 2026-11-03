# Spaghet — Cartographie visuelle du réseau

## Objectif

Une application qui :
1. découvre tous les appareils présents sur le réseau local,
2. observe qui parle à qui, avec quel protocole et quel volume,
3. affiche le tout sous forme de graphe interactif où l'apparence des liens dépend des données échangées et de la « distance » entre les appareils.

## Ce que « distance » peut vouloir dire

| Mesure | Comment l'obtenir | Fiabilité |
|---|---|---|
| **Latence (RTT)** | ping ICMP / ARP, temps de réponse TCP | Bonne, c'est la mesure principale |
| **Nombre de sauts** | traceroute, TTL observé dans les paquets | Bonne pour Internet, toujours 0–1 sur le LAN |
| **Physique (Wi-Fi)** | RSSI du signal, seulement si la carte passe en mode monitor | Approximative (±5 m au mieux) |
| **Géographique (Internet)** | GeoIP (base MaxMind GeoLite2) | Au niveau de la ville, parfois faux |

Dans le graphe, **longueur du lien = latence**. Le nombre de sauts et le lieu GeoIP s'affichent dans le panneau de détails.

## Limite importante à connaître

Sur un réseau avec switch (presque tous aujourd'hui), une machine ne voit que **son propre trafic, le broadcast et le multicast**. Pour voir tout le trafic entre les autres appareils, il faut :
- lancer l'outil **sur le routeur ou la passerelle** (OpenWrt, pfSense, Raspberry Pi en passerelle), ou
- utiliser un **port miroir (SPAN)** sur un switch administrable, ou
- lire les exports **NetFlow/sFlow** du routeur s'il en fait.

Sans ça, la découverte des appareils marche quand même ; seuls les liens entre appareils tiers manqueront.

### Proxy ARP (constaté sur le réseau de test)

Certains réseaux (machine virtuelle, box, Wi-Fi d'entreprise) ont un équipement qui **répond à l'ARP pour toutes les adresses** avec sa propre MAC. Un scan ARP naïf y « trouve » alors un appareil par adresse : 512 sur un /23. C'est ce qui rendait la première interface inutilisable.

Détection : une même MAC qui répond pour plus de 8 IP est un proxy ; ses réponses sont ignorées (sauf pour la passerelle). Les vrais appareils, qui répondent avec leur propre MAC, restent visibles ; les autres n'apparaissent que s'ils échangent du trafic avec la machine.

> À utiliser uniquement sur un réseau qu'on possède ou qu'on a l'autorisation d'analyser. La capture demande root ou `CAP_NET_RAW`/`CAP_NET_ADMIN`.

## Architecture

```
┌──────────────┐   ┌───────────────┐  copie chaque  ┌──────────────────────┐
│ Découverte   │──▶│               │   seconde      │  Fenêtre Qt native   │
│ (actif)      │   │  Modèle du    │───────────────▶│  graphe · carte ·    │
├──────────────┤   │  réseau       │                │  détails · filtres   │
│ Capture      │──▶│  (nœuds +     │◀───────────────│                      │
│ (passif)     │   │   liens)      │   traceroute   └──────────────────────┘
├──────────────┤   │               │
│ Mesures      │──▶│               │
│ (latence)    │   └───────────────┘
└──────────────┘
      threads de fond                 un seul processus, pas de serveur ni de navigateur
```

### 1. Découverte (actif)
- **Scan ARP** du sous-réseau → IP + MAC de chaque appareil.
- **Fabricant** à partir du préfixe MAC (base OUI de l'IEEE).
- **Nom** via DNS inverse, mDNS/Bonjour, NetBIOS, SSDP/UPnP.
- **Type d'appareil** (téléphone, imprimante, TV, routeur…) déduit du fabricant, des services mDNS et des ports ouverts.
- Optionnel : scan de ports léger (top 100) pour identifier les services.

### 2. Capture (passif)
- libpcap sur l'interface choisie.
- Pour chaque paquet : source, destination, protocole, taille, horodatage.
- Agrégation en **flux** (src, dst, protocole, port) avec octets/s et paquets/s sur une fenêtre glissante.
- Identification du protocole applicatif : par port (80 = HTTP, 53 = DNS…), puis par SNI TLS et requêtes DNS pour nommer les destinations Internet (« youtube.com » plutôt qu'une IP).

### 3. Mesures
- Requête ARP périodique vers chaque appareil connu → RTT lissé (l'ARP passe là où beaucoup d'appareils bloquent le ping).
- Traceroute vers les destinations Internet principales → nombre de sauts.
- GeoIP sur les IP publiques.

### 4. Modèle
- **Nœud** : IP, MAC, nom, fabricant, type, première et dernière apparition, RTT.
- **Lien** : paire de nœuds, protocoles, débit actuel, total échangé, historique court.
- Tout en mémoire. Une base SQLite seulement si l'historique long devient nécessaire.

### 5. Visualisation
- **Graphe à forces** : la passerelle au centre, les appareils du LAN autour, Internet regroupé dans un cadre repliable.
- Encodage visuel :
  - **Longueur du lien** → latence
  - **Épaisseur du lien** → débit
  - **Couleur du lien** → protocole (web, DNS, streaming, P2P, chiffré/inconnu…)
  - **Particules animées** le long du lien → sens et intensité du trafic en direct
  - **Couleur du nœud** → type d'appareil (pas d'emoji : Qt ne les affiche pas de façon fiable)
- Clic sur un nœud : détails, liste des connexions, graphique de débit.
- Filtres : par protocole, par appareil, LAN seul / Internet inclus.
- Vue optionnelle : carte du monde (tuiles OpenStreetMap dessinées par Qt) pour les destinations Internet.

## Stack retenue

**Application Python avec une interface native Qt : pas de serveur, pas de navigateur.**

| Rôle | Choix |
|---|---|
| Scan ARP, capture, latence, traceroute | `scapy` |
| mDNS | `zeroconf` |
| Fabricant MAC | `manuf` |
| GeoIP | `maxminddb` + DB-IP City Lite (gratuite, sans compte) ou GeoLite2 |
| Interface | `PySide6` (Qt 6) : fenêtre, panneau, légende, exports |
| Graphe | dessiné avec `QPainter`, disposition à forces calculée avec `numpy` |
| Carte | tuiles OpenStreetMap téléchargées par `QNetworkAccessManager` |

### Pourquoi une interface native plutôt qu'une page web

La première version (FastAPI + WebSocket + Cytoscape.js + Leaflet dans le navigateur) n'a pas fonctionné sur la machine de test. Une interface native :
- **supprime des pièces** : plus de serveur web, de WebSocket, de format d'échange, ni de bibliothèques chargées depuis Internet ;
- **lit directement l'état** en mémoire (une copie par seconde) au lieu de le sérialiser ;
- **s'ouvre comme une application** (`python -m spaghet`), sans port à ouvrir ni page à charger ;
- reste **testable sans écran** (`QT_QPA_PLATFORM=offscreen`), y compris par des captures de la fenêtre.

Ce qu'on perd : Cytoscape et Leaflet faisaient la disposition du graphe et la carte. Il a fallu les écrire : environ 390 lignes pour le graphe, 140 pour la carte, plus 380 pour la fenêtre.

### Options étudiées pour l'interface native

| Option | Pour | Contre |
|---|---|---|
| **PySide6 (Qt)** ✔ | Dessin 2D rapide, animations, réseau intégré pour les tuiles, multiplateforme, licence LGPL | Paquet lourd (~200 Mo) |
| Tkinter | Inclus avec Python | Canvas lent pour les animations, rendu daté |
| Dear PyGui / pygame | Très rapides | Pas de widgets classiques (formulaires, listes), carte à refaire entièrement |
| GTK (PyGObject) | Natif sous Fedora/GNOME | Installation pénible hors Linux |
| pywebview | Réutilise la page web | Reste un moteur de navigateur, ce qu'on voulait éviter |

Alternative si les performances deviennent un problème (gros débit) : réécrire la capture en Rust (`pnet` ou `pcap`) et garder l'interface Qt.

## Étapes

1. **MVP découverte** : scan ARP + fabricant + nom → liste des appareils affichée en graphe en étoile autour de la passerelle.
2. **Latence** : ping périodique, longueur des liens = RTT.
3. **Capture** : flux réels entre les nœuds, épaisseur et couleur selon le trafic.
4. **Temps réel** : l'interface relit l'état chaque seconde, particules animées.
5. **Internet** : DNS/SNI pour nommer les destinations, traceroute, GeoIP, vue carte.
6. **Confort** : filtres, panneau de détails, export PNG/JSON, historique.

## Outils existants à étudier (pour s'inspirer)

- **Wireshark** : Statistiques → Conversations / Endpoints (l'analyse, sans le graphe)
- **EtherApe** : graphe de trafic en direct, très proche de l'idée
- **ntopng** : tableau de bord du trafic par hôte et par protocole
- **Nmap / Zenmap** : découverte + vue topologie
- **Fing** : découverte d'appareils et identification du type

## Questions ouvertes

- Où tournera l'outil : sur un PC du réseau (vue partielle) ou sur la passerelle (vue complète) ?
- Le Wi-Fi en mode monitor (RSSI, distance physique) en vaut-il la complexité ?
- Faut-il garder un historique dans le temps, ou seulement la vue en direct ?
