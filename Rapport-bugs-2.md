# Spaghet — Rapport de bugs n° 2

*21 octobre 2026. Deuxième passage sur `spaghet/*.py`, `tests/` et `setcap.sh`, après les corrections du premier rapport. Aucun code n'a été modifié.*

**Ce que fait le projet :** Spaghet découvre les appareils du réseau local, écoute le trafic de la machine (IPv4 et IPv6) et dessine le tout dans une fenêtre Qt, avec un graphe animé et une carte. Un seul processus, des threads de fond, un état partagé en mémoire.

**Charge supposée :** une personne sur un réseau domestique (moins de 50 appareils), souvent sur un portable qui se met en veille ou change de Wi-Fi, avec des sessions de quelques heures.

**Comment c'est vérifié :** *(vérifié)* signifie reproduit sur ta machine ; *(lu dans le code)* signifie déduit de la lecture, avec le scénario qui le déclenche.

---

## À corriger en priorité

### 1. Sur ton réseau, un appareil parti ne disparaît jamais *(vérifié)*
`spaghet/probes.py:16-31`
- **Ce que c'est :** toutes les 10 secondes, `ping_all` envoie une requête ARP à chaque appareil connu. Une réponse compte comme « l'appareil est là » : sa latence est mesurée et sa date de dernière activité est rafraîchie.
- **Problème :** sur ton réseau, l'équipement « proxy ARP » répond **aussi aux requêtes adressées à un appareil absent**. Essai : une adresse libre (192.168.129.77) avec une MAC inventée a reçu une réponse de `9a:55:9a:55:9a:55`. Le code ne vérifie pas qui répond. Un téléphone qui quitte la maison reste donc affiché comme présent : il n'est jamais grisé ni retiré, et sa « latence » est en fait celle du proxy.
- **Correction :** n'accepter que les réponses venant de la MAC attendue : `if reply.hwsrc != node.mac: continue`. Une ligne et un test. MSI, interrogé avec une mauvaise MAC, a bien répondu avec la sienne : les vrais appareils restent mesurés.
- **Si on ne fait rien :** le graphe accumule des appareils fantômes et la latence affichée est fausse.

### 2. Si le Wi-Fi se reconnecte, la capture s'arrête pour toujours *(lu dans le code)*
`spaghet/capture.py:126-131`, `spaghet/__main__.py:68`
- **Ce que c'est :** la capture est lancée une seule fois au démarrage, dans un thread de scapy.
- **Problème :** quand la lecture du socket échoue (interface coupée : câble débranché, Wi-Fi qui se reconnecte, sortie de veille), scapy ferme le socket. Comme c'était le seul, la boucle de capture se termine. Spaghet ne le vérifie jamais : la fenêtre reste ouverte, les débits tombent à zéro, et seul un avertissement de scapy apparaît dans le terminal. Je ne l'ai pas reproduit pour ne pas couper ta connexion.
- **Correction :** dans `__main__.py`, une tâche `every(5, …)` qui vérifie `sniffer.running`. Si la capture est arrêtée, elle la relance et l'indique dans la barre d'état. Une dizaine de lignes.
- **Si on ne fait rien :** sur un portable, l'outil devient silencieusement inutile après la première veille.

---

## À corriger

### 3. Sur un grand réseau, ou si une étape facultative échoue, rien ne démarre *(lu dans le code)*
`spaghet/discovery.py:60-61`, `spaghet/__main__.py:55-76`
- **Ce que c'est :** le démarrage enchaîne scan ARP, identification, latence, **puis seulement** la capture. Toute erreur saute directement au message « Erreur : … ».
- **Problème :** sur un réseau en /16 (65 536 adresses, fréquent en entreprise et sur certains systèmes Wi-Fi maillés), le scan refuse de tourner. Le démarrage s'arrête là : ni capture, ni latence, ni rescan. Il suffit aussi que l'écoute mDNS échoue, par exemple sur une interface sans multicast, pour perdre la capture, alors que l'identification n'est qu'un bonus.
- **Correction :** lancer la capture et le calcul de débit **en premier**. Si le sous-réseau est trop grand, scanner seulement le /24 qui contient la machine : `ipaddress.ip_interface(f"{my_ip}/24").network`.
- **Si on ne fait rien :** sur ces réseaux, Spaghet affiche une erreur et ne montre aucun trafic.

### 4. Une base de géolocalisation encore compressée fait planter le démarrage *(vérifié)*
`spaghet/geo.py:9-14`
- **Ce que c'est :** au démarrage, `geo.load()` ouvre `geoip.mmdb` s'il existe.
- **Problème :** DB-IP fournit un fichier `.mmdb.gz`. Si on le renomme sans le décompresser, `maxminddb` lève `InvalidDatabaseError` (reproduit). L'erreur n'est pas rattrapée : Spaghet plante avec une trace Python, avant même d'ouvrir la fenêtre.
- **Correction :** `try: … except (OSError, maxminddb.InvalidDatabaseError)`, puis continuer sans géolocalisation, en le signalant dans la barre d'état (« base illisible : décompresse-la »).
- **Si on ne fait rien :** une erreur de manipulation courante empêche complètement de lancer l'outil.

