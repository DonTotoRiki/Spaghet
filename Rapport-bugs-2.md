# Spaghet — Rapport de bugs n° 2

*21 octobre 2026. Deuxième passage sur `spaghet/*.py`, `tests/` et `setcap.sh`, après les corrections du premier rapport. Aucun code n'a été modifié.*

**Ce que fait le projet :** Spaghet découvre les appareils du réseau local, écoute le trafic de la machine (IPv4 et IPv6) et dessine le tout dans une fenêtre Qt, avec un graphe animé et une carte. Un seul processus, des threads de fond, un état partagé en mémoire.

**Charge supposée :** une personne sur un réseau domestique (moins de 50 appareils), souvent sur un portable qui se met en veille ou change de Wi-Fi, avec des sessions de quelques heures.

**Comment c'est vérifié :** *(vérifié)* signifie reproduit sur ta machine ; *(lu dans le code)* signifie déduit de la lecture, avec le scénario qui le déclenche.

---

## À corriger en priorité

### 1. ✅ Corrigé — Sur ton réseau, un appareil parti ne disparaît jamais *(vérifié)*
`spaghet/probes.py:16-31`
- **Ce que c'est :** toutes les 10 secondes, `ping_all` envoie une requête ARP à chaque appareil connu. Une réponse compte comme « l'appareil est là » : sa latence est mesurée et sa date de dernière activité est rafraîchie.
- **Problème :** sur ton réseau, l'équipement « proxy ARP » répond **aussi aux requêtes adressées à un appareil absent**. Essai : une adresse libre (192.168.129.77) avec une MAC inventée a reçu une réponse de `9a:55:9a:55:9a:55`. Le code ne vérifie pas qui répond. Un téléphone qui quitte la maison reste donc affiché comme présent : il n'est jamais grisé ni retiré, et sa « latence » est en fait celle du proxy.
- **Correction :** n'accepter que les réponses venant de la MAC attendue : `if reply.hwsrc != node.mac: continue`. Une ligne et un test. MSI, interrogé avec une mauvaise MAC, a bien répondu avec la sienne : les vrais appareils restent mesurés.
- **Si on ne fait rien :** le graphe accumule des appareils fantômes et la latence affichée est fausse.

### 2. ✅ Corrigé — Si le Wi-Fi se reconnecte, la capture s'arrête pour toujours *(lu dans le code)*
`spaghet/capture.py:126-131`, `spaghet/__main__.py:68`
- **Ce que c'est :** la capture est lancée une seule fois au démarrage, dans un thread de scapy.
- **Problème :** quand la lecture du socket échoue (interface coupée : câble débranché, Wi-Fi qui se reconnecte, sortie de veille), scapy ferme le socket. Comme c'était le seul, la boucle de capture se termine. Spaghet ne le vérifie jamais : la fenêtre reste ouverte, les débits tombent à zéro, et seul un avertissement de scapy apparaît dans le terminal. Je ne l'ai pas reproduit pour ne pas couper ta connexion.
- **Correction :** dans `__main__.py`, une tâche `every(5, …)` qui vérifie `sniffer.running`. Si la capture est arrêtée, elle la relance et l'indique dans la barre d'état. Une dizaine de lignes.
- **Si on ne fait rien :** sur un portable, l'outil devient silencieusement inutile après la première veille.

---

## À corriger

### 3. ✅ Corrigé — Sur un grand réseau, ou si une étape facultative échoue, rien ne démarre *(lu dans le code)*
`spaghet/discovery.py:60-61`, `spaghet/__main__.py:55-76`
- **Ce que c'est :** le démarrage enchaîne scan ARP, identification, latence, **puis seulement** la capture. Toute erreur saute directement au message « Erreur : … ».
- **Problème :** sur un réseau en /16 (65 536 adresses, fréquent en entreprise et sur certains systèmes Wi-Fi maillés), le scan refuse de tourner. Le démarrage s'arrête là : ni capture, ni latence, ni rescan. Il suffit aussi que l'écoute mDNS échoue, par exemple sur une interface sans multicast, pour perdre la capture, alors que l'identification n'est qu'un bonus.
- **Correction :** lancer la capture et le calcul de débit **en premier**. Si le sous-réseau est trop grand, scanner seulement le /24 qui contient la machine : `ipaddress.ip_interface(f"{my_ip}/24").network`.
- **Si on ne fait rien :** sur ces réseaux, Spaghet affiche une erreur et ne montre aucun trafic.

