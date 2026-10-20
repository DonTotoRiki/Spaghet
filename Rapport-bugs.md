# Spaghet — Rapport de bugs

*21 octobre 2026. Audit de `spaghet/*.py`, `tests/` et `setcap.sh`. Aucun code n'a été modifié.*

**Ce que fait le projet :** Spaghet découvre les appareils du réseau local (scan ARP, noms, fabricants), écoute le trafic de la machine, et dessine le tout dans une fenêtre Qt : un graphe animé et une carte du monde. Tout tourne dans un seul processus, avec des threads de fond qui écrivent dans un état partagé en mémoire.

**Charge supposée :** une personne sur un réseau domestique (moins de 50 appareils), avec des sessions de quelques heures.

**Comment c'est vérifié :** les points marqués *(vérifié)* ont été reproduits sur ta machine. Les autres viennent de la lecture du code, avec un cas concret.

---

## À corriger en priorité

### 1. ✅ Corrigé — Presque tout ton trafic est invisible : l'IPv6 est ignoré *(vérifié)*
`spaghet/capture.py:87`, `capture.py:44-66`
- **Ce que c'est :** la capture écoute les paquets avec le filtre `ip`, qui ne laisse passer que l'IPv4. Le traitement ne connaît que la couche IPv4 et ne lit que les réponses DNS de type A (adresses IPv4).
- **Problème :** ton réseau a une route IPv6 par défaut. Pendant 20 secondes, j'ai mesuré **21 450 octets en IPv6 contre 180 en IPv4** : plus de 99 % du trafic n'apparaît pas. YouTube, Google, Netflix, Facebook passent en IPv6 dès qu'il est disponible. C'est pour ça que l'essai réel n'a montré presque aucun trafic.
- **Correction :** filtre `ip or ip6`. Dans `handle`, lire `IPv6` comme `IP`. Dans `endpoint`, considérer comme local ce qui est en `fe80::/10` ou dans le même /64 que les adresses de la machine, et ignorer le multicast `ff00::/8`. Dans `learn`, retenir aussi les réponses AAAA (type 28). Environ 25 lignes, plus un paquet IPv6 dans le test de rejeu.
- **Si on ne fait rien :** le graphe reste presque vide alors que le réseau est occupé, et l'outil est trompeur.

### 2. ✅ Corrigé — Un seul paquet DNS abîmé arrête la capture pour toujours, sans prévenir *(vérifié)*
`spaghet/capture.py:44-49`
- **Ce que c'est :** `learn()` lit la question d'une réponse DNS (`pkt[DNS].qd[0].qname`) pour nommer les destinations.
- **Problème :** quand la réponse est malformée, scapy ne sait pas la décoder et `qname` n'existe pas : `AttributeError`. J'ai reproduit l'erreur avec des octets au hasard sur le port 53. Or scapy n'intercepte pas les erreurs du traitement : la boucle de capture s'arrête définitivement. La fenêtre continue de s'afficher, les débits retombent à zéro, et aucun message n'apparaît.
- **Correction :** dans `handle`, entourer le traitement d'un `try/except Exception` qui ignore le paquet (et l'affiche une fois dans le terminal). Ajouter un test qui envoie un paquet DNS au hasard au gestionnaire.
- **Si on ne fait rien :** sur un réseau un peu bruyant, Spaghet arrête de voir le trafic au bout d'un temps imprévisible, et rien ne le signale.

---

## À corriger

### 3. ✅ Corrigé — Les appareils derrière un répéteur Wi-Fi peuvent disparaître
`spaghet/discovery.py:147`, `discovery.py:174-177`
- **Ce que c'est :** une même adresse MAC qui répond pour plus de 8 adresses IP est prise pour un proxy ARP, et toutes ses réponses sont ignorées.
- **Problème :** beaucoup de répéteurs Wi-Fi en mode pont font de la « traduction de MAC » : tous les appareils connectés derrière eux répondent avec la MAC du répéteur. Avec 9 téléphones ou PC derrière un répéteur, ils disparaissent tous du graphe. Un vrai proxy ARP, lui, répond pour **toutes** les adresses : 511 sur 512 sur ton réseau.
- **Correction :** comparer à la taille du sous-réseau : proxy seulement si la MAC répond pour plus de la moitié des adresses, `n > max(8, subnet.num_addresses // 2)`. Une ligne, plus le nombre d'adresses passé en paramètre. Ton réseau reste correctement détecté.
- **Si on ne fait rien :** des appareils bien réels manquent, sans explication.

