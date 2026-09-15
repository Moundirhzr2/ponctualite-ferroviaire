# Construction du rapport Power BI

Deux chemins. Le projet PBIP (`powerbi/Ponctualite.pbip`) livre le modèle déjà
câblé — c'est la voie rapide. La construction manuelle reste documentée plus
bas pour comprendre ce que le projet contient.

Le rapport se construit sur les trois fichiers CSV produits par
`src/export_powerbi.py` dans `data/export/`. Power BI ne lit SQLite qu'à travers
un pilote ODBC à installer et configurer séparément ; le CSV évite cette
dépendance et le rafraîchissement se résume à relancer le script.

## Voie rapide : ouvrir le projet PBIP

```bash
python src/export_powerbi.py     # les CSV doivent exister
start powerbi\Ponctualite.pbip
```

Le format PBIP est textuel (TMDL + JSON), donc versionnable dans Git, à la
différence du `.pbix` binaire. Le projet arrive avec les trois tables, les deux
relations, les cinq mesures DAX et les catégories Latitude/Longitude déjà
définies.

**Au premier lancement :** un PBIP ne stocke que des définitions, jamais de
données. Les bandeaux « Certaines tables ont des données incomplètes » et
« Une ou plusieurs relations ont été modifiées » sont normaux. Cliquer
**Actualiser** dans le ruban : les trois tables se chargent et les bandeaux
disparaissent.

**Le rapport contient trois pages :**

| Page | Ce qu'elle montre |
|---|---|
| Vue d'ensemble | Les deux bornes du taux, la courbe horaire, la distribution des retards annoncés, la vérification jour par jour |
| Lignes et gares | Les dix pires lignes et gares, un nuage volume × taux, le détail trié |
| Méthode et qualité | L'écart entre conventions, les trois populations, la couverture horaire, les corrections appliquées |

Les segments *Jour de service* et *Mode* sont synchronisés entre les pages
(`syncGroup` dans le JSON), et le filtrage croisé reste actif partout : cliquer
une barre filtre les autres visuels de la page.

Tous les taux passent par la même mesure de population, et les classements sont
identiques, position par position, à ceux de `src/report.py`.

**Le classement est une mesure, pas un filtre.** `Taux ligne (10 pires)` calcule
un rang avec `RANKX` sur les lignes sélectionnées et renvoie `BLANK()` au-delà du
dixième rang : le visuel n'affiche donc que dix barres, et le classement se
recalcule quand un segment change. Un filtre *Top N* figé aurait donné le même
visuel au repos, mais faux dès le premier filtre.

**Le rapport est écrit en JSON, pas à la souris.** Le format PBIR range chaque
visuel dans son propre fichier ; la mise en page, les titres et les couleurs sont
donc générés par `powerbi/build_report.py`, relisibles en revue de code et
reproductibles après un changement de mesure — relancer le script avec Power BI
fermé réécrit les trois pages à l'identique. Le thème (`StaticResources/RegisteredResources/PonctualiteTheme.json`)
porte la palette, les polices et les cadres, ce qui évite de répéter la mise en
forme sur chaque visuel.

**Format :** le rapport a été converti au format PBIR à l'enregistrement. Les
visuels sont désormais un fichier JSON chacun sous
`Ponctualite.Report/definition/pages/`, ce qui les rend lisibles en revue de
code — un `.pbix` ne l'aurait pas permis. La conversion est irréversible.

### Deux bornes plutôt qu'un taux

Le flux SNCF n'annonce les retards que par paliers de 5 minutes, soit exactement
le seuil de ponctualité (journal du 15/09). Le rapport publie donc deux mesures
côte à côte, jamais l'une sans l'autre :

| Mesure | Définition | Valeur au 15/09 |
|---|---|---|
| `Taux ponctualite observe` | retard annoncé de 0 ou 5 minutes | 92,4 % |
| `Taux ponctualite strict` | retard annoncé sous 5 minutes | 83,6 % |

`Ecart de convention (pts)` affiche la différence, 8,8 points, qui ne mesure
aucun train : seulement le sort du palier de 5 minutes.