### 4. ✅ Corrigé — Une base de géolocalisation encore compressée fait planter le démarrage *(vérifié)*
`spaghet/geo.py:9-14`
- **Ce que c'est :** au démarrage, `geo.load()` ouvre `geoip.mmdb` s'il existe.
- **Problème :** DB-IP fournit un fichier `.mmdb.gz`. Si on le renomme sans le décompresser, `maxminddb` lève `InvalidDatabaseError` (reproduit). L'erreur n'est pas rattrapée : Spaghet plante avec une trace Python, avant même d'ouvrir la fenêtre.
- **Correction :** `try: … except (OSError, maxminddb.InvalidDatabaseError)`, puis continuer sans géolocalisation, en le signalant dans la barre d'état (« base illisible : décompresse-la »).
- **Si on ne fait rien :** une erreur de manipulation courante empêche complètement de lancer l'outil.

### 5. ✅ Corrigé — Le traceroute annonce 1 saut pour un site Internet *(vérifié)*
`spaghet/probes.py:50-62`
- **Ce que c'est :** le traceroute envoie des paquets TCP vers le port 443 avec une durée de vie (TTL) croissante. La cible est considérée comme atteinte à la première réponse TCP.
- **Problème :** essai réel : **wikipedia.org à 1 saut**, en moins d'une seconde. C'est impossible depuis un réseau domestique : il y a au moins la box et le fournisseur d'accès. Un équipement intermédiaire répond donc au TCP à la place de la cible (proxy transparent, ou NAT de machine virtuelle). La réponse est acceptée sans contrôle.
- **Correction :** pour une destination Internet, une cible « atteinte » au premier saut est forcément interceptée. Dans ce cas, renvoyer « réponse interceptée par un équipement intermédiaire » au lieu d'un nombre de sauts.
- **Si on ne fait rien :** le panneau affiche un nombre de sauts faux, présenté comme une mesure.

### 6. ✅ Corrigé — `python3` dans le venv n'a pas les droits réseau *(vérifié)*
`setcap.sh:6-15`
- **Ce que c'est :** `setcap.sh` donne les droits réseau à `.venv/bin/python`.
- **Problème :** le venv contient trois copies indépendantes de l'interpréteur : `python`, `python3` et `python3.14`. Seule la première reçoit les droits. Lancer `.venv/bin/python3 -m spaghet` affiche « Droits réseau manquants : lance ./setcap.sh », alors que le script a déjà été lancé, ce qui est déroutant. En plus, les deux autres copies restent exécutables par tout le monde (`-rwxr-xr-x`).
- **Correction :** dans `setcap.sh`, remplacer `python3` et `python3.14` par des liens vers `python` (`ln -sf python .venv/bin/python3`, idem pour `python3.14`).
- **Si on ne fait rien :** confusion assurée le jour où quelqu'un tape `python3`.

---

## Améliorations utiles

### 7. ✅ Corrigé — Le trafic IPv6 avec le routeur lui-même est perdu *(lu dans le code)*
`spaghet/capture.py:67-87`
- **Ce que c'est :** une adresse IPv6 locale est rattachée à un appareil grâce à sa MAC, cherchée parmi les MAC connues.
- **Problème :** sur ton réseau, le routeur n'est connu que par la MAC du proxy, `9a:55…`. Sa vraie MAC, déduite de son adresse IPv6 de lien local `fe80::6e3:1aff:fec2:3d42`, est `04:e3:1a:c2:3d:42`, et aucun nœud ne la porte. Tous les échanges IPv6 avec le routeur lui-même (DNS en IPv6, découverte de voisins) sont donc ignorés.
- **Correction :** quand un paquet va vers Internet, la MAC de destination est forcément celle du routeur. L'associer au nœud passerelle dans `by_mac` (une ligne dans `count`).
- **Si on ne fait rien :** le lien vers le routeur sous-estime le trafic DNS.

