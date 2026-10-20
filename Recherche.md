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

Dans le graphe, **longueur du lien = latence**. Le nombre de sauts et le GeoIP s'affichent en info-bulle.

## Limite importante à connaître

Sur un réseau avec switch (presque tous aujourd'hui), une machine ne voit que **son propre trafic, le broadcast et le multicast**. Pour voir tout le trafic entre les autres appareils, il faut :
- lancer l'outil **sur le routeur ou la passerelle** (OpenWrt, pfSense, Raspberry Pi en passerelle), ou
- utiliser un **port miroir (SPAN)** sur un switch administrable, ou
- lire les exports **NetFlow/sFlow** du routeur s'il en fait.

Sans ça, la découverte des appareils marche quand même ; seuls les liens entre appareils tiers manqueront.

> À utiliser uniquement sur un réseau qu'on possède ou qu'on a l'autorisation d'analyser. La capture demande root ou `CAP_NET_RAW`/`CAP_NET_ADMIN`.

## Architecture

```
┌──────────────┐   ┌───────────────┐   ┌──────────────┐   WebSocket   ┌─────────────┐
│ Découverte   │──▶│               │   │              │──────────────▶│  GUI        │
│ (actif)      │   │  Modèle du    │──▶│  Serveur     │               │  (graphe)   │
├──────────────┤   │  réseau       │   │  local       │◀──────────────│             │
│ Capture      │──▶│  (nœuds +     │   │              │   filtres     └─────────────┘
│ (passif)     │   │   liens)      │   └──────────────┘
├──────────────┤   │               │
│ Mesures      │──▶│               │
│ (latence)    │   └───────────────┘
└──────────────┘
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
- Ping périodique de chaque appareil connu → RTT moyen + gigue.
- Traceroute vers les destinations Internet principales → nombre de sauts.
- GeoIP sur les IP publiques.

### 4. Modèle
- **Nœud** : IP, MAC, nom, fabricant, type, première et dernière apparition, RTT.
- **Lien** : paire de nœuds, protocoles, débit actuel, total échangé, historique court.
- Tout en mémoire. Une base SQLite seulement si l'historique long devient nécessaire.

### 5. Visualisation
- **Graphe à forces** : la passerelle au centre, les appareils du LAN autour, Internet regroupé en un nuage (déplié à la demande).
- Encodage visuel :
  - **Longueur du lien** → latence
  - **Épaisseur du lien** → débit
  - **Couleur du lien** → protocole (web, DNS, streaming, P2P, chiffré/inconnu…)
  - **Particules animées** le long du lien → sens et intensité du trafic en direct
  - **Icône du nœud** → type d'appareil
  - **Taille du nœud** → volume total échangé
- Clic sur un nœud : détails, liste des connexions, graphique de débit.
- Filtres : par protocole, par appareil, LAN seul / Internet inclus.
- Vue optionnelle : carte du monde (Leaflet) pour les destinations Internet.

## Stack recommandée

**Backend Python + frontend web dans le navigateur.**

| Rôle | Choix |
|---|---|
| Scan ARP, capture, ping | `scapy` |
| mDNS | `zeroconf` |
| Fabricant MAC | `manuf` ou fichier OUI |
| GeoIP | `geoip2` + GeoLite2 |
| Serveur + WebSocket | `FastAPI` |
| Graphe | `Cytoscape.js` (ou `vis-network`) |
| Carte | `Leaflet` |

Pourquoi : scapy fait la découverte et la capture dans une seule bibliothèque, et une page web évite d'écrire un GUI natif.
Alternative si les performances deviennent un problème (gros débit) : réécrire la capture en Rust (`pnet` ou `pcap`) et garder le même frontend.

## Étapes

1. **MVP découverte** : scan ARP + fabricant + nom → liste des appareils affichée en graphe en étoile autour de la passerelle.
2. **Latence** : ping périodique, longueur des liens = RTT.
3. **Capture** : flux réels entre les nœuds, épaisseur et couleur selon le trafic.
4. **Temps réel** : mises à jour par WebSocket, particules animées.
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
