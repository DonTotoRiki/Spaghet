# Spaghet

Cartographie visuelle du réseau local : Spaghet découvre les appareils du réseau, observe qui parle à qui (protocole, volume) et affiche le tout dans une fenêtre Qt, sous forme de graphe animé.

- **Graphe** : longueur des liens = latence, épaisseur et particules = débit, couleur du lien = protocole, couleur du nœud = type d’appareil.
- **Détails** : fabricant, noms (DNS, mDNS, NetBIOS), sauts (traceroute), lieu des destinations Internet (GeoIP, base `.mmdb` optionnelle), exports PNG et JSON.

## Installation

Python ≥ 3.12, Linux.

```sh
python3 -m venv --copies .venv
.venv/bin/pip install -e '.[dev]'
./setcap.sh        # une fois : droits réseau sur une copie dédiée de Python (demande sudo)
```

## Lancement

```sh
./spaghet.sh [--iface IFACE] [--geoip FICHIER.mmdb]
```

Sans `--geoip`, Spaghet lit `$SPAGHET_GEOIP`, sinon `geoip.mmdb` dans le dossier du projet.

## Tests

```sh
.venv/bin/python -m pytest
```

## Documentation

- [docs/architecture.md](docs/architecture.md) : modules, modèle de données, limites, sécurité.
- [docs/recherche.md](docs/recherche.md) : étude préalable et choix techniques.