### Une seule mesure porte toutes les règles de population

```dax
Taux ponctualite observe =
VAR Observed =
    CALCULATE([Passages mesurables], fact_passage[is_collected] = 1, fact_passage[is_past] = 1)
RETURN
    IF(Observed >= 20,
       CALCULATE([Taux de ponctualite], fact_passage[is_collected] = 1, fact_passage[is_past] = 1))
```

Elle réunit trois règles qui, dispersées dans des filtres de visuels, se
perdraient à la première modification :

- **passages effectués** (`is_past`) : un passage encore à venir porte une
  prévision, pas un résultat ;
- **heures surveillées** (`is_collected`) : un passage vu seulement après coup
  appartient à un trajet encore en circulation à la reprise de la collecte, donc
  à un train long — échantillon biaisé qui affichait 88,5 % contre 92,6 % ;
- **au moins 20 passages** : en dessous, le taux ne veut rien dire et la mesure
  renvoie `BLANK()`, si bien que l'élément disparaît de lui-même du visuel.

L'intention est inscrite dans le modèle — description de la mesure comprise — et
réutilisable partout, au lieu d'être enfouie dans le volet de filtres d'un seul
visuel.

**Regroupement par nom.** Les axes utilisent les noms de ligne et de gare, que
Power BI regroupe sans tenir compte de la casse. C'est le bon choix : la SNCF
découpe certaines lignes en plusieurs `route_id` et en écrit certaines en deux
casses. `report.py` a été aligné sur ce comportement.

### Les visuels de carte sont désactivés par défaut

Un visuel *Carte* affiche « Les visuels de carte et de carte remplie sont
désactivés ». C'est volontaire de la part de Power BI : ces visuels transmettent
les coordonnées à un service cartographique externe (Bing/Azure Maps).
L'activation se fait dans *Fichier > Options et paramètres > Options > Global >
Sécurité*, et revient à accepter cet envoi.

Le rapport n'en dépend pas : la ponctualité par gare est rendue par un
histogramme, sans service externe. Les colonnes `stop_lat` / `stop_lon` restent
catégorisées Latitude/Longitude dans le modèle, prêtes à servir si la carte est
activée.

### Pièges rencontrés lors de la mise au point

Ces quatre points ont chacun fait échouer l'ouverture du projet ; ils sont
corrigés dans les fichiers livrés, et notés ici parce qu'ils ne sont pas
devinables.

| Symptôme | Cause |
|---|---|
| `Expected '$schema' property … to follow patterns` | l'URL `$schema` du `.pbip` doit suivre `fabric/pbip/pbipProperties/1.x.x` |
| `La propriété « description » est inconnue` | les commentaires `///` de TMDL deviennent des descriptions, que les relations n'acceptent pas |
| `Une erreur s'est produite lors du rendu du rapport` | `report.json` (format hérité) sans `resourcePackages` déclarant le thème de base — échoue même sans aucun visuel |
| `dim_station : 8 720 lignes chargées, 8 720 erreurs` | culture `fr-FR` : la virgule y est le séparateur décimal, donc `47.75` est rejeté. Corrigé par le paramètre `"en-US"` de `Table.TransformColumnTypes` |
| `La propriété requise « reportVersionAtImport » n'a pas été incluse` | un thème personnalisé déclaré dans `themeCollection.customTheme` exige le même bloc de version que le thème de base |
| Un segment n'affiche que son titre, sans aucune valeur | `general.orientation` vaut **1** pour les vignettes en ligne et 2 pour la liste verticale ; il faut en plus `data.mode = "Basic"`. En liste verticale sans en-tête, le segment restait vide |

Le dernier est le plus insidieux : il n'empêche pas l'ouverture, il vide
seulement la dimension gare, et la carte reste désespérément vide sans message
d'erreur visible.

## 1. Préparer les données

```bash
python src/ingest_sncf.py       # si la collecte planifiée est arrêtée
python src/build_marts.py
python src/quality_checks.py    # doit sortir "all checks passed"
python src/export_powerbi.py
```

Ne pas construire le rapport si `quality_checks.py` échoue : les indicateurs
seraient faux sans que rien ne le signale à l'écran.

