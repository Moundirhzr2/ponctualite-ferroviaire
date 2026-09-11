# Ponctualité ferroviaire — pipeline temps réel GTFS-RT

Pipeline de collecte et d'analyse de la ponctualité du réseau ferroviaire
français (TGV, Intercités, TER), à partir des données ouvertes du Point d'Accès
National [transport.data.gouv.fr](https://transport.data.gouv.fr).

**Objectif :** mesurer la ponctualité réelle en confrontant les passages temps
réel (GTFS-RT) aux horaires théoriques (GTFS), et restituer les résultats par
ligne, par gare et par tranche horaire — avec un focus Grand Est
(Mulhouse – Strasbourg – Bâle).

## Sources

| Source | Format | Contenu |
|---|---|---|
| [Horaires théoriques SNCF](https://transport.data.gouv.fr/datasets/horaires-sncf) | GTFS | 39 040 trajets, 8 720 arrêts — validité 10/09/2026 → 28/02/2027 |
| [SNCF GTFS-RT Trip Updates](https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates) | GTFS-RT | ~2 240 trajets et ~20 300 prévisions d'arrêt par appel |

## Qualité des données — ce qui a été mesuré

Taux de jointure entre le flux temps réel et le référentiel théorique
(`src/reconcile_check.py`, mesure du 11/09/2026) :

| Jointure | Taux |
|---|---|
| `trip_id` (hors trajets non planifiés) | **99,85 %** (2 015 / 2 018) |
| `stop_id` | **100 %** (20 279 / 20 279) |

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

## Modèle de données

Schéma en étoile construit par `src/build_marts.py` dans `data/punctuality.db` :

```
                dim_route                     dim_station
          (route_id, nom, mode,          (stop_id, nom, lat, lon,
            agency_name)                    parent_station)
                    \                              /
                     \                            /
                      +------ fact_passage ------+
        service_date, trip_id, route_id, stop_id, stop_sequence,
        scheduled_arrival, scheduled_hour, arrival_delay_s,
        schedule_relationship, is_punctual, observed_at
```

Une ligne de `fact_passage` = un passage observé en gare, avec le retard mesuré
et l'horaire théorique auquel il est comparé.

**Choix de méthode, qui conditionnent la lecture des chiffres :**

- Les trajets `ADDED` n'ont pas d'horaire théorique par construction : ils sont
  exclus de la table de faits (20 166 passages retenus sur 20 877 observations).
- Les trajets supprimés sont **conservés** dans la table de faits mais **exclus**
  des indicateurs (`is_punctual` à `NULL`) : un train supprimé n'est pas un train
  en retard, et l'intégrer à une moyenne embellit le résultat sans le dire.
- Le seuil de ponctualité est de 5 minutes à l'arrivée, appliqué **par passage en
  gare** et non par trajet.
- `stop_sequence` provient du GTFS théorique, le flux temps réel ne l'alimentant
  pas (voir journal).

## Résultats

Mesure du 11/09/2026 sur 17 881 passages exploitables :

| Indicateur | Valeur |
|---|---|
| Ponctualité globale (< 5 min) | **91,2 %** |
| — dont passages effectués | 91,9 % sur 12 901 |
| — dont passages à venir | 90,0 % sur 8 286 |
| Retard médian | 0 min |
| Retard moyen | 2,4 min |
| 9e décile (p90) | 5 min |
| p99 | **40 min** |
| Retard maximal | 160 min |
| Focus Grand Est | 91,6 % sur 2 467 passages |
| Gare de Mulhouse | 92,7 % sur 41 passages |

La moyenne seule induit en erreur : à 1,9 minute elle suggère un réseau
régulier, alors que la médiane est nulle — la majorité des trains sont à
l'heure — et que le dernier centile dépasse 40 minutes. Ce sont deux
descriptions exactes du même jeu de données, et seule la seconde décrit ce que
vit un voyageur en retard. Les trois indicateurs sont donc publiés ensemble.

Ponctualité par tranche horaire — la pointe de fin d'après-midi se dégrade
nettement, le réseau se rétablit en soirée :

| Heure | 16h | 17h | 18h | 19h | 20h | 21h |
|---|---|---|---|---|---|---|
| Taux | 88,8 % | 91,7 % | 91,0 % | 95,4 % | 97,3 % | 95,2 % |

Lignes les moins ponctuelles sur la période, plusieurs en Grand Est :
`Ambérieu – Mâcon` (28,9 %), `Lille Flandres – Amiens` (33,3 %),
`Strasbourg – Wissembourg` (42,5 %), `Strasbourg – Saint-Dié – Épinal` (53,3 %).

> **Limite importante :** ces chiffres portent sur une collecte démarrée en fin
> de journée. Les tranches horaires antérieures à 17h ne comptent que quelques
> dizaines de passages — des trajets déjà accomplis encore présents dans le flux
> — contre plus de 6 000 pour 18h et 19h. La comparaison entre tranches horaires
> ne sera valide qu'après plusieurs journées complètes de collecte.

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

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Utilisation

```bash
python src/probe_feeds.py       # vérifier qu'un flux contient réellement des données
python src/reconcile_check.py   # mesurer le taux de jointure GTFS-RT ↔ GTFS
python src/ingest_sncf.py       # ingestion SNCF vers data/punctuality.db
python src/load_gtfs.py         # charger le GTFS théorique (tables de référence)
python src/refresh.py           # tout reconstruire : marts, contrôles, export
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

`load_gtfs.py` n'est à relancer qu'au rafraîchissement de l'archive GTFS
théorique (validité affichée sur transport.data.gouv.fr).

### Collecte planifiée (Windows)

L'ingestion tourne toutes les 5 minutes via la tâche planifiée `SncfRTIngest`,
exécutée par `pythonw.exe` (sans fenêtre). Sortie dans `data/ingest.log`.

```powershell
Get-ScheduledTaskInfo -TaskName "SncfRTIngest"
Unregister-ScheduledTask -TaskName "SncfRTIngest" -Confirm:$false
```

Le flux pesant 1,5 Mo par appel, la cadence de 5 minutes représente environ
18 Mo/heure de téléchargement. Une cadence plus fine n'apporterait qu'une
précision marginale sur le dernier relevé avant passage.

## Suite

- [x] Vérifier qu'un flux temps réel exploitable existe
- [x] Mesurer le taux de jointure temps réel ↔ théorique
- [x] Ingestion SNCF : décodage et stockage des observations
- [x] Chargement du GTFS théorique en tables de référence
- [x] Modélisation en étoile (fait : passage observé vs théorique ; dims : ligne, gare)
- [x] Indicateurs de ponctualité par ligne / gare / tranche horaire
- [x] Export CSV pour Power BI
- [x] Contrôles qualité automatisés (structure + choix de méthode)
- [x] Indicateurs de dispersion (médiane, p90, p99) en complément de la moyenne
- [x] Procédure de construction du rapport Power BI (`docs/powerbi.md`)
- [ ] Rapport Power BI (carte des gares, courbe horaire, classement des lignes)
- [ ] Accumuler plusieurs journées complètes pour valider les comparaisons horaires
- [ ] Migration SQLite → PostgreSQL/Supabase
