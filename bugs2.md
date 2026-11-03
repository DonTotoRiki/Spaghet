# Spaghet — Rapport de bugs n° 4

**Ce que fait le projet :** Spaghet découvre les appareils du réseau local, écoute le trafic de la machine (IPv4 et IPv6) et dessine le tout dans une fenêtre Qt : un graphe animé avec un panneau de détails. Un seul processus, des threads de fond, un état partagé en mémoire.
**Charge supposée :** une personne sur un réseau domestique (moins de 50 appareils), souvent sur un portable, avec des sessions de quelques heures.
**Comment c'est vérifié :** *(vérifié)* = reproduit hors réseau avec un petit script ; *(mesuré)* = chronométré ; *(lu dans le code)* = déduit de la lecture, avec le scénario qui le déclenche.
**Périmètre :** le code actuel, après les corrections de `Bugs.md` (points 1 à 12), le retrait de la carte et le nouveau style. Plusieurs points ci-dessous sont des effets de bord de ces changements récents.

## À corriger en priorité

### 1. ⏳ À faire par toi — Le Python habituel du venv a toujours les droits réseau *(vérifié)*
- **Ce que c'est :** `setcap.sh` doit donner les droits réseau à une copie dédiée, `.venv/bin/python-net`, et les **retirer** du Python habituel.
- **Problème :** `getcap` montre `cap_net_admin,cap_net_raw=eip` sur `.venv/bin/python`, et `python-net` n'existe pas : la nouvelle version du script n'a jamais été lancée. Tout ce que lance ce Python a donc les droits de capture et d'envoi de paquets bruts : `pip install`, avec le code d'installation des paquets, et n'importe quel script du venv. C'est exactement le risque que le point 20 du deuxième rapport devait supprimer.
- **Correction :** lancer `./setcap.sh` une fois. Aucun changement de code.
- **Si on ne fait rien :** un paquet piégé installé dans le venv peut écouter tout le trafic de la machine.

## À corriger

### 2. ✅ Corrigé — Les adresses VPN et des autres réseaux privés comptent comme appareils du réseau local *(vérifié)*
- **Ce que c'est :** depuis la correction 10 de `Bugs.md`, une adresse privée hors du sous-réseau devient un nœud (`spaghet/capture.py:94`). Ce nœud n'est pas de type `internet`, donc tout le reste le traite comme un appareil du LAN.
- **Problème :** essai : 30 échanges avec des adresses `10.8.0.x` (VPN) → la barre d'état affiche **« 31 appareils »** (`spaghet/gui.py:335`). Ces nœuds reçoivent aussi le trait pointillé vers la passerelle réservé aux appareils du LAN (`spaghet/graph_view.py:184`), et l'identification leur envoie une recherche DNS inverse à chaque nouveau nœud. Avec un VPN d'entreprise, le graphe se remplit de faux appareils.
- **Correction :** un type à part, `kind="private"`, avec sa couleur et son libellé dans `KINDS` (« Réseau privé »). L'exclure du compte d'appareils, des traits vers la passerelle et de `identify` (comme `internet`). Environ 8 lignes et un test.
- **Si on ne fait rien :** sur un réseau avec VPN, le nombre d'appareils et la forme du graphe sont faux.

### 3. ✅ Corrigé — Au-delà de ~150 destinations, le dessin dépasse le temps d'une image *(mesuré)*
- **Ce que c'est :** le graphe est redessiné jusqu'à 30 fois par seconde tant qu'il bouge ou qu'un lien a des particules.
- **Problème :** mesuré hors écran, 20 appareils + 300 destinations : **64 ms par image** pour un budget de 33 ms (22 ms avec 100 destinations). La moitié du temps est dans le tracé des liens (`drawLine`, un appel par lien, lissé et semi-transparent, `spaghet/graph_view.py:329`). Ce n'est pas le nouveau style : l'ancien dessin prenait 69 ms. Une navigation chargée atteint vite quelques centaines de domaines en 5 minutes, et la fenêtre devient lente à réagir aux clics.
- **Correction :** grouper les liens par stylo (couleur, épaisseur, style) et les tracer d'un seul appel `p.drawLines(...)` par groupe ; ne lisser que les liens épais. À mesurer avant/après avec le même script.
- **Si on ne fait rien :** l'interface saccade sur une machine qui navigue beaucoup.
- **Fait autrement :** grouper les liens n'a presque rien gagné (63 → 58 ms). La vraie cause : un trait épais, lissé et **semi-transparent** coûte ~8 fois plus cher qu'un trait opaque. Les liens utilisent maintenant une couleur opaque déjà mélangée au fond (`opaque()`), rendu quasi identique : **25,5 ms** pour 300 destinations.

