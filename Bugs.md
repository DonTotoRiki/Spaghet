# Spaghet — Rapport de bugs n° 3

**Ce que fait le projet :** Spaghet découvre les appareils du réseau local, écoute le trafic de la machine (IPv4 et IPv6) et dessine le tout dans une fenêtre Qt : un graphe animé et une carte. Un seul processus, des threads de fond, un état partagé en mémoire.
**Charge supposée :** une personne sur un réseau domestique (moins de 50 appareils), souvent sur un portable, avec des sessions de quelques heures.
**Comment c'est vérifié :** *(vérifié)* = reproduit hors réseau avec un petit script ; *(lu dans le code)* = déduit de la lecture, avec le scénario qui le déclenche ; *(à vérifier)* = probable, mais demande un essai sur ton réseau.
Les 34 points des deux rapports précédents sont corrigés ; ils ne sont pas repris ici.

## À corriger en priorité

### 1. ✅ Corrigé — Si l'écoute mDNS échoue, aucun appareil n'est identifié ni jamais retiré *(vérifié)*
- **Ce que c'est :** `discovery.identify()` (`spaghet/discovery.py:138`) enchaîne DNS inverse, mDNS, NetBIOS, puis seulement enregistre fabricant, nom et type.
- **Problème :** si `_mdns()` ou `_netbios()` lève une erreur (interface sans multicast, VPN, `OSError: No such device`), rien n'est enregistré. Même le fabricant, qui est calculé localement à partir de la MAC, est perdu. Essai : `_mdns` qui lève `OSError` → fabricant `None`. Pire, dans `rescan()` (`spaghet/__main__.py:61`), l'erreur sort avant `network.prune()` : les appareils partis ne sont **jamais retirés**. Et comme `identified` n'est pas mis à jour, tout est réinterrogé chaque minute.
- **Correction :** dans `identify`, entourer `_mdns(...)` et `_netbios(...)` d'un `try/except OSError` qui renvoie `{}`. Dans `rescan`, appeler `prune` avant `identify_new`. Environ 6 lignes, plus un test qui fait échouer `_mdns`.
- **Si on ne fait rien :** sur certaines interfaces, le graphe ne montre que des IP grises, et les appareils fantômes s'accumulent.

### 2. ✅ Corrigé — Si le premier scan échoue, la latence et les nouveaux appareils ne sont plus jamais mesurés *(lu dans le code)*
- **Ce que c'est :** dans `backend()` (`spaghet/__main__.py:97-111`), `every(PROBE_INTERVAL, …)` et `every(RESCAN_INTERVAL, …)` ne sont lancés **qu'après** un premier scan réussi.
- **Problème :** si ce scan lève une erreur passagère (Wi-Fi pas encore prêt juste après la sortie de veille, `OSError: Network is down`), on saute au message « Découverte incomplète ». Les tâches de latence et de rescan ne sont jamais démarrées. La capture continue, mais plus aucun nouvel appareil, plus de latence ni de retrait, jusqu'à ce que tu relances Spaghet.
- **Correction :** démarrer les deux `every(...)` avant le `try` (ils attendent déjà leur intervalle avant la première tournée). Déplacer deux lignes.
- **Si on ne fait rien :** un lancement au mauvais moment donne un graphe figé, et le message ne dit pas qu'il faut relancer.

### 3. ✅ Corrigé — La carte redemande ses tuiles en échec chaque seconde, sans cache disque *(lu dans le code)*
- **Ce que c'est :** quand une tuile échoue, `_on_tile` la retire de `_pending` pour qu'elle soit redemandée « au prochain dessin » (`spaghet/map_view.py:62`). Or `set_points()` redessine la carte chaque seconde tant qu'elle est visible.
- **Problème :** hors connexion, ou si OpenStreetMap refuse (erreur 403/429), chaque tuile visible est redemandée **chaque seconde** : environ 24 requêtes par seconde en plein écran, indéfiniment. Il n'y a pas non plus de cache disque : chaque lancement retélécharge tout. La politique d'utilisation des tuiles OSM exige un cache et interdit ce genre de rafale. Le risque est un blocage de ton IP.
- **Correction :** retenir la date de l'échec (`self._failed[key] = time.monotonic()`) et ne pas redemander avant 30 s. Ajouter `QNetworkDiskCache` au `QNetworkAccessManager` (4 lignes, dans `~/.cache/spaghet`).
- **Si on ne fait rien :** la carte peut cesser de fonctionner pour toi, et Spaghet ne respecte pas les règles du service qu'il utilise.
- **Depuis :** l'onglet Carte a été retiré ; Spaghet ne contacte plus OpenStreetMap. Ce point est sans objet.

## À corriger