### 8. ✅ Corrigé — Une tuile de carte bloquée ne revient jamais *(lu dans le code)*
`spaghet/map_view.py:46-58`
- **Ce que c'est :** chaque tuile est demandée une seule fois et marquée « en attente » jusqu'à la réponse.
- **Problème :** les requêtes n'ont pas de délai maximal. Si une connexion reste bloquée (Wi-Fi faible, serveur lent), la tuile reste « en attente » pour toujours : elle n'est jamais redemandée, et un carré vide reste sur la carte jusqu'au redémarrage.
- **Correction :** `request.setTransferTimeout(10_000)` : au bout de 10 s, la requête échoue et la tuile sera redemandée au prochain dessin.
- **Si on ne fait rien :** des trous sur la carte après un passage réseau difficile.

---

## Trouvés en corrigeant

### 9. ✅ Corrigé — La correction du point 14 du premier rapport ne marchait qu'une fois sur 40 *(vérifié)*
`spaghet/graph_view.py`, `_layout_step`
- **Problème :** ta machine finissait dans le cadre Internet dans 39 dispositions sur 40, car les positions de départ sont tirées au hasard. Le test d'origine n'essayait qu'un seul tirage et était passé par chance. La poussée travaillait sur un cercle, alors que le cadre est un rectangle aux marges plus larges, qui grandissaient en plus avec le dézoom.
- **Correction :** le cadre a une marge fixe. Après chaque étape de disposition, tout appareil du LAN qui s'y trouve est replacé juste à l'extérieur : c'est une contrainte stricte, plus une force. L'ancrage du groupe Internet est aussi plus fort. Résultat : 0 échec sur 60 tirages, et le test en essaie maintenant 20.

### 10. ✅ Corrigé — Les recherches de noms de Spaghet dessinaient de faux contacts *(vérifié)*
`spaghet/capture.py`, `count`
- **Problème :** pour nommer les appareils, Spaghet les interroge un par un en NetBIOS. Pour le DNS inverse, `systemd-resolved` les interroge aussi directement en LLMNR (TCP 5355), ce qui a été vu sur le réseau réel. Ces échanges étaient comptés comme du trafic : ta machine apparaissait en contact direct avec tous les appareils. Le défaut existait déjà avant, pour chaque nouvel appareil identifié pendant un rescan. Le « contact direct » de MSI et d'Ismael venait très probablement de là. Le démarrage de la capture avant l'identification (point 3) l'avait aggravé.
- **Correction :** les ports de résolution de noms locale (137 NetBIOS, 5353 mDNS, 5355 LLMNR) sont ignorés par la capture.

## Correctifs supplémentaires

### 11. ✅ Corrigé — Un changement de réseau passait inaperçu
`spaghet/__main__.py`, `watch_network` ; `spaghet/discovery.py`, `current_network`
- **Problème :** l'interface, le sous-réseau et les préfixes IPv6 sont fixés au lancement. Sur un portable qui passe du Wi-Fi de la maison à celui d'un café, les appareils du nouveau réseau étaient classés comme Internet, sans aucun avertissement.
- **Correction :** toutes les 30 s, Spaghet relit la table de routage. Si le réseau a changé, la barre d'état demande de relancer. Essai réel de 70 s : aucune fausse alerte.

### 12. ✅ Corrigé — Le traceroute remplissait le nœud « (non résolu) »
`spaghet/capture.py`, `count`
- **Problème :** chaque routeur traversé par un traceroute répond par un message ICMP « délai dépassé ». Ces messages étaient comptés comme du trafic entre ta machine et « (non résolu) ».
- **Correction :** les messages d'erreur ICMP et ICMPv6 (destination injoignable, délai dépassé) sont ignorés. Un vrai ping compte toujours.

### 13. ✅ Corrigé — La table des noms grandissait sans limite
`spaghet/capture.py`, `learn`
- **Correction :** au-delà de 50 000 adresses retenues, la table repart de zéro. Les noms reviennent avec les réponses DNS suivantes.

### 14. ✅ Corrigé — Tailles et accords mal affichés *(vérifié)*
`spaghet/gui.py`
- **Problème :** « 1024.0 Ko » au lieu de « 1.0 Mo », « 1000.0 kbit/s » au lieu de « 1.0 Mbit/s », « 1 sauts », « 1 appareils ».
- **Correction :** l'unité est choisie d'après la valeur arrondie telle qu'affichée. Les deux fonctions de format, presque identiques, ont été fusionnées. Les pluriels suivent le français, où 0 et 1 restent au singulier.

