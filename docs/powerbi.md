# Construction du rapport Power BI

Le rapport se construit sur les trois fichiers CSV produits par
`src/export_powerbi.py` dans `data/export/`. Power BI ne lit SQLite qu'à travers
un pilote ODBC à installer et configurer séparément ; le CSV évite cette
dépendance et le rafraîchissement se résume à relancer le script.

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