### 4. ✅ Corrigé — Molette de pavé tactile : la carte saute de plusieurs niveaux, et défiler sur le côté dézoome *(lu dans le code)*
- **Ce que c'est :** `wheelEvent` de la carte (`spaghet/map_view.py:141`) et du graphe (`spaghet/graph_view.py:435`) changent le zoom d'un cran **à chaque événement**, vers l'avant si `angleDelta().y() > 0`, sinon vers l'arrière.
- **Problème :** un pavé tactile envoie des dizaines de petits événements par geste : un glissement fait passer la carte du monde entier à la rue (niveaux 1 à 18). Un défilement horizontal (`y() == 0`) est compté comme « vers l'arrière » et dézoome.
- **Correction :** ignorer `y() == 0`. Pour la carte, cumuler `angleDelta().y()` et ne changer de niveau qu'à chaque tranche de 120. Pour le graphe, utiliser un facteur `1.15 ** (y / 120)`.
- **Si on ne fait rien :** sur portable, la carte est presque inutilisable au pavé tactile.
- **Depuis :** l'onglet Carte a été retiré ; la correction reste valable pour le graphe.

### 5. ✅ Corrigé — Sur ton réseau, la réponse du proxy ARP peut masquer celle du vrai appareil *(à vérifier)*
- **Ce que c'est :** `arping` (scan) et `srp` (latence, `spaghet/probes.py:26`) ne gardent que la **première** réponse à chaque requête.
- **Problème :** le premier rapport mesurait que le proxy répond pour 511 adresses sur 512, y compris donc celles d'appareils présents. Quand le proxy répond avant l'appareil, la vraie réponse est jetée. Au scan, l'appareil est alors filtré comme proxy et n'apparaît pas. À la mesure de latence, la réponse est refusée (mauvaise MAC), l'appareil vieillit et est retiré au bout de 5 min alors qu'il est là.
- **Pour vérifier :** comparer `arping("<ton sous-réseau>", multi=True)` et le même appel sans `multi`, avec `.venv/bin/python-net`. Si un appareil n'a sa vraie MAC que dans la première version, le bug est réel.
- **Correction :** passer `multi=True` aux deux appels, et garder la réponse dont la MAC n'est pas celle d'un proxy (scan) ou est la MAC attendue (latence).
- **Si on ne fait rien :** des appareils clignotent ou manquent, sans cause visible.
- **État :** corrigé par précaution (`multi=True` dans `scan` et `ping_all`, testé hors réseau). Le bug n'a pas encore été observé sur ton réseau : la vérification ci-dessus reste utile.

## Améliorations utiles

