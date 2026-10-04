# Ponctualité ferroviaire — pipeline temps réel GTFS-RT

Pipeline de collecte et d'analyse de la ponctualité du réseau ferroviaire
français (TGV, Intercités, TER), à partir des données ouvertes du Point d'Accès
National [transport.data.gouv.fr](https://transport.data.gouv.fr).

**Objectif :** mesurer la ponctualité réelle en confrontant les passages temps
réel (GTFS-RT) aux horaires théoriques (GTFS), et restituer les résultats par
ligne, par gare et par tranche horaire — avec un focus Grand Est
(Mulhouse – Strasbourg – Bâle).

## Le rapport

Trois pages, construites sur les seuls passages observés. Les segments
« Jour de service » et « Mode » sont synchronisés d'une page à l'autre, et tous
les visuels se croisent : cliquer sur une ligne filtre le reste de la page.

![Vue d'ensemble du rapport Power BI](docs/images/powerbi-1-vue-ensemble.png)

La première page met le taux entre ses deux bornes et donne, juste à côté, la
raison de cet encadrement : le flux n'annonce les retards que par paliers de
5 minutes. La courbe horaire est doublée d'une comparaison semaine / week-end,
dont l'écart a été soumis à un test exact avant d'être affiché (journal du
03/10).

![Lignes et gares les moins ponctuelles](docs/images/powerbi-2-lignes-gares.png)

Les classements se recalculent à chaque filtre : le top 10 est une mesure de
rang en DAX, pas un filtre figé. Le nuage de points rappelle ce que les
classements ne disent pas — un taux extrême sur 20 passages ne pèse pas autant
qu'un taux médiocre sur 500.

![Méthode et qualité](docs/images/powerbi-3-methode.png)

La troisième page expose les corrections appliquées avant publication, et ce que
chacune a changé. C'est elle qui sépare une mesure d'une impression.

## Sources

| Source | Format | Contenu |
|---|---|---|
| [Horaires théoriques SNCF](https://transport.data.gouv.fr/datasets/horaires-sncf) | GTFS | Versions quotidiennes archivées et appliquées successivement (fenêtre glissante, voir journal du 12/09) |
| [SNCF GTFS-RT Trip Updates](https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates) | GTFS-RT | ~2 240 trajets et ~20 300 prévisions d'arrêt par appel |

Les données sont diffusées sous Licence Ouverte (Etalab) par le Point d'Accès
National ; elles ne relèvent pas de la licence MIT, qui couvre le code.

## Qualité des données — ce qui a été mesuré

Taux de jointure entre le flux temps réel et le référentiel théorique
(`src/reconcile_check.py`, mesure du 11/09/2026) :

| Jointure | Taux |
|---|---|
| `trip_id` (hors trajets non planifiés) | **99,85 %** (2 015 / 2 018) |
| `stop_id` | **100 %** (20 279 / 20 279) |

Ce taux est recalculé **par jour de service** à chaque exécution du contrôle
qualité (`src/quality_checks.py`), jamais en cumulé, avec un seuil d'échec à
95 % : du 11 au 15/09, 99,85 %, 99,91 %, 99,87 %, 99,71 % et 97,95 %.

Répartition des trajets du flux temps réel :

| `schedule_relationship` | Présent dans le GTFS | Nombre |
|---|---|---|
| `SCHEDULED` | oui | 1 991 |
| `CANCELED` | oui | 24 |
| `CANCELED` | non | 3 |
| `ADDED` | non | 220 |

Les 220 trajets absents du référentiel sont explicitement marqués `ADDED` :
la spécification GTFS-RT les définit comme des trajets ajoutés hors horaire
planifié. Les compter comme des échecs de jointure ramènerait le taux à 90 %
et donnerait une image fausse de la qualité du flux. Ils sont donc exclus du
calcul et traités à part : n'ayant pas d'horaire théorique, ils ne peuvent pas
faire l'objet d'un calcul de retard.

## Journal de bord

Les problèmes rencontrés sur les données sont documentés ici plutôt que masqués :
ils font partie du travail.

### 2026-09-10 → 09-11 — Le flux temps réel Soléa est vide : abandon de la source

Le projet visait initialement le réseau urbain Soléa (Mulhouse). Première
interrogation du flux trip updates à 22:47 : réponse HTTP 200, mais **13 octets**.
Décodage manuel du protobuf :

```
0a0b 0a03 312e 3018 baae 8cd5 06
└─ header (11 octets) : gtfs_realtime_version="1.0", timestamp=1789073210
   aucune entité
```

L'hypothèse d'un flux simplement vide en dehors des heures de service a été
testée par une collecte automatisée toutes les 2 minutes :

- **110 appels sur 20 heures**, dont 22 en heures de service (vendredi 13h–15h et 18h)
- **charge utile identique à l'octet près à chaque appel** : 13 octets, zéro entité
- groupe de contrôle : sur le même collecteur, le flux *service alerts* a varié
  entre 5, 6 et 7 alertes — la collecte fonctionnait bien

Conclusion : le flux trip updates de Soléa est publié mais ne contient aucune
donnée. Le calcul de ponctualité y est impossible. *Limite de la mesure :* le
poste étant en veille la nuit, la pointe du matin (7h–9h) n'a pas été
échantillonnée ; la constance de la charge utile jour et nuit rend toutefois
l'hypothèse d'un flux alimenté uniquement le matin peu crédible.

**Décision : bascule vers le flux SNCF**, vérifié avant toute reconstruction
(`src/probe_feeds.py`) :

| Flux | Entités | Prévisions d'arrêt | Dont avec retard | Taille |
|---|---|---|---|---|
| SNCF | 2 247 | 20 379 | 18 116 (89 %) | 1,58 Mo |
| Soléa | 0 | 0 | 0 | 13 o |

### 2026-09-11 — Les identifiants non appariés ne sont pas des troncatures

Premier constat : 90 % seulement des `trip_id` temps réel se retrouvaient dans
le GTFS. Les identifiants non appariés (`OCESN117153F`) ressemblant à des
versions tronquées des identifiants théoriques
(`OCESN105241F1187_F:NAV:FR:Line::…`), l'hypothèse d'une réconciliation par
préfixe a été testée — **et invalidée** : 3 cas résolus sur 223.

L'examen du champ `schedule_relationship` a donné la vraie explication : 220 des
223 trajets concernés sont marqués `ADDED`, donc absents du référentiel par
construction. Le taux de jointure pertinent est de 99,85 %, pas de 90 %.

### 2026-09-11 — Pas d'archivage des payloads bruts

Le flux SNCF pèse 1,58 Mo par appel, soit environ **0,8 Go par jour** de service
à raison d'un appel toutes les 2 minutes. L'archivage brut pratiqué sur Soléa
(13 octets par appel) n'est pas transposable.

Choix retenu : décodage à l'ingestion et stockage des seules observations utiles,
avec mise à jour de la dernière valeur connue par couple (trajet, arrêt) et par
date de service. Le volume passe de ~0,8 Go/jour à quelques dizaines de milliers
de lignes par jour, et la dernière valeur observée avant le passage effectif
constitue le meilleur estimateur du retard réalisé.

### 2026-09-11 — `stop_sequence` n'est pas alimenté par le flux SNCF

Sur les 20 284 prévisions d'arrêt d'un appel, le champ `stop_sequence` vaut `0`
partout. Les lignes restent uniques grâce au `stop_id`, mais **l'ordre des
arrêts le long d'un trajet ne peut pas être déduit du flux temps réel** : il
devra être repris de `stop_times.txt` du GTFS théorique. Le champ est conservé
dans la clé primaire au cas où la SNCF l'alimenterait par la suite.

### 2026-09-11 — Première mesure

Sur un instantané de 18 031 arrivées observées :

| Écart à l'horaire | Part |
|---|---|
| À l'heure ou en avance | 85,6 % |
| Retard ≤ 5 min | 8,3 % |
| 5 – 15 min | 3,3 % |
| 15 – 30 min | 1,6 % |
| > 30 min | 1,1 % |

**Ponctualité (arrivée à moins de 5 minutes) : 94,0 %.** Retard maximal
observé : 160 minutes. Chiffre provisoire — il porte sur un instantané et non
sur une journée complète consolidée.

### 2026-09-11 — Passages effectués et passages à venir : une hypothèse invalidée

Entre deux exports séparés d'une heure, la ponctualité globale est passée de
93,1 % à 91,2 %, la tranche 20h chutant de 97,3 % à 92,4 % alors que la tranche
18h ne bougeait presque pas. Hypothèse formulée : le flux temps réel étant
consulté avant l'heure de passage, les tranches encore à venir portaient des
prévisions optimistes, corrigées à mesure que les trains passaient réellement.

**Vérification — hypothèse fausse.** En comparant les deux populations
(`arrival_time` et `observed_at` étant deux horodatages absolus, la comparaison
ne demande aucun calcul de fuseau) :

| Population | Effectif | Ponctualité | Retard moyen |
|---|---|---|---|
| Passage déjà effectué | 12 901 | **91,9 %** | 2,2 min |
| Passage encore à venir | 8 286 | **90,0 %** | 2,7 min |

Les prévisions sont donc légèrement **plus pessimistes** que les réalisations,
et non l'inverse. La baisse du taux global s'explique plus simplement : l'export
suivant portait sur 3 000 passages supplémentaires, couvrant des trajets de
soirée plus tardifs.

La distinction reste néanmoins pertinente et a été conservée sous la forme d'un
indicateur `is_past` dans la table de faits : un taux qui mélange des passages
effectués et des passages à venir mesure en partie des prévisions et non des
résultats, et les deux populations diffèrent de façon mesurable.

### 2026-09-12 — Le référentiel est une fenêtre glissante : une correction qui a aggravé les choses

Le taux de jointure global est passé de 99,84 % à 99,16 %. Diagnostic posé : GTFS
théorique périmé — la copie locale datait du 11, la SNCF en avait publié une
nouvelle le 12. Correction appliquée : télécharger l'export récent et remplacer
l'ancien.

**Résultat, mesuré jour par jour :**

| Jour | GTFS du 11 | Après remplacement |
|---|---|---|
| vendredi 11 | 99,85 % | **94,45 %** |
| samedi 12 | 98,38 % | 98,33 % |

La correction a cassé vendredi sans réparer samedi. Deux erreurs :

- **L'export SNCF est une fenêtre glissante.** Chaque version démarre à sa date de
  publication (`feed_start_date` 20260912) et abandonne les trajets antérieurs.
  Remplacer la copie a donc orphelinisé les observations de la veille — et le
  remplacement atomique avait supprimé l'ancienne version.
- **Le contrôle qualité mesurait un taux agrégé.** À 96,28 %, il passait le seuil
  de 95 % : une journée à 94 % se cachait derrière une journée saine.

**Correction réelle.** Les versions précédentes étaient récupérables dans
l'historique de transport.data.gouv.fr (les 25 dernières y sont archivées).
Trois ont été récupérées et validées. Le référentiel est désormais **historisé** :

- `download_gtfs.py` archive chaque publication sous son horodatage dans
  `data/gtfs/versions/` et n'écrase jamais rien ;
- `load_gtfs.py` applique les versions dans l'ordre de `feed_version`. Un trajet
  présent dans une version récente en reprend entièrement la définition (sa
  séquence d'arrêts est remplacée, pas fusionnée) ; un trajet absent des versions
  récentes est conservé, puisque des observations y renvoient encore. Le
  chargement est incrémental ;
- `quality_checks.py` contrôle le taux de jointure **par jour de service**.

| Jour | Après historisation |
|---|---|
| vendredi 11 | **99,85 %** |
| samedi 12 | **99,92 %** |

Samedi dépasse même son niveau initial : les trajets qui manquaient figuraient
dans la version du 11, absente de l'export du 12. Seule l'accumulation des
versions pouvait les retrouver.

### 2026-09-12 — Les heures non collectées se faisaient passer pour des mesures

Le collecteur tourne sur un poste personnel et s'arrête avec lui. Sur ses 27
premières heures, il a été **à l'arrêt 62 % du temps** (vendredi 20:02 → 23:57,
samedi 08:07 → 20:26).

Ces trous ne se voyaient pas comme des trous. Le flux conserve les arrêts déjà
desservis d'un trajet tant que le train roule : à la reprise de la collecte, des
passages de l'après-midi restent visibles — mais seulement ceux des trains
**encore en circulation**, donc les plus longs. Vendredi, collecte démarrée à
18:50 :

| Heure | Passages effectués | Durée moyenne du trajet | Trajets > 3 h | Ponctualité apparente |
|---|---|---|---|---|
| 14h | 45 | 378 min | 100 % | 75,6 % |
| 16h | 303 | 233 min | 61 % | 88,8 % |
| 18h | 6 167 | 94 min | 7 % | 90,4 % |
| 19h | 5 545 | 94 min | 8 % | 92,8 % |

La « dégradation de fin d'après-midi » affichée dans les premières versions de
ce document était un **biais de survie** : elle décrivait les trains longs, pas
le réseau à 14h. Même mécanisme à 21h, après l'arrêt de la collecte.

**Correction :**

- `dim_collection_hour` enregistre, pour chaque heure de chaque jour, la part de
  l'heure pendant laquelle le collecteur tournait. Heure de Paris via `tzdata`,
  donc juste aux changements d'heure ; une heure manquée est un zéro explicite,
  pas une ligne absente.
- `fact_passage.is_collected` marque les passages survenus pendant une heure
  surveillée (au moins 50 %), jugée **jour par jour**, jamais en moyenne sur
  plusieurs jours. Un horaire GTFS au-delà de 24:00 est rattaché au jour civil
  suivant.
- **Tous les taux** — global, horaire, classements — portent désormais sur les
  passages *observés* : `is_past = 1` et `is_collected = 1`. Une seule
  définition, appliquée à l'identique par `report.py` et par la mesure Power BI
  `Taux ponctualite observe`.

Le biais se chiffre : **88,5 %** de ponctualité sur les passages vus après coup,
contre **92,6 %** sur les passages observés. Et il ne touchait pas que la courbe
horaire : `Francfort – Marseille`, en tête des pires lignes avec 36 passages,
n'en compte que 3 réellement observés — trop peu pour être classée.

Recouper les classements SQL et Power BI a révélé deux autres écarts, corrigés
côté SQL :

- la SNCF découpe certaines lignes en plusieurs `route_id`. Classer les fragments
  séparément faisait passer sous le seuil de 20 passages un fragment en retard,
  qui disparaissait du classement : `32. Ussel – Brive – Périgueux – Bordeaux`
  affichait 60,0 % sur son fragment principal pour 53,9 % sur la ligne entière ;
- certains noms existent en deux casses (`STRASBOURG – NIEDERBRONN…` et
  `Strasbourg – Niederbronn…`). Power BI regroupe le texte sans tenir compte de
  la casse, SQLite si.

Les dix premières lignes et les dix premières gares sont désormais identiques
dans les deux outils.

### 2026-09-13 — Sortir la collecte du poste personnel

La collecte sur poste personnel avait été à l'arrêt 62 % du temps. Elle tourne
désormais dans Supabase (offre gratuite) : une Edge Function appelée toutes les
5 minutes par `pg_cron`. Seule la collecte y est hébergée ; le référentiel GTFS,
le schéma en étoile et les contrôles restent sur le poste, qui rapatrie les
données (`src/sync_supabase.py`).

**Contraintes de l'offre gratuite, vérifiées avant de construire :** 2 s de CPU
par exécution, 256 Mo de mémoire, base de 500 Mo, mise en pause après 7 jours
sans activité (une collecte toutes les 5 minutes l'exclut). Première exécution
planifiée : **624 ms** au total, téléchargement et écriture compris.

**Le stockage imposait une rétention.** Mesure réelle : 526 octets par ligne,
l'identifiant de trajet SNCF (~100 caractères) étant stocké dans la table puis
dans l'index de clé primaire. Le GTFS théorique prévoit ~98 000 passages en
semaine : ~49 Mo/jour, soit 500 Mo atteints en une dizaine de jours — après quoi
Supabase restreint le projet et la collecte s'arrête. Supabase garde donc
7 jours ; l'archive est la base locale.

**Chaque exécution réécrivait toute la table.** La règle d'origine (« une lecture
plus récente remplace la ligne ») réécrit à chaque passage toutes les lignes
encore présentes dans le flux, presque toujours à l'identique. Sans conséquence
en SQLite, mais en Postgres chaque réécriture laisse une version morte et de
nouvelles entrées d'index. Nouvelle règle, identique pour les deux collecteurs :
une ligne n'est réécrite que si une valeur change — ou une seule fois quand la
lecture confirme que le train est passé, faute de quoi `is_past` resterait faux.

| Exécution | Règle | Arrêts reçus | Lignes écrites |
|---|---|---|---|
| 19:05 | tout réécrire | 7 532 | 7 532 (100 %) |
| 19:10 | changements seulement | 7 430 | **344 (4,6 %)** |

La règle a été testée sur huit cas (valeurs inchangées, lecture plus ancienne,
`NULL`, confirmation du passage) en SQLite puis en Postgres, avec des résultats
identiques. Sur données réelles : les **177** passages survenus entre les deux
exécutions ont tous leur passage confirmé. Le collecteur local, passé à la même
règle, écrit 394 lignes sur 7 540.

**Deux défauts trouvés au déploiement :**

- le lot d'observations, transmis en JSON avec `::jsonb`, était encodé deux fois
  par le pilote : Postgres recevait une chaîne au lieu d'un tableau
  (`cannot call jsonb_to_recordset on a non-array`). Corrigé par `::text::jsonb` ;
- le curseur de synchronisation prévu, `updated_at`, est identique pour les
  ~20 000 lignes d'une exécution, et une pagination par décalage sur une fenêtre
  de temps saute des lignes dès qu'une ligne change pendant la synchronisation.
  Remplacé, avant la première donnée, par `sync_seq`, numéro strictement
  croissant attribué à chaque écriture.

**Vérifié en production :** un appel sans jeton reçoit un 401 ; l'appel planifié
de 18:55, arrivé moins de 4 minutes après une exécution, a été refusé par la
limitation (ce qui prouve à la fois la planification et le jeton) ; 87,9 % des
lignes hébergées portent un retard, contre 88,9 % pour le collecteur local, et
les retards absents restent `NULL` au lieu de devenir des 0.

### 2026-09-15 — Le flux ne mesure pas à la minute : le seuil de 5 minutes tombe sur un palier

Cinq jours de collecte hébergée donnent 92,4 % de passages à moins de 5 minutes
de retard. Avant de publier ce chiffre, un contrôle de la distribution des
retards a montré qu'il ne dit pas ce qu'il paraît dire.

Ce que contient le flux, sur 224 408 passages observés dont le retard est
renseigné :

| Constat | Mesure |
|---|---|
| Retards négatifs (train en avance) | **0** |
| Retards non entiers en minutes | 0 |
| Valeurs de retard distinctes | 73 |
| Retards non nuls multiples de 5 minutes | **98,6 %** (36 763 sur 37 287) |
| Passages annoncés à exactement 5 minutes | **19 620**, soit 8,8 % des passages observés |

Le flux publie donc des paliers de 5 minutes, et jamais d'avance. Le champ
`arrival_time`, qui donne l'heure d'arrivée prévue, n'apporte aucune précision :
il vaut l'horaire théorique plus le retard annoncé dans 99,97 % des cas. La
minute réelle est irrécupérable.

Le seuil de ponctualité de 5 minutes tombe alors exactement sur un palier, à
l'endroit le plus défavorable :

| Convention | Taux |
|---|---|
| Retard annoncé sous 5 minutes (paliers 0 à 4) | **83,6 %** |
| Retard annoncé de 0 ou 5 minutes | **92,4 %** |

Ces 8,8 points ne mesurent aucun train : ils dépendent du sort d'un seul palier,
celui des 19 620 passages annoncés à 5 minutes, dont le flux ne dit pas s'ils
sont à 5 minutes exactement, entre 5 et 10, ou autour de 5. Les 354 passages
annoncés entre 1 et 4 minutes prouvent que le producteur sait publier une valeur
fine ; il ne le fait presque jamais.

Ce qui est retenu : ne plus publier un taux seul. Le chiffre mis en avant reste
celui des deux premiers paliers, 92,4 %, parce que l'ensemble de passages qu'il
désigne est sans ambiguïté — retard annoncé de 0 ou 5 minutes —, mais il est
toujours accompagné du taux strict, 83,6 %, qui le borne par le bas. La
ponctualité réelle à moins de 5 minutes est entre les deux, et ce flux ne permet
pas de resserrer l'encadrement.

Trois contrôles gardent l'hypothèse : aucun retard négatif, aucun retard non
entier, au plus 5 % de retards hors paliers de 5 minutes. Si SNCF se met à
publier à la minute, ils échouent — et c'est le signal qu'il faut réécrire cette
page, plutôt que continuer à citer un chiffre qui aura changé de sens.

### 2026-09-18 — Le référentiel avait des trous, et la porte qualité les a vus

Le contrôle qualité a refusé de publier : taux de jointure de **94,83 % le
17/09**, sous le seuil de 95 %, donc pipeline arrêté et export non regénéré.

La cause n'était pas dans les données temps réel mais dans le référentiel. Le
GTFS théorique n'est archivé que lorsque `refresh.py` tourne ; entre le 15 et le
18 septembre il n'a pas tourné, et les publications du 16 et du 17 n'ont jamais
été téléchargées. Comme chaque export SNCF est une fenêtre glissante qui démarre
à sa date de publication, les trajets propres à ces deux jours n'existaient plus
nulle part dans l'archive locale : impossible de leur trouver un horaire
théorique.

L'archive contenait 7 publications pour 9 jours, dont deux jours avec deux
éditions et trois jours sans aucune.

**Correction.** Le Point d'Accès National conserve les 25 dernières publications
de chaque ressource. `download_gtfs.py` interroge donc désormais l'historique du
jeu de données et télécharge toute publication postérieure à la plus ancienne
déjà archivée qui manque à l'appel — l'archive comble ses propres trous. Cinq
publications ont été récupérées d'un coup.

| Jour | Avant | Après |
|---|---|---|
| 15/09 | 97,95 % | 99,79 % |
| 17/09 | **94,83 %** | **97,01 %** |

**Ce que ça change dans l'exploitation.** La règle « lancer `refresh.py` au moins
une fois par semaine » suffisait pour la collecte hébergée, qui garde 7 jours ;
elle ne suffisait pas pour le référentiel, qui n'était archivé qu'au moment de
l'exécution. Elle suffit maintenant, avec une marge de 25 publications, soit
environ 25 jours. Au-delà, un trou devient définitif.

Deux enseignements qui valent au-delà de ce projet : une donnée de référence
qu'on ne peut pas re-télécharger doit être archivée dès qu'elle paraît, et un
contrôle qui bloque la publication vaut mieux qu'un tableau de bord qui affiche
un chiffre faux sans le dire. Ici, le rapport a continué d'afficher les données
de la veille au lieu d'un résultat douteux.

### 2026-10-03 — Cinq jours perdus : l'archivage ne pouvait pas dépendre de moi

La règle « lancer `refresh.py` au moins une fois par semaine » a tenu deux
semaines. Du 20 septembre au 3 octobre, elle n'a pas été appliquée, et Supabase
a fait ce qu'on lui a demandé : supprimer les observations de plus de 7 jours.
**Les journées du 21 au 25 septembre n'existent plus nulle part.** Cinq jours de
service, environ 390 000 passages, irrécupérables.

Le défaut n'est pas l'oubli, il est dans la conception : la seule copie durable
dépendait d'une action humaine répétée, alors que la source, elle, est
automatique. Une rétention courte et un archivage manuel ne vont pas ensemble.

**Correction.** `src/archive.py` regroupe les deux seules étapes dont le retard
coûte des données — rapatrier la collecte hébergée, archiver la publication GTFS
du jour — et une tâche planifiée l'exécute toutes les 4 heures. Le reste du
pipeline garde son rythme manuel : il se reconstruit à l'identique depuis
l'archive, quand on veut.

La répétition toutes les 4 heures remplace l'option « rattraper une exécution
manquée », que Windows ne laisse pas activer sans droits d'administrateur :
six créneaux par jour suffisent pour qu'un portable allumé une fois dans la
journée soit à jour.

*Correction du 04/10 : ce n'était pas Windows qui refusait, mais l'environnement
restreint depuis lequel la tâche avait été créée. Le rattrapage et le démarrage
sur batterie sont maintenant activés ; la répétition reste, elle ne coûte rien.
Une hypothèse fausse de plus, consignée comme les autres.*

**Un trou se voit désormais.** `sync_supabase.py` compare les jours présents dans
l'archive et nomme ceux qui manquent :

```
5 day(s) missing from the archive, unrecoverable: 2026-09-21, 2026-09-22,
2026-09-23, 2026-09-24, 2026-09-25
```

Un trou silencieux est pire qu'un trou bruyant : tous les taux calculés sur la
période continuent de fonctionner, sur un échantillon que personne ne songe à
mettre en doute.

### 2026-10-03 — Le week-end est plus ponctuel, et ce n'est pas le hasard

Quinze journées sont désormais couvertes 24 h sur 24 : dix de semaine, cinq de
week-end. Assez pour trancher la question laissée ouverte depuis le 12/09, où
chaque tranche horaire ne reposait que sur un jour.

| | Jours | Moyenne | Étendue |
|---|---|---|---|
| Semaine | 10 | 91,98 % | 90,2 – 93,2 |
| Week-end | 5 | **93,21 %** | 92,2 – 94,0 |

L'écart est de **1,23 point**. Les deux étendues se chevauchent — le samedi
26/09 (92,2 %) fait moins bien que le vendredi 18/09 (93,2 %) — donc la moyenne
seule ne prouve rien. Il fallait un test.

**Test de permutation exact.** Si le jour de la semaine n'avait aucun effet,
répartir ces quinze taux au hasard en groupes de dix et cinq donnerait aussi
souvent un écart de cette taille. Il y a exactement 3 003 façons de choisir
cinq jours parmi quinze : elles ont toutes été énumérées, sans échantillonnage
ni approximation. **25 d'entre elles atteignent l'écart observé, soit p = 0,008.**

Sur la borne stricte — retard annoncé nul — l'écart monte à **3,76 points**
(82,70 % contre 86,46 %) et **p = 0,0003** : une seule permutation sur 3 003.

**Deux choses à en retenir.**

La première est mécanique : le week-end fait circuler 57 % des trains d'un jour
de semaine. Moins de circulations, donc plus de marge entre elles, et un retard
qui se propage moins loin. L'écart se retrouve dans les cinq tranches horaires,
y compris la nuit, ce qui n'était pas le cas sur un seul week-end.

La seconde porte sur la mesure elle-même : **la convention de seuil comprime
l'écart**. Le taux publié ne montre que 1,2 point de différence là où la borne
stricte en montre 3,8. Le palier de 5 minutes du flux absorbe une partie du
phénomène — les trains de semaine sont plus nombreux dans ce palier, et le seuil
« au plus 5 minutes » les compte ponctuels. Un indicateur trop généreux ne se
contente pas de flatter un chiffre : il efface des différences réelles.

### 2026-10-04 — Le plafond Supabase venait des identifiants, pas des données

La base hébergée atteignait 409 Mo sur les 500 de l'offre gratuite, pour une
semaine de collecte. Avant de réduire quoi que ce soit, la question était : où
partent ces octets ?

| Objet | Taille |
|---|---|
| Index de clé primaire | **192 Mo** |
| Table des observations | 161 Mo |
| Index de synchronisation | 35 Mo |

L'index pesait plus lourd que les données. Chaque ligne y recopiait son
identifiant de trajet — une centaine de caractères chez SNCF — et son
identifiant d'arrêt, une trentaine. Un B-tree de longues clés texte se
fragmente vite : environ 300 octets d'index par ligne, pour retrouver un passage.

**Correction.** Les identifiants partent dans trois dictionnaires qui stockent
chaque chaîne une seule fois ; la table d'observations ne garde que des
références entières de 4 octets, et sa clé primaire tombe à 16 octets. Les
colonnes sont rangées des plus larges aux plus étroites pour ne perdre aucun
octet d'alignement. `public.observation` subsiste sous forme de vue, avec
exactement les colonnes d'avant : la synchronisation locale n'a pas changé
d'une ligne de code.

| | Avant | Après |
|---|---|---|
| Base entière | 409 Mo | **136 Mo** |
| Par observation | ~616 octets | **~174 octets** |
| Rétention tenable sur 500 Mo | ~7 jours | plus de 4 semaines |

**La migration elle-même avait un piège.** Copier les 646 276 lignes vers la
nouvelle table pendant que l'ancienne existe encore aurait culminé vers 520 Mo,
au-dessus du plafond — et Postgres ne libère l'espace d'un objet supprimé qu'à la
fin de la transaction. Les deux index de l'ancienne table ont donc été supprimés
dans une première migration, à part : 227 Mo rendus avant la copie, un pic réel
autour de 300 Mo. La collecte était suspendue pendant l'opération, et l'archive
locale vérifiée au même curseur que Supabase avant la première instruction.

Un détail de conception, aussi : insérer les nouveaux identifiants avec
`ON CONFLICT DO NOTHING` aurait consommé une valeur d'identité pour chaque
identifiant déjà connu — environ 10 000 par exécution, toutes les cinq minutes,
de quoi épuiser un entier de 4 octets en moins de deux ans. Seuls les
identifiants absents du dictionnaire sont insérés.

Vérifié après coup : 646 276 lignes et le même `sync_seq` maximal qu'avant, la
lecture publique sert les mêmes colonnes, l'écriture reste refusée (401), la
fonction d'ingestion n'est pas exposée par l'API (404), et les audits de
sécurité et de performance de Supabase ne remontent rien. La rétention passe de
7 à 14 jours ; elle pourrait aller plus loin, mais l'archive locale est
alimentée toutes les quatre heures et n'en a plus besoin.

## Modèle de données

Schéma en étoile construit par `src/build_marts.py` dans `data/punctuality.db` :

```
     dim_route                 dim_station              dim_collection_hour
 (route_id, nom, mode,     (stop_id, nom, lat, lon,   (service_date, hour,
   agency_name)              parent_station)           runs, coverage_pct)
          \                        /                            :
           \                      /                   calcule is_collected
            +---- fact_passage --+ . . . . . . . . . . . . . . . :
   service_date, trip_id, route_id, stop_id, stop_sequence,
   scheduled_arrival, scheduled_hour, arrival_delay_s, arrival_time,
   schedule_relationship, is_punctual, is_past, is_collected, observed_at
```

Une ligne de `fact_passage` = un passage en gare, avec le retard relevé et
l'horaire théorique auquel il est comparé.

**Choix de méthode, qui conditionnent la lecture des chiffres :**

- Les trajets `ADDED` n'ont pas d'horaire théorique par construction : ils sont
  exclus de la table de faits.
- Les trajets supprimés sont **conservés** dans la table de faits mais **exclus**
  des indicateurs (`is_punctual` à `NULL`) : un train supprimé n'est pas un train
  en retard, et l'intégrer à une moyenne embellit le résultat sans le dire.
- Le seuil de ponctualité est de 5 minutes à l'arrivée, appliqué **par passage en
  gare** et non par trajet.
- Les taux portent sur les **passages observés** : déjà effectués (`is_past`) et
  survenus pendant une heure où le collecteur tournait (`is_collected`). Les
  passages vus après coup forment un échantillon biaisé vers les trains longs
  (voir journal du 12/09).
- Lignes et gares sont regroupées par nom, sans tenir compte de la casse, et non
  par identifiant.
- `stop_sequence` provient du GTFS théorique, le flux temps réel ne l'alimentant
  pas (voir journal).

## Résultats

Mesure du 03/10/2026 sur 18 jours de service, du 11/09 au 03/10 :
**1 076 281 passages observés**, c'est-à-dire effectués pendant une heure où le
collecteur tournait. Cinq journées manquent, du 21 au 25 septembre, perdues
faute d'archivage automatique — le journal du 03/10 raconte comment.

**Le flux publie les retards par paliers de 5 minutes**, et le seuil de
ponctualité tombe exactement sur un palier : le taux dépend donc autant d'une
convention que des trains (journal du 15/09). Les deux bornes sont publiées
ensemble.

| Indicateur | Valeur |
|---|---|
| Ponctualité, retard annoncé de 0 ou 5 min | **92,3 %** |
| Ponctualité, retard annoncé sous 5 min | **83,7 %** |
| Retard médian | 0 min |
| Retard moyen | 2,2 min |
| 9e décile (p90) | 5 min |
| p99 | **40 min** |
| Retard maximal | 1 430 min |
| Focus Grand Est | 94,1 % sur 204 433 passages, retard moyen 1,5 min |

Populations écartées du taux, pour comparaison :

| Population | Ponctualité | Passages |
|---|---|---|
| Tous passages mesurables confondus | 92,2 % | 1 106 149 |
| Vus après coup (heure non surveillée) | **87,9 %** | 5 030 |
| Encore à venir (prévision) | 89,6 % | 24 838 |

La moyenne seule induit en erreur : à 2,2 minutes elle suggère un réseau
régulier, alors que la médiane est nulle — la majorité des trains n'ont aucun
retard annoncé — et que le dernier centile atteint 40 minutes. Ce sont deux
descriptions exactes des mêmes données, et seule la seconde décrit ce que vit un
voyageur en retard. Les trois indicateurs sont donc publiés ensemble.

### Semaine et week-end

Sur les quinze journées couvertes 24 h sur 24 — dix de semaine, cinq de
week-end — le week-end est plus ponctuel de **1,23 point** : 93,21 % contre
91,98 % en moyenne par jour. Un test de permutation exact, qui énumère les
3 003 répartitions possibles de ces quinze jours, donne **p = 0,008** ; sur la
borne stricte, l'écart atteint 3,76 points et **p = 0,0003** (journal du 03/10).

| Tranche | Semaine | Week-end | Écart |
|---|---|---|---|
| 05-08h | 93,7 % | 95,4 % | **+1,7** |
| 09-12h | 91,4 % | 93,9 % | **+2,5** |
| 13-16h | 92,8 % | 93,8 % | +1,0 |
| 17-20h | 91,3 % | 92,4 % | +1,1 |
| 21h-04h | 87,2 % | 88,5 % | +1,3 |

Deux lectures dans un seul tableau. **Horizontalement**, le week-end gagne dans
les cinq tranches, sans exception. **Verticalement**, la ponctualité se dégrade
au fil de la journée des deux côtés : de 93,7 % à 87,2 % en semaine, de 95,4 % à
88,5 % le week-end. Les retards s'accumulent du matin au soir, et le week-end
part simplement de plus haut — il fait circuler 57 % des trains d'un jour de
semaine.

**Lignes les moins ponctuelles** (passages observés, au moins 20) :
`Paris - Francfort Route Sud` (45,3 % sur 364), `Bordeaux - Marseille` (50,2 %
sur 1 807), `Paris - Stuttgart Munich` (51,6 % sur 539), `Lyon - LR` (60,7 % sur
737), `Selestat - St Die Des Vosges` (63,7 % sur 4 235, un service routier). Les
liaisons longues et transfrontalières dominent : plus un trajet est long, plus
il a d'occasions d'accumuler du retard, et plus il traverse de réseaux.

**Gares les moins ponctuelles** : `Stuttgart Hbf` (19,8 % sur 81),
`Ulm Hbf` (37,5 %), `Francfort sur le Main` (38,7 % sur 119), `Karlsruhe Hbf`
(40,1 % sur 274) — toutes allemandes, en bout de liaisons transfrontalières,
après plusieurs centaines de kilomètres. Viennent ensuite les arrêts de
`Lièpvre` et `Ste-Marie-aux-Mines`, desservis par le même autocar de
substitution, qu'un classement de gares fait apparaître autant de fois qu'il
compte d'arrêts.

**À Mulhouse**, la gare centrale est à 91,3 % sur 2 291 passages, et à 78,4 %
sur la borne stricte. Les arrêts urbains du tram-train dépassent 99 %, mais la
comparaison n'a pas de sens : sur un service où les rames passent toutes les dix
minutes, cinq minutes de retard représentent la moitié d'un intervalle, alors
que le flux ne sait pas descendre sous ce palier. Le mode tram affiche 99,0 %
d'ensemble, l'autocar 87,7 %, le train 92,3 %.

> **Limites.** 18 jours, dont 15 couverts 24 h sur 24 et un trou de cinq jours
> assumé. La comparaison semaine / week-end repose sur 10 et 5 journées : le test
> exact dit que l'écart n'est pas un accident d'échantillonnage, il ne dit pas
> qu'il vaudra la même chose en décembre. Les classements portent sur 20 à 4 000
> passages selon les lignes ; ils désignent des pistes à instruire, pas des
> palmarès. Les gares allemandes apparaissent par les liaisons transfrontalières
> et ne disent rien du réseau allemand. Enfin, aucun taux publié ici n'est plus
> fin que le palier de 5 minutes du flux.

## Archivage automatique

Deux sources oublient. Supabase supprime les observations de plus de 14 jours
pour tenir dans l'offre gratuite, et chaque publication GTFS abandonne les
trajets antérieurs à sa date — le Point d'Accès National n'en garde que 25.
Tout le reste du pipeline peut être rejoué à n'importe quel moment ; ces deux
étapes-là, non.

`src/archive.py` les exécute, et une tâche planifiée Windows l'appelle toutes
les 4 heures :

```powershell
schtasks /Query /TN SncfArchiveSync          # état et prochaine exécution
schtasks /Run   /TN SncfArchiveSync          # forcer une exécution
schtasks /Delete /TN SncfArchiveSync /F      # retirer l'automatisation
```

Le journal est dans `data/archive.log`. Une exécution sans rien de neuf prend
7 secondes ; la tâche est donc répétée plutôt que programmée une fois par jour,
ce qui la rend insensible à une machine éteinte au mauvais moment.

La tâche tourne aussi sur batterie et rattrape une exécution manquée : un
portable éteint à l'heure prévue se met à jour dès qu'il se rallume.

`src/refresh.py` reste le point d'entrée pour tout reconstruire — référentiel,
schéma en étoile, contrôles, export Power BI — mais plus rien n'est perdu si on
tarde à le lancer.

## Utilisation

```bash
python src/probe_feeds.py       # vérifier qu'un flux contient réellement des données
python src/reconcile_check.py   # mesurer le taux de jointure GTFS-RT ↔ GTFS
python src/refresh.py           # tout reconstruire : synchro Supabase, GTFS, marts, contrôles, export
python src/archive.py           # archivage quotidien : collecte hébergée + référentiel GTFS
python src/sync_supabase.py     # rapatrier la collecte hébergée dans data/punctuality.db
python src/ingest_sncf.py       # collecte locale ponctuelle (secours)
python src/download_gtfs.py     # archiver la dernière publication du GTFS théorique
python src/load_gtfs.py         # appliquer les versions GTFS archivées (incrémental)
python src/build_marts.py       # construire le schéma en étoile
python src/quality_checks.py    # vérifier les invariants du modèle
python src/report.py            # indicateurs de ponctualité en console
python src/export_powerbi.py    # export CSV pour Power BI
python src/fetch_rt.py          # collecte Soléa (conservée à titre de trace)
```

Pour tout remettre à jour, un seul point d'entrée :

```bash
python src/refresh.py
```

Il enchaîne la reconstruction du schéma, le contrôle des invariants puis
l'export, et **s'arrête à la première erreur**. C'est le point important : si le
contrôle qualité échoue, l'export n'est pas régénéré, et Power BI continue
d'afficher les données précédentes plutôt qu'un résultat douteux. Exporter après
un contrôle en échec produirait un rapport d'apparence normale et faux.

`quality_checks.py` sort en code 1 si un invariant est rompu ; un rapport ne
doit pas être construit sur un modèle qui échoue. Outre les contrôles
structurels (unicité des clés, absence de lignes orphelines, valeurs
impossibles), il teste **les choix de méthode** : qu'aucun train supprimé
n'entre dans le calcul du taux, et qu'aucun trajet `ADDED` n'entre dans la table
de faits. Ces deux règles sont faciles à défaire par inadvertance en modifiant
une requête, et les défaire gonfle le taux de ponctualité sans que rien ne
paraisse anormal.

La construction du rapport Power BI est décrite dans
[`docs/powerbi.md`](docs/powerbi.md) : import, relations, mesures DAX et visuels.

`refresh.py` récupère et applique d'elle-même toute nouvelle publication du GTFS
théorique.

### Collecte hébergée (Supabase)

La collecte ne dépend plus du poste de travail : elle tourne dans un projet
Supabase (offre gratuite, région Paris), dont tout le code est versionné dans
`supabase/`.

```
pg_cron, toutes les 5 min
  └─ pg_net ── POST + jeton ──▶ Edge Function ingest-sncf ── fetch ──▶ flux GTFS-RT SNCF
                                       │
                                       └─ upsert ──▶ Postgres : observation, ingest_run
                                                          │
            src/sync_supabase.py (poste) ◀── lecture seule, curseur sync_seq
```

- **Authentification** : un jeton généré par la base elle-même et conservé dans
  Supabase Vault. `pg_cron` l'envoie, la fonction le compare à la copie du
  Vault ; il n'apparaît ni dans le dépôt ni dans aucun échange. Sans jeton, la
  fonction répond 401.
- **Limitation** : une exécution lancée moins de 4 minutes après la précédente
  est refusée, quel que soit l'appelant.
- **Écritures sobres** : une ligne n'est réécrite que si une valeur change, ou
  une seule fois quand le passage est confirmé (voir journal du 13/09).
- **Stockage compact** : les identifiants SNCF sont stockés une seule fois,
  dans des dictionnaires, et les observations n'en gardent que des références
  entières — environ 174 octets par ligne au lieu de 616 (journal du 04/10).
  `public.observation` est une vue qui restitue les identifiants en clair.
- **Rétention de 14 jours** dans Supabase, couverte par l'archivage automatique
  (voir plus bas) : environ 240 Mo pour 500 disponibles. L'archive complète est `data/punctuality.db`,
  alimentée toutes les 4 heures par `src/archive.py` (journal du 03/10, après
  cinq jours perdus faute de l'avoir fait à la main).
- **Lecture** : l'API n'autorise que la lecture (RLS). Renseigner `.env` d'après
  `.env.example` avec la clé publishable du projet.

Pour reproduire sur un autre projet : appliquer `supabase/migrations/` dans
l'ordre, créer le secret `project_url` (en-tête de la migration de
planification), déployer `supabase/functions/ingest-sncf`, puis remplir `.env`.

### Collecte locale (secours)

La tâche planifiée Windows `SncfRTIngest` exécute `src/ingest_sncf.py` toutes
les 5 minutes, avec la même règle d'écriture que la fonction hébergée : les deux
sources fusionnent sans conflit. Elle n'est plus nécessaire et peut être
désactivée ; sortie dans `data/ingest.log`.

```powershell
Get-ScheduledTaskInfo -TaskName "SncfRTIngest"
Disable-ScheduledTask -TaskName "SncfRTIngest"
```

## Suite

- [x] Vérifier qu'un flux temps réel exploitable existe
- [x] Mesurer le taux de jointure temps réel ↔ théorique
- [x] Ingestion SNCF : décodage et stockage des observations
- [x] Référentiel GTFS historisé (fenêtre glissante SNCF)
- [x] Modélisation en étoile (fait : passage vs horaire théorique ; dims : ligne, gare, heure collectée)
- [x] Indicateurs de ponctualité par ligne, gare et tranche horaire, sur les seuls passages observés
- [x] Contrôles qualité automatisés (structure, choix de méthode, jointure par jour)
- [x] Indicateurs de dispersion (médiane, p90, p99) en complément de la moyenne
- [x] Rapport Power BI (courbe horaire, classements des lignes et des gares)
- [x] Sortir la collecte du poste personnel (Supabase : Edge Function, `pg_cron`, Postgres)
- [x] Identifiants entiers dans la base hébergée : 409 Mo ramenés à 136 Mo, rétention portée à 14 jours
- [x] Séparer semaine et week-end dans la courbe horaire (15 journées complètes, test de permutation exact)
- [ ] Carte des gares (visuel désactivé par défaut dans Power BI, voir `docs/powerbi.md`)