### 4. ✅ Corrigé — Deux identifications peuvent tourner en même temps *(lu dans le code)*
- **Ce que c'est :** le premier rescan est programmé 60 s après le démarrage, pendant que le démarrage fait sa propre identification (`spaghet/__main__.py:49`, `:54`).
- **Problème :** `docs/architecture.md` note une première identification de plus de 45 s. Si elle dépasse ~58 s, le rescan lance une deuxième identification des **mêmes** appareils : deux écoutes mDNS, des requêtes NetBIOS en double, et deux threads qui modifient l'ensemble `identified` en même temps.
- **Correction :** un `threading.Lock` dans `Backend` ; `identify_new` sort sans rien faire si une identification est déjà en cours (`acquire(blocking=False)`). 4 lignes et un test.

## Améliorations utiles

### 5. ✅ Corrigé — Sans droits réseau, le terminal reçoit la même erreur toutes les 10 s *(lu dans le code)*
- **Ce que c'est :** depuis la correction 2, la mesure de latence et le rescan tournent même si le premier scan a échoué. `every()` affiche chaque erreur (`spaghet/__main__.py:24`).
- **Problème :** sans `setcap.sh`, « ping_all : Operation not permitted » s'affiche toutes les 10 s et « rescan : … » toutes les 60 s, pendant toute la session.
- **Correction :** dans `every()`, n'afficher une erreur que si son texte diffère de la précédente.

### 6. ✅ Corrigé — « Proxy ARP détecté » n'apparaît jamais si le premier scan a échoué *(lu dans le code)*
- **Ce que c'est :** la remarque n'est ajoutée qu'au premier scan, dans `Backend.start`. `rescan()` ignore ce que renvoie `discovery.scan`.
- **Problème :** si le premier scan échoue (Wi-Fi pas prêt) et que le rescan réussit ensuite, la barre d'état n'explique jamais pourquoi il manque des appareils.
- **Correction :** déplacer l'ajout de la remarque dans une petite méthode appelée par `start` et `rescan`, qui ne l'ajoute qu'une fois.

### 7. ✅ Corrigé — La légende des appareils peut cacher des nœuds *(lu dans le code)*
- **Ce que c'est :** la légende est dessinée par-dessus le graphe, en bas à gauche (`spaghet/graph_view.py:386`). Le cadrage (`fit`, ligne 278) ne lui réserve pas de place.
- **Problème :** un appareil placé dans ce coin est caché, et on peut cliquer dessus sans le voir.
- **Correction :** dans `fit`, retirer la largeur de la légende de la zone utile ; ou rendre la légende repliable d'un clic.

### 8. ✅ Corrigé — Les filtres de protocole n'indiquent leur état que par la couleur *(lu dans le code)*
- **Ce que c'est :** la case à cocher des pastilles est cachée (`spaghet/gui.py:27`). Un protocole masqué se reconnaît seulement à sa pastille grise et à son texte plus sombre.
- **Problème :** pour une personne daltonienne ou au clavier, l'état coché/décoché est difficile à voir. Rien n'est annoncé aux lecteurs d'écran.
- **Correction :** barrer le texte quand le filtre est désactivé (`QCheckBox#chip:!checked { text-decoration: line-through; }`), et donner un nom accessible à chaque pastille.

### 9. ✅ Corrigé — « Internet » et « Inconnu » ont presque le même gris *(vu sur capture)*
- **Ce que c'est :** `#888888` et `#666666` (`spaghet/graph_view.py:17-18`).
- **Problème :** dans le graphe et la légende, on ne distingue pas une destination Internet d'un appareil non identifié.
- **Correction :** donner à « Inconnu » un gris nettement plus sombre avec un contour, ou une teinte froide (`#5b6b7f`).

### 10. ✅ Corrigé — L'export PNG n'a ni légende ni fond *(lu dans le code)*
- **Ce que c'est :** `export_png` appelle `draw()`, qui ne dessine que le graphe ; la légende et le dégradé sont ajoutés dans `paintEvent`.
- **Problème :** l'image exportée ne dit pas ce que signifient les couleurs des nœuds.
- **Correction :** appeler `_draw_legend` aussi dans `export_png`, après avoir remis la transformation à zéro.

### 11. ✅ Corrigé — Documentation en retard
- `Bugs.md`, point 13, n'est pas encore fait : deux lignes périmées dans `docs/architecture.md` (ligne 171 « Table IP → nom : grandit sans limite » ; ligne 178 « Scan ARP refusé au-delà de 1024 adresses »).
- `docs/architecture.md:5` annonce 63 tests ; il y en a 64.
- `docs/architecture.md:166` (« Taille du graphe ») ne parle que de la disposition ; le dessin coûte aussi (point 3).

## Vérifié et écarté
- **Le graphe se recadre-t-il quand le panneau de détails s'ouvre ?** Non : essai avec 11 appareils, zoom et position inchangés au clic.
- **Les nouveaux tests dépendent-ils du hasard ?** Non : les trois tests de disposition (positions de départ aléatoires) passent 40 fois sur 40.

## Bilan

| # | Gravité | Effort |
|---|---|---|
| 1 | haute (sécurité) | lancer `./setcap.sh` |
| 2 | moyenne | ~8 lignes + test |
| 3 | moyenne | ~15 lignes, à mesurer |
| 4 | faible | 4 lignes + test |
| 5–10 | faible | 1 à 5 lignes chacun |
| 11 | doc | 4 lignes |