### 4. ✅ Corrigé — Sur la carte, un clic peut viser le mauvais point *(vérifié)*
`spaghet/map_view.py:477-484`
- **Ce que c'est :** `point_at()` cherche le point le plus proche de la souris.
- **Problème :** la condition `d < max(best_d, rayon + 2)` laisse un point **plus éloigné** remplacer le meilleur s'il est gros. Essai : souris pile sur un petit point, un gros point à 12 px → le résultat est le gros. Les destinations d'un même hébergeur se chevauchent souvent.
- **Correction :** `best, best_d = None, math.inf`, puis `if d <= point[4] + 2 and d < best_d:`.
- **Si on ne fait rien :** l'infobulle et le panneau montrent parfois une autre destination que celle survolée.

### 5. ✅ Corrigé — Le panneau de détails se reconstruit chaque seconde
`spaghet/gui.py:117-149`, appelé par `gui.py:288`
- **Ce que c'est :** chaque seconde, `show_node()` efface puis recrée toutes les lignes d'infos et toute la liste des connexions.
- **Problème :** pour un appareil avec 40 connexions, si tu descends dans la liste, elle remonte en haut à chaque seconde. Un texte sélectionné est désélectionné, et un clic qui tombe pendant la reconstruction est perdu.
- **Correction :** ne reconstruire le formulaire et la liste que si leur texte a changé (comparer la liste des textes affichés), et mettre seulement le mini-graphe à jour chaque seconde.
- **Si on ne fait rien :** le panneau est pénible à utiliser dès qu'il y a beaucoup de connexions.

### 6. ✅ Corrigé — Avec Wi-Fi et câble branchés, Spaghet peut écouter la mauvaise interface
`spaghet/discovery.py:153`
- **Ce que c'est :** sans `--iface`, l'interface est celle de la première route par défaut trouvée dans la table.
- **Problème :** quand il y a plusieurs routes par défaut (portable branché en Ethernet avec le Wi-Fi allumé), l'ordre de la table ne suit pas la priorité. Spaghet peut scanner et écouter le Wi-Fi alors que le trafic passe par le câble.
- **Correction :** prendre la route par défaut de plus petite métrique (le 6ᵉ champ) : `min((r for r in routes if …), key=lambda r: r[5], default=None)`.
- **Si on ne fait rien :** graphe presque vide sur un portable, sans raison visible.

### 7. ✅ Corrigé — Le graphe se redessine 30 fois par seconde même quand rien ne bouge
`spaghet/graph_view.py:206-210`
- **Ce que c'est :** une minuterie redessine tout le graphe toutes les 33 ms, tant que l'onglet est visible.
- **Problème :** sur un réseau calme, le graphe est figé et sans particules, mais il est quand même entièrement redessiné en continu, texte compris. Le processeur ne se repose jamais, ce qui use la batterie d'un portable.
- **Correction :** ne redessiner que si la disposition bouge encore (`temperature > 0`) ou si au moins un lien dépasse `ACTIVE_BPS`. Les clics, le zoom et le glisser redessinent déjà eux-mêmes.
- **Si on ne fait rien :** consommation inutile tant que la fenêtre est ouverte.

---

## Améliorations utiles

### 8. ✅ Corrigé — Fermer la fenêtre pendant l'identification ne termine pas tout de suite le programme
`spaghet/discovery.py:264`, `spaghet/__main__.py:71`
- **Ce que c'est :** l'identification lance les recherches DNS inverses dans un groupe de threads (`ThreadPoolExecutor`).
- **Problème :** à la sortie, Python attend la fin de ces threads, même si le thread qui les a lancés est « jetable ». Si tu fermes la fenêtre pendant les premières secondes, le processus reste vivant jusqu'à la fin des recherches DNS. C'est peut-être la cause du blocage du tout premier essai, mais ce n'est pas prouvé.
- **Correction :** dans `__main__.py`, terminer par `os._exit(gui.run(network, backend))`. Il n'y a rien à sauvegarder à la sortie.
- **Si on ne fait rien :** le terminal reste bloqué quelques secondes, parfois davantage, après la fermeture.