### 5. Le traceroute annonce 1 saut pour un site Internet *(vérifié)*
`spaghet/probes.py:50-62`
- **Ce que c'est :** le traceroute envoie des paquets TCP vers le port 443 avec une durée de vie (TTL) croissante. La cible est considérée comme atteinte à la première réponse TCP.
- **Problème :** essai réel : **wikipedia.org à 1 saut**, en moins d'une seconde. C'est impossible depuis un réseau domestique : il y a au moins la box et le fournisseur d'accès. Un équipement intermédiaire répond donc au TCP à la place de la cible (proxy transparent, ou NAT de machine virtuelle). La réponse est acceptée sans contrôle.
- **Correction :** pour une destination Internet, une cible « atteinte » au premier saut est forcément interceptée. Dans ce cas, renvoyer « réponse interceptée par un équipement intermédiaire » au lieu d'un nombre de sauts.
- **Si on ne fait rien :** le panneau affiche un nombre de sauts faux, présenté comme une mesure.

### 6. `python3` dans le venv n'a pas les droits réseau *(vérifié)*
`setcap.sh:6-15`
- **Ce que c'est :** `setcap.sh` donne les droits réseau à `.venv/bin/python`.
- **Problème :** le venv contient trois copies indépendantes de l'interpréteur : `python`, `python3` et `python3.14`. Seule la première reçoit les droits. Lancer `.venv/bin/python3 -m spaghet` affiche « Droits réseau manquants : lance ./setcap.sh », alors que le script a déjà été lancé, ce qui est déroutant. En plus, les deux autres copies restent exécutables par tout le monde (`-rwxr-xr-x`).
- **Correction :** dans `setcap.sh`, remplacer `python3` et `python3.14` par des liens vers `python` (`ln -sf python .venv/bin/python3`, idem pour `python3.14`).
- **Si on ne fait rien :** confusion assurée le jour où quelqu'un tape `python3`.

---

## Améliorations utiles

### 7. Le trafic IPv6 avec le routeur lui-même est perdu *(lu dans le code)*
`spaghet/capture.py:67-87`
- **Ce que c'est :** une adresse IPv6 locale est rattachée à un appareil grâce à sa MAC, cherchée parmi les MAC connues.
- **Problème :** sur ton réseau, le routeur n'est connu que par la MAC du proxy, `9a:55…`. Sa vraie MAC, déduite de son adresse IPv6 de lien local `fe80::6e3:1aff:fec2:3d42`, est `04:e3:1a:c2:3d:42`, et aucun nœud ne la porte. Tous les échanges IPv6 avec le routeur lui-même (DNS en IPv6, découverte de voisins) sont donc ignorés.
- **Correction :** quand un paquet va vers Internet, la MAC de destination est forcément celle du routeur. L'associer au nœud passerelle dans `by_mac` (une ligne dans `count`).
- **Si on ne fait rien :** le lien vers le routeur sous-estime le trafic DNS.

### 8. Une tuile de carte bloquée ne revient jamais *(lu dans le code)*
`spaghet/map_view.py:46-58`
- **Ce que c'est :** chaque tuile est demandée une seule fois et marquée « en attente » jusqu'à la réponse.
- **Problème :** les requêtes n'ont pas de délai maximal. Si une connexion reste bloquée (Wi-Fi faible, serveur lent), la tuile reste « en attente » pour toujours : elle n'est jamais redemandée, et un carré vide reste sur la carte jusqu'au redémarrage.
- **Correction :** `request.setTransferTimeout(10_000)` : au bout de 10 s, la requête échoue et la tuile sera redemandée au prochain dessin.
- **Si on ne fait rien :** des trous sur la carte après un passage réseau difficile.

---

**Verdict :** corriger d'abord les points 1 et 2. Sur ton réseau, le point 1 fausse déjà ce que tu vois.

**Allègement possible :** rien de notable cette fois.

**Remarque :** les essais suggèrent que cette machine est peut-être une machine virtuelle, d'après l'interface `enp1s0`, la MAC `9a:55:9a:55:9a:55` qui répond pour tout, et le TCP intercepté au premier saut. Si c'est le cas, Spaghet voit le réseau tel que la machine virtuelle le voit. Pour une vue fidèle, mieux vaut le lancer sur la machine physique.

**Non vérifié :**
- la coupure réelle de l'interface (point 2) ;
- un réseau en /16 (point 3) ;
- un vrai fichier DB-IP ;
- la fenêtre sur un vrai écran ;
- le comportement sous fort débit.