### 15. ✅ Corrigé — Au lancement, la plupart du réseau était hors de l'écran *(vérifié)*
`spaghet/graph_view.py`, `_frame`, `fit`
- **Problème :** le graphe se cadrait une seule fois, sur la première copie non vide. Depuis que la capture démarre avant le scan (point 3), cette copie ne contient que ta machine et une ou deux destinations Internet. Résultat, vu en réel : la vue était zoomée sur ta machine, avec le routeur et presque tous les appareils hors de l'écran. Mes captures précédentes ne le montraient pas, car mon script d'essai recadrait lui-même avant de photographier.
- **Correction :** le graphe se recadre chaque fois que la disposition se stabilise, tant que tu n'as pas zoomé ni déplacé la vue toi-même. « Recadrer » réactive ce cadrage automatique. Sa marge couvre aussi le cadre Internet, qui touchait le bord.

### 16. ✅ Corrigé — Un changement d'adresse IP ou de passerelle passait inaperçu
`spaghet/__main__.py`, `watch_network`
- **Problème :** la surveillance du réseau (point 11) ne comparait que l'interface et le sous-réseau. Une nouvelle adresse IP donnée par la box (bail DHCP), ou une autre passerelle, sur le même sous-réseau, n'était pas signalée. Ta machine aurait alors été dessinée en double.
- **Correction :** l'adresse de la machine et la passerelle font partie de la comparaison.

### 17. ✅ Corrigé — `setcap.sh` sans `python3` dans le venv
`setcap.sh`
- **Problème :** si aucun fichier `python3*` n'existait, le shell gardait le motif tel quel et le script créait un lien nommé littéralement `python3*`.
- **Correction :** un motif sans correspondance est ignoré.

### 18. ✅ Corrigé — Le dessin dépassait le temps d'une image sur un gros réseau *(mesuré)*
`spaghet/graph_view.py`, `draw`
- **Problème :** avec 290 nœuds et 250 liens actifs, une image prenait 57 ms, pour un budget de 33 ms à 30 images/s : l'interface saturait tant que des particules bougeaient. Mesures : les bouts ronds des traits doublaient leur temps de tracé, et les 18 000 particules coûtaient le reste, tracé et calculs en Python.
- **Correction :** traits à bouts plats, et particules seulement sur les 40 liens les plus actifs. Résultat : 30 ms par image dans le même cas.

### 19. ✅ Corrigé — Une réponse DNS illisible faisait perdre son trafic
`spaghet/capture.py`, `count`
- **Problème :** depuis le point 2 du premier rapport, un paquet DNS malformé ne plante plus la capture, mais il était ignoré en entier, donc ses octets n'étaient pas comptés. Un test avec 4 000 paquets aléatoires ou malformés n'a montré que ce cas d'erreur.
- **Correction :** un nom illisible fait perdre le nom, pas le trafic.

### 20. ✅ Corrigé — `pip install` s'exécutait avec les droits réseau
`setcap.sh`, nouveau `spaghet.sh`
- **Problème :** les droits (`CAP_NET_RAW`, mais aussi `CAP_NET_ADMIN`, qui permet de modifier interfaces, routes et pare-feu) étaient sur le Python habituel du venv. Or c'est lui qui exécute `pip install` : le code d'installation de n'importe quel paquet, et tout script lancé avec ce Python, en profitait.
- **Correction :** les droits sont donnés à une copie dédiée, `.venv/bin/python-net`, et retirés du Python habituel. On lance Spaghet avec `./spaghet.sh`. **À faire de ton côté :** relancer `./setcap.sh` une fois (il demande `sudo`).

**Verdict :** les 10 points sont corrigés et 48 tests passent, sans échec sur trois lancements de suite. L'essai sur le réseau réel est propre. Avec les correctifs 11 à 20 : 54 tests.

**Allègement possible :** rien de notable cette fois.

**Remarque :** les essais suggèrent que cette machine est peut-être une machine virtuelle, d'après l'interface `enp1s0`, la MAC `9a:55:9a:55:9a:55` qui répond pour tout, et le TCP intercepté au premier saut. Si c'est le cas, Spaghet voit le réseau tel que la machine virtuelle le voit. Pour une vue fidèle, mieux vaut le lancer sur la machine physique.

**Non vérifié :**
- la coupure réelle de l'interface (point 2) ;
- un réseau en /16 (point 3) ;
- un vrai fichier DB-IP ;
- la fenêtre sur un vrai écran ;
- le comportement sous fort débit.