### 9. ✅ Corrigé — La zone de clic d'un nœud ne suit pas le zoom
`spaghet/graph_view.py:334`
- **Ce que c'est :** un clic sélectionne le nœud le plus proche à moins de 18 unités du graphe.
- **Problème :** ces 18 unités sont mesurées dans le graphe, pas à l'écran. Dézoomé à 20 %, il faut viser à 3,6 pixels près. Zoomé à 500 %, un clic à 80 pixels d'un nœud le sélectionne quand même.
- **Correction :** `best_d = 18 / self.zoom` (environ 18 pixels à l'écran à tous les zooms).
- **Si on ne fait rien :** sélection difficile quand on voit tout le graphe, et sélections inattendues de près.

### 10. ✅ Corrigé — Les points de la carte disparaissent quand on la fait défiler d'un tour
`spaghet/map_view.py:463-468`
- **Ce que c'est :** les tuiles se répètent à l'infini en longitude, mais les points ne sont dessinés qu'une fois.
- **Problème :** si tu fais glisser la carte d'un tour complet (environ 1 000 px au zoom de départ), le fond reste là mais tous les points ont disparu.
- **Correction :** dessiner chaque point dans la copie la plus proche du centre : `x += round((self.center.x() - x) / size) * size`, dans le dessin et dans `point_at`.
- **Si on ne fait rien :** carte vide et trompeuse après un grand déplacement.

### 11. ✅ Corrigé — Les appareils sans nom sont ré-identifiés chaque minute, indéfiniment
`spaghet/discovery.py:259`
- **Ce que c'est :** le rescan identifie les nœuds qui n'ont ni nom ni fabricant.
- **Problème :** un téléphone à MAC aléatoire sans nom DNS reste dans ce cas pour toujours. À chaque minute, Spaghet refait pour lui environ 7 secondes d'écoute mDNS et de requêtes NetBIOS, sans jamais rien obtenir de plus.
- **Correction :** garder dans `__main__.py` l'ensemble des IP déjà identifiées une fois, et ne passer à `identify` que les nouvelles.
- **Si on ne fait rien :** du bruit réseau et du temps de calcul toutes les minutes, pour rien.

### 12. ✅ Corrigé — « cette machine » sert d'identifiant à deux endroits
`spaghet/discovery.py:60`, `spaghet/probes.py:21`
- **Ce que c'est :** `ping_all` reconnaît ta propre machine parce que son nom vaut le texte `"cette machine"`.
- **Problème :** c'est la même règle écrite deux fois avec un texte d'affichage. Si l'on renomme ce libellé dans `discovery.py` (traduction, nom réel de la machine), `ping_all` se mesure lui-même et affiche une fausse latence de 0 ms.
- **Correction :** exclure l'IP locale renvoyée par `local_network()` plutôt que le nom.
- **Si on ne fait rien :** rien tant qu'on ne touche pas au libellé, puis un bug discret.

### 13. ✅ Corrigé — Deux valeurs de retour que personne n'utilise
`spaghet/model.py:77`, `model.py:93`
- **Ce que c'est :** `upsert_node` renvoie le nœud et `add_traffic` le lien.
- **Problème :** aucun appel, ni dans le code ni dans les tests, n'utilise ces valeurs. En plus, elles laisseraient l'appelant modifier un objet partagé hors du verrou.
- **Correction :** supprimer les deux `return` et leurs annotations.
- **Si on ne fait rien :** une porte ouverte pour une modification hors verrou, sans bénéfice.

---

## Trouvé en corrigeant 1 et 2

### 14. ✅ Corrigé — Ta machine peut se retrouver dessinée dans le cadre « Internet »
`spaghet/graph_view.py:242-251`
- **Ce que c'est :** le cadre Internet est le rectangle qui englobe toutes les destinations Internet.
- **Problème :** ta machine est reliée à presque toutes ces destinations, donc la disposition la place au milieu d'elles, et elle finit à l'intérieur du rectangle. Constaté sur l'essai réel : « cette machine » apparaît comme si elle faisait partie d'Internet.
- **Correction :** exclure les destinations Internet de la gravité vers le centre et les pousser à l'écart du LAN, par exemple avec une force qui éloigne le groupe Internet du centre du LAN.
- **Si on ne fait rien :** le dessin suggère que ta machine est sur Internet.

**Verdict :** les 14 points sont corrigés et 41 tests passent. Tous ont un test, sauf le 8 (vérifié en réel : arrêt immédiat à la fermeture) et le 13 (simple suppression). L'essai sur le réseau réel fonctionne.

**Allègement possible :** environ −4 lignes, aucune dépendance en moins.

**Non vérifié :**
- le traceroute réel ;
- la carte avec une vraie base de géolocalisation ;
- la fenêtre sur un vrai écran Wayland (seulement des captures sans écran) ;
- le comportement sous fort débit (plus de 5 000 paquets/s) ;
- NetBIOS face à une machine Windows ;
- la cause de la lenteur du premier essai.