### 6. ✅ Corrigé — Sur un réseau calme, la longueur des liens ne montre jamais la latence *(vérifié)*
- **Ce que c'est :** la disposition n'est relancée que quand un nœud ou un lien arrive ou part (`spaghet/graph_view.py:120`). Or la première disposition se fait **avant** la première mesure de latence : tous les liens ont la longueur par défaut.
- **Problème :** essai : un appareil passe de « latence inconnue » à 80 ms. La disposition ne repart pas (température 0) et le lien reste à 189 au lieu de 377. Tant que rien d'autre ne change, « longueur = latence », l'idée centrale du graphe, n'est pas visible. C'est noté dans les limites de `docs/architecture.md`, mais l'effet est plus large que prévu : sur un réseau calme, la latence n'apparaît jamais.
- **Correction :** dans `update_data`, relancer aussi quand une latence change de plus de 20 % (comparer l'ancien et le nouveau `rtt_ms` par nœud). Environ 5 lignes.
- **Fait autrement :** un seuil en % aurait relancé le graphe toutes les 10 s pour les appareils filaires (0,5 → 0,7 ms = +40 %). La relance se fait donc quand la **longueur idéale** du lien bouge de plus de 15 unités (`RELAYOUT_LENGTH`), par rapport à la dernière relance : une dérive lente finit aussi par compter.

### 7. ✅ Corrigé — « Capture relancée » reste affiché toute la session, et se répète quand le Wi-Fi est coupé *(lu dans le code)*
- **Ce que c'est :** `watch_capture` (`spaghet/__main__.py:80`) écrit le message dans la barre d'état. Rien ne l'efface ensuite.
- **Problème :** après une seule coupure, la barre d'état dit « Capture relancée après une coupure » pendant des heures. Pendant que l'interface est coupée, la relance échoue et recommence toutes les 5 s.
- **Correction :** effacer le message (`status()`) au premier passage où la capture tourne à nouveau ; afficher « Capture en pause : interface coupée » tant que la relance échoue.

### 8. ✅ Corrigé — Un traceroute sans réponse laisse affiché l'ancien nombre de sauts *(lu dans le code)*
- **Ce que c'est :** `_traceroute` enregistre `hops=None` quand la cible ne répond pas (`spaghet/probes.py:66`), mais `upsert_node` n'écrase jamais une valeur par `None`.
- **Problème :** le panneau affiche à la fois « La cible n'a pas répondu » et « Sauts : 12 », la valeur d'un essai précédent.
- **Correction :** effacer la valeur en passant directement par le verrou (`network.nodes[node_id].hops = None` sous `network._lock`), ou ajouter un paramètre `clear=("hops",)` à `upsert_node`.

### 9. ✅ Corrigé — La base GeoIP n'est trouvée que si on lance Spaghet depuis son dossier *(lu dans le code)*
- **Ce que c'est :** le chemin par défaut `geoip.mmdb` est relatif au **dossier courant** (`spaghet/geo.py:6`), et `spaghet.sh` ne change pas de dossier.
- **Problème :** `~/Bureau/Spaghet/spaghet.sh` lancé depuis `~` ne trouve pas la base : la carte reste vide. Le message affiché nomme aussi `geoip.mmdb` même quand c'est `$SPAGHET_GEOIP` qui manque (`spaghet/__main__.py:41`).
- **Correction :** dans `spaghet.sh`, `export SPAGHET_GEOIP="${SPAGHET_GEOIP:-$dir/geoip.mmdb}"`. Dans le message, afficher le chemin réellement essayé (faire renvoyer le chemin par `geo.load`, ou le recalculer).

### 10. ✅ Corrigé — Les adresses privées hors du sous-réseau sont comptées comme Internet *(lu dans le code)*
- **Ce que c'est :** `endpoint()` (`spaghet/capture.py:88`) ne reconnaît comme local que l'IPv4 du sous-réseau. Le reste est traité comme une destination Internet.
- **Problème :** un autre réseau de la maison (192.168.1.x derrière un second routeur), un VPN d'entreprise (10.x), le CGNAT du fournisseur (100.64.x) ou une adresse 169.254.x finissent dans « (non résolu) », mélangés au vrai Internet, avec une recherche GeoIP inutile.
- **Correction :** si `addr.is_private` ou `addr in 100.64.0.0/10`, créer un nœud « réseau privé » par adresse (ou un seul nœud groupé), sans géolocalisation.

### 11. ✅ Corrigé — `setcap.sh` échoue avec une erreur obscure si Spaghet est ouvert *(lu dans le code)*
- **Ce que c'est :** le script copie l'interpréteur par-dessus `.venv/bin/python-net` (`setcap.sh:15`).
- **Problème :** si Spaghet tourne, Linux refuse d'écrire sur un exécutable en cours d'utilisation : `cp: Text file busy`, et le script s'arrête (`set -e`) sans explication.
- **Correction :** copier vers un fichier temporaire puis le renommer (`cp "$py" .venv/bin/python-net.tmp && mv -f …`), ce qui marche même quand le programme tourne. Ou bien détecter le cas et dire « ferme Spaghet d'abord ».

### 12. ✅ Corrigé — La logique de démarrage n'a aucun test
- **Ce que c'est :** `__main__.py` lit les options, ouvre la fenêtre et contient `rescan`, `backend`, `watch_*`, tout ça au niveau du module : l'importer lance l'application.
- **Problème :** les bugs 1, 2 et 7 sont tous dans cette partie, que les 54 tests ne couvrent pas.
- **Correction :** mettre le code du module dans une fonction `main()` (appelée sous `if __name__ == "__main__":`). Les tâches de fond deviennent importables et testables avec un faux `discovery`. Ajouter au passage `[project.scripts] spaghet = "spaghet.__main__:main"` dans `pyproject.toml`.
- **Fait :** état et tâches de fond dans une classe `Backend`, options dans `main()`, 4 tests dans `tests/test_main.py` (corrections 1, 2 et 7, plus le changement de réseau).
- **Pas fait :** la commande `spaghet` de `[project.scripts]`. Elle lancerait le Python habituel du venv, sans droits réseau, au lieu de `python-net` : ce serait une deuxième façon de lancer Spaghet, qui ne marche pas. `./spaghet.sh` reste le lanceur.

### 13. Deux lignes périmées dans `docs/architecture.md`
- « Table IP → nom : grandit sans limite » : faux depuis `MAX_NAMES` (`spaghet/capture.py:16`).
- « Scan ARP refusé au-delà de 1024 adresses » : le scan se limite maintenant au /24 de la machine (`spaghet/discovery.py:68`).

## Bilan

| # | Gravité | Effort |
|---|---|---|
| 1 | haute | ~6 lignes + test |
| 2 | haute | 2 lignes déplacées |
| 3 | haute | ~10 lignes |
| 4 | moyenne | ~6 lignes |
| 5 | moyenne, à vérifier | ~6 lignes |
| 6–11 | faible | 1 à 5 lignes chacun |
| 12 | entretien | refactorisation du point d'entrée |
| 13 | doc | 2 lignes |