## 2. Importer

*Accueil → Obtenir les données → Texte/CSV*, une fois par fichier :

| Fichier | Table |
|---|---|
| `data/export/fact_passage.csv` | table de faits |
| `data/export/dim_station.csv` | dimension gare |
| `data/export/dim_route.csv` | dimension ligne |

Les fichiers sont encodés en UTF-8 avec BOM : Power BI détecte `65001`
automatiquement et les accents des noms de gares sont corrects. Si des
caractères apparaissent altérés, forcer l'encodage à *Unicode (UTF-8)* dans la
boîte de dialogue d'import.

Vérifier le typage avant de charger : `arrival_delay_s`, `scheduled_hour` et
`is_punctual` en nombre entier, `service_date` en date, `stop_lat` / `stop_lon`
en nombre décimal.

## 3. Relations

*Modèle* → créer si elles n'ont pas été détectées :

```
dim_station[stop_id]  1 ─── * fact_passage[stop_id]
dim_route[route_id]   1 ─── * fact_passage[route_id]
```

Cardinalité **un vers plusieurs**, direction de filtre **simple** (de la
dimension vers les faits). L'unicité des clés et l'absence de lignes orphelines
sont vérifiées par `quality_checks.py`, ces relations doivent donc se créer sans
avertissement.

Pour la carte : sélectionner `stop_lat` → *Catégorie de données : Latitude*, et
`stop_lon` → *Longitude*.

## 4. Mesures DAX

```dax
Passages mesurables =
CALCULATE(COUNTROWS(fact_passage), NOT ISBLANK(fact_passage[is_punctual]))

Passages ponctuels =
CALCULATE(SUM(fact_passage[is_punctual]), NOT ISBLANK(fact_passage[is_punctual]))

Taux de ponctualite =
DIVIDE([Passages ponctuels], [Passages mesurables])

Retard moyen (min) =
DIVIDE(AVERAGE(fact_passage[arrival_delay_s]), 60)

Trains supprimes =
CALCULATE(COUNTROWS(fact_passage), fact_passage[schedule_relationship] = "CANCELED")
```

Formater *Taux de ponctualite* en pourcentage à une décimale.

`is_punctual` est vide pour les trains supprimés et pour les passages sans
retard renseigné. C'est délibéré : le filtre `NOT ISBLANK` les exclut du
dénominateur, de sorte qu'un train supprimé ne compte ni comme ponctuel ni comme
en retard. Remplacer ces mesures par un simple `AVERAGE(is_punctual)` donnerait
le même résultat aujourd'hui mais masquerait l'intention ; les conserver
explicites documente la méthode dans le modèle lui-même.

## 5. Visuels

**Carte des gares** — *Carte* : `stop_name` en emplacement, `Passages mesurables`
en taille de bulle, `Taux de ponctualite` en couleur. Filtre visuel :
`Passages mesurables` ≥ 20, sinon les petites gares dominent l'échelle.

**Courbe horaire** — *Graphique en courbes* : `scheduled_hour` en axe,
`Taux de ponctualite` en valeur.

> Tant que la collecte ne couvre pas plusieurs journées complètes, ajouter
> `Passages mesurables` en axe secondaire ou en info-bulle : les tranches
> horaires faiblement échantillonnées doivent rester visibles comme telles.

**Classement des lignes** — *Histogramme groupé horizontal* :
`route_long_name` en axe, `Taux de ponctualite` en valeur, tri croissant,
filtre visuel `Passages mesurables` ≥ 20, 10 premiers résultats.

**Cartes de synthèse** — `Taux de ponctualite`, `Retard moyen (min)`,
`Passages mesurables`, `Trains supprimes`.

**Segments** — `service_date`, `dim_route[mode]`, et `stop_name` pour isoler un
périmètre régional.

## 6. Rafraîchir

```bash
python src/build_marts.py && python src/quality_checks.py && python src/export_powerbi.py
```

puis *Accueil → Actualiser* dans Power BI. Les chemins des fichiers ne changent
pas, l'actualisation ne demande aucune reconfiguration.
