# -*- coding: utf-8 -*-
"""Write the Power BI report definition: theme, pages and visuals.

The report is generated rather than clicked together. The PBIR format stores
every visual as its own JSON file, so the layout can be exact to the pixel, read
in a diff, and rebuilt after a measure changes instead of being repositioned by
hand. Only the strings are French: they are the report's own titles.

Run it with Power BI closed, then reopen powerbi/Ponctualite.pbip and refresh.
"""
import hashlib
import json
import shutil
from pathlib import Path

RACINE = Path(__file__).resolve().parent / "Ponctualite.Report"
PAGES = RACINE / "definition" / "pages"

SCHEMA_VISUAL = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json"
SCHEMA_PAGE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
SCHEMA_PAGES = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json"
SCHEMA_REPORT = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json"

ENCRE = "#1F2933"
GRIS = "#52606D"
BLEU = "#1B3A5C"
AMBRE = "#E39A2B"
ROUGE = "#A63D40"
FOND = "#F4F6F8"

compteur = [0]


def ident(cle):
    compteur[0] += 1
    return hashlib.sha512(f"{cle}-{compteur[0]}".encode()).hexdigest()[:20]


def litt(valeur):
    """A literal value for a formatting property."""
    if isinstance(valeur, bool):
        v = "true" if valeur else "false"
    elif isinstance(valeur, (int, float)):
        v = f"{valeur}D"
    else:
        v = f"'{valeur}'"
    return {"expr": {"Literal": {"Value": v}}}


def couleur(hexa):
    return {"solid": {"color": litt(hexa)}}


def champ_colonne(entite, propriete):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entite}}, "Property": propriete}}


def champ_mesure(entite, propriete):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entite}}, "Property": propriete}}


def projection(champ, entite, propriete, actif=None, nom_affiche=None):
    p = {
        "field": champ,
        "queryRef": f"{entite}.{propriete}",
        "nativeQueryRef": propriete,
    }
    if nom_affiche:
        p["displayName"] = nom_affiche
    if actif is not None:
        p["active"] = actif
    return p


def col(entite, propriete, **kw):
    return projection(champ_colonne(entite, propriete), entite, propriete, **kw)


def mes(propriete, entite="fact_passage", **kw):
    return projection(champ_mesure(entite, propriete), entite, propriete, **kw)


def visuel(cle, type_visuel, x, y, w, h, roles=None, titre=None, sous_titre=None,
           objets=None, tri=None, sync=None, conteneur_extra=None):
    compteur_z = compteur[0]
    conteneur = {}
    if titre is not None:
        props = {
            "show": litt(True),
            "text": litt(titre),
            "fontColor": couleur(ENCRE),
            "fontSize": litt(12),
            "bold": litt(True),
            "alignment": litt("left"),
            "titleWrap": litt(True),
        }
        conteneur["title"] = [{"properties": props}]
    if sous_titre is not None:
        conteneur["subTitle"] = [{"properties": {
            "show": litt(True),
            "text": litt(sous_titre),
            "fontColor": couleur(GRIS),
            "fontSize": litt(9),
            "alignment": litt("left"),
        }}]
    conteneur["background"] = [{"properties": {"show": litt(True), "color": couleur("#FFFFFF"), "transparency": litt(0)}}]
    conteneur["border"] = [{"properties": {"show": litt(True), "color": couleur("#E4E7EB"), "radius": litt(6)}}]
    conteneur["padding"] = [{"properties": {"top": litt(10), "bottom": litt(10), "left": litt(12), "right": litt(12)}}]
    conteneur["visualHeader"] = [{"properties": {"show": litt(False)}}]
    if conteneur_extra:
        conteneur.update(conteneur_extra)

    v = {"visualType": type_visuel}
    if roles:
        query = {"queryState": {role: {"projections": projs} for role, projs in roles.items()}}
        if tri:
            query["sortDefinition"] = {"sort": tri}
        v["query"] = query
    if objets:
        v["objects"] = objets
    if sync:
        v["syncGroup"] = sync
    v["visualContainerObjects"] = conteneur

    return {
        "$schema": SCHEMA_VISUAL,
        "name": ident(cle),
        "position": {"x": x, "y": y, "z": compteur_z, "height": h, "width": w, "tabOrder": compteur_z},
        "visual": v,
    }


def texte(cle, x, y, w, h, paragraphes, fond=None):
    """Text box: paragraphs are a plain value, not a wrapped expression."""
    conteneur = {
        "background": [{"properties": {"show": litt(fond is not None), "color": couleur(fond or "#FFFFFF"), "transparency": litt(0)}}],
        "visualHeader": [{"properties": {"show": litt(False)}}],
        "padding": [{"properties": {"top": litt(8), "bottom": litt(8), "left": litt(12), "right": litt(12)}}],
    }
    if fond is None:
        conteneur["border"] = [{"properties": {"show": litt(False)}}]
    else:
        conteneur["border"] = [{"properties": {"show": litt(True), "color": couleur("#E4E7EB"), "radius": litt(6)}}]
    compteur_z = compteur[0]
    return {
        "$schema": SCHEMA_VISUAL,
        "name": ident(cle),
        "position": {"x": x, "y": y, "z": compteur_z, "height": h, "width": w, "tabOrder": compteur_z},
        "visual": {
            "visualType": "textbox",
            "objects": {"general": [{"properties": {"paragraphs": paragraphes}}]},
            "visualContainerObjects": conteneur,
        },
    }


def paragraphe(runs, taille_defaut="10pt", espace_avant=None):
    p = {"textRuns": [
        {"value": r[0], "textStyle": {k: v for k, v in [
            ("fontSize", r[1].get("taille", taille_defaut)),
            ("fontWeight", r[1].get("gras")),
            ("color", r[1].get("couleur", ENCRE)),
            ("fontFamily", r[1].get("police", "Segoe UI")),
        ] if v is not None}}
        for r in runs
    ]}
    # Power BI ignore l'espacement inter-paragraphe d'une zone de texte : il le
    # retire du fichier a la sauvegarde. L'argument est conserve pour la lisibilite
    # de l'appel, mais n'est plus ecrit.
    return p


def carte(cle, mesure, titre, x, y, w, h, teinte=BLEU):
    return visuel(
        cle, "card", x, y, w, h,
        roles={"Values": [mes(mesure)]},
        titre=titre,
        objets={
            "labels": [{"properties": {"color": couleur(teinte), "fontSize": litt(28), "bold": litt(True)}}],
            "categoryLabels": [{"properties": {"show": litt(False)}}],
            "wordWrap": [{"properties": {"show": litt(False)}}],
        },
    )


def segment(cle, entite, propriete, titre, x, y, w, h, groupe, horizontal=True, liste_deroulante=False):
    # orientation 1 is the tile layout, 2 the vertical list. Checked against
    # what Power BI itself writes: as a vertical list with no header, the slicer
    # rendered its title and nothing else. data.mode separates a list from a
    # dropdown, and is required for the values to appear at all.
    objets = {
        "general": [{"properties": {"orientation": litt(1 if horizontal else 2)}}],
        "data": [{"properties": {"mode": litt("Dropdown" if liste_deroulante else "Basic")}}],
        "header": [{"properties": {"show": litt(False)}}],
        "items": [{"properties": {"fontColor": couleur(ENCRE), "fontSize": litt(9)}}],
        "selection": [{"properties": {"singleSelect": litt(False), "strictSingleSelect": litt(False)}}],
    }
    return visuel(
        cle, "slicer", x, y, w, h,
        roles={"Values": [col(entite, propriete)]},
        titre=titre,
        objets=objets,
        sync={"groupName": groupe, "fieldChanges": False, "filterChanges": True},
    )


def axe_pourcentage(debut=0.70, fin=1.0):
    return {
        "valueAxis": [{"properties": {
            "start": litt(debut), "end": litt(fin),
            "showAxisTitle": litt(False),
            "gridlineColor": couleur("#EDF0F3"),
            "labelColor": couleur(GRIS), "fontSize": litt(9),
        }}],
        "categoryAxis": [{"properties": {
            "showAxisTitle": litt(False),
            "gridlineShow": litt(False),
            "labelColor": couleur(GRIS), "fontSize": litt(9),
        }}],
    }


def legende(position="TopCenter"):
    return {"legend": [{"properties": {
        "show": litt(True), "position": litt(position), "showTitle": litt(False),
        "labelColor": couleur(GRIS), "fontSize": litt(9),
    }}]}


# ---------------------------------------------------------------- page 1
def page_vue():
    v = []
    v.append(texte("titre", 20, 10, 500, 74, [
        paragraphe([("Ponctualité du réseau ferroviaire français", {"taille": "18pt", "gras": "600", "couleur": BLEU})]),
        paragraphe([("Flux GTFS-RT SNCF confronté aux horaires théoriques", {"taille": "9pt", "couleur": GRIS})]),
    ]))
    v.append(visuel(
        "perimetre", "card", 20, 84, 620, 26,
        roles={"Values": [mes("Perimetre")]},
        objets={
            "labels": [{"properties": {"color": couleur(GRIS), "fontSize": litt(9), "bold": litt(False)}}],
            "categoryLabels": [{"properties": {"show": litt(False)}}],
            "wordWrap": [{"properties": {"show": litt(False)}}],
        },
        conteneur_extra={
            "background": [{"properties": {"show": litt(False)}}],
            "border": [{"properties": {"show": litt(False)}}],
            "padding": [{"properties": {"top": litt(0), "bottom": litt(0), "left": litt(0), "right": litt(0)}}],
        },
    ))
    v.append(segment("seg-jour", "fact_passage", "Jour", "Jour de service", 540, 14, 150, 88, "jour", liste_deroulante=True))
    v.append(segment("seg-type", "fact_passage", "Type de jour", "Type de jour", 705, 14, 220, 88, "type"))
    v.append(segment("seg-mode", "dim_route", "mode", "Mode", 940, 14, 320, 88, "mode"))

    cartes = [
        ("Taux ponctualite observe", "Ponctualité (0 ou 5 min annoncés)", BLEU),
        ("Taux ponctualite strict", "Ponctualité stricte (sous 5 min)", AMBRE),
        ("Passages observes", "Passages observés", ENCRE),
        ("Retard p90 observe (min)", "9 passages sur 10 sous (min)", ENCRE),
    ]
    for i, (mesure, titre, teinte) in enumerate(cartes):
        v.append(carte(f"carte{i}", mesure, titre, 20 + i * 310, 112, 290, 96, teinte))

    objets_ligne = {}
    objets_ligne.update(axe_pourcentage(0.70, 1.0))
    objets_ligne.update(legende())
    objets_ligne["lineStyles"] = [{"properties": {"strokeWidth": litt(3), "showMarker": litt(True), "markerShape": litt("circle")}}]
    v.append(visuel(
        "courbe-horaire", "lineChart", 20, 216, 780, 244,
        roles={
            "Category": [col("fact_passage", "Heure", actif=True)],
            "Y": [mes("Taux ponctualite observe", nom_affiche="Retard annoncé de 0 ou 5 min"), mes("Taux ponctualite strict", nom_affiche="Retard annoncé sous 5 min")],
        },
        titre="La ponctualité se dégrade au fil de la journée",
        sous_titre="Les deux conventions de seuil, par heure théorique d'arrivée",
        objets=objets_ligne,
        tri=[{"field": champ_colonne("fact_passage", "Heure"), "direction": "Ascending"}],
    ))

    objets_hist = {
        "categoryAxis": [{"properties": {"showAxisTitle": litt(False), "gridlineShow": litt(False), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
        "valueAxis": [{"properties": {"showAxisTitle": litt(False), "gridlineColor": couleur("#EDF0F3"), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
        "dataPoint": [{"properties": {"fill": couleur(AMBRE)}}],
        "labels": [{"properties": {"show": litt(False)}}],
    }
    v.append(visuel(
        "histogramme", "columnChart", 820, 216, 440, 244,
        roles={
            "Category": [col("fact_passage", "Retard annonce", actif=True)],
            "Y": [mes("Passages observes en retard")],
        },
        titre="Le flux n'annonce que des multiples de 5 minutes",
        sous_titre="Passages en retard, par minute annoncée — le seuil tombe sur un palier",
        objets=objets_hist,
        tri=[{"field": champ_colonne("fact_passage", "Retard annonce"), "direction": "Ascending"}],
    ))

    objets_jour = {}
    objets_jour.update(axe_pourcentage(0.85, 0.97))
    objets_jour.update(legende())
    objets_jour["lineStyles"] = [{"properties": {"strokeWidth": litt(2), "showMarker": litt(True)}}]
    v.append(visuel(
        "par-jour", "lineChart", 20, 472, 780, 232,
        roles={
            "Category": [col("fact_passage", "Tranche", actif=True)],
            "Series": [col("fact_passage", "Type de jour")],
            "Y": [mes("Taux ponctualite observe")],
        },
        titre="Le week-end est plus ponctuel à toute heure, et les deux déclinent de la même façon",
        sous_titre="Écart vérifié sur 15 journées complètes — test de permutation exact, p = 0,008",
        objets=objets_jour,
        tri=[{"field": champ_colonne("fact_passage", "Tranche"), "direction": "Ascending"}],
    ))

    v.append(texte("methode-courte", 820, 472, 440, 232, [
        paragraphe([("Comment lire ces chiffres", {"taille": "11pt", "gras": "600", "couleur": BLEU})]),
        paragraphe([("Passages observés. ", {"taille": "9pt", "gras": "600"}),
                    ("Un passage ne compte que s'il a eu lieu et que le collecteur tournait à cette heure-là. Les passages vus après coup sont biaisés vers les trains longs.", {"taille": "9pt", "couleur": GRIS})], espace_avant=6),
        paragraphe([("Deux bornes. ", {"taille": "9pt", "gras": "600"}),
                    ("Le flux n'annonce les retards que par paliers de 5 minutes, soit exactement le seuil retenu : près de 9 % des passages sont annoncés à 5 minutes pile et décident du taux à eux seuls. Le taux est donc encadré, jamais publié seul.", {"taille": "9pt", "couleur": GRIS})], espace_avant=6),
        paragraphe([("Seuils. ", {"taille": "9pt", "gras": "600"}),
                    ("Ponctualité mesurée par passage en gare, pas par trajet. Trains supprimés conservés mais jamais comptés ponctuels. Classements limités aux lignes et gares d'au moins 20 passages.", {"taille": "9pt", "couleur": GRIS})], espace_avant=6),
    ], fond="#FFFFFF"))
    return "PageVue", "Vue d'ensemble", v


# ---------------------------------------------------------------- page 2
def page_lignes():
    v = []
    v.append(texte("titre2", 20, 10, 500, 74, [
        paragraphe([("Où la ponctualité décroche", {"taille": "18pt", "gras": "600", "couleur": BLEU})]),
        paragraphe([("Lignes et gares d'au moins 20 passages observés  ·  tout est croisé : cliquez sur une barre", {"taille": "9pt", "couleur": GRIS})]),
    ]))
    v.append(segment("seg-jour2", "fact_passage", "Jour", "Jour de service", 540, 14, 150, 88, "jour", liste_deroulante=True))
    v.append(segment("seg-type2", "fact_passage", "Type de jour", "Type de jour", 705, 14, 220, 88, "type"))
    v.append(segment("seg-mode2", "dim_route", "mode", "Mode", 940, 14, 320, 88, "mode"))

    objets_barres = {
        "categoryAxis": [{"properties": {"showAxisTitle": litt(False), "labelColor": couleur(ENCRE), "fontSize": litt(9)}}],
        "valueAxis": [{"properties": {"showAxisTitle": litt(False), "gridlineColor": couleur("#EDF0F3"), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
        "dataPoint": [{"properties": {"fill": couleur(ROUGE)}}],
        "labels": [{"properties": {"show": litt(True), "color": couleur(GRIS), "fontSize": litt(9)}}],
    }
    v.append(visuel(
        "lignes-pires", "clusteredBarChart", 20, 112, 610, 320,
        roles={
            "Category": [col("dim_route", "route_long_name", actif=True)],
            "Y": [mes("Taux ligne (10 pires)")],
        },
        titre="Les 10 lignes les moins ponctuelles",
        sous_titre="Classement recalculé selon les filtres actifs",
        objets=objets_barres,
        tri=[{"field": champ_mesure("fact_passage", "Taux ligne (10 pires)"), "direction": "Ascending"}],
    ))
    v.append(visuel(
        "gares-pires", "clusteredBarChart", 650, 112, 610, 320,
        roles={
            "Category": [col("dim_station", "stop_name", actif=True)],
            "Y": [mes("Taux gare (10 pires)")],
        },
        titre="Les 10 gares les moins ponctuelles",
        sous_titre="Un service défaillant apparaît autant de fois qu'il dessert d'arrêts",
        objets=objets_barres,
        tri=[{"field": champ_mesure("fact_passage", "Taux gare (10 pires)"), "direction": "Ascending"}],
    ))

    objets_nuage = {
        "categoryAxis": [{"properties": {"showAxisTitle": litt(True), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
        "valueAxis": [{"properties": {"showAxisTitle": litt(False), "gridlineColor": couleur("#EDF0F3"), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
        "fillPoint": [{"properties": {"show": litt(True)}}],
        "dataPoint": [{"properties": {"fill": couleur(BLEU)}}],
    }
    v.append(visuel(
        "nuage", "scatterChart", 20, 444, 610, 260,
        roles={
            "Category": [col("dim_route", "route_long_name", actif=True)],
            "X": [mes("Passages observes", nom_affiche="Passages observés")],
            "Y": [mes("Taux ponctualite observe")],
        },
        titre="Un taux extrême sur peu de passages ne prouve rien",
        sous_titre="Chaque point est une ligne : nombre de passages observés et taux mesuré",
        objets=objets_nuage,
    ))

    v.append(visuel(
        "table-lignes", "tableEx", 650, 444, 610, 260,
        roles={"Values": [
            col("dim_route", "route_long_name", nom_affiche="Ligne"),
            mes("Passages observes (min 20)", nom_affiche="Passages"),
            mes("Taux ponctualite observe", nom_affiche="Taux (0 ou 5 min)"),
            mes("Taux ponctualite strict", nom_affiche="Taux strict"),
        ]},
        titre="Le détail, trié du pire au meilleur",
        objets={
            "grid": [{"properties": {"gridVertical": litt(False), "outlineColor": couleur("#E4E7EB")}}],
            "columnHeaders": [{"properties": {"fontColor": couleur(GRIS), "fontSize": litt(9), "bold": litt(True)}}],
            "values": [{"properties": {"fontSize": litt(9), "fontColor": couleur(ENCRE)}}],
            "total": [{"properties": {"totals": litt(False)}}],
        },
        tri=[{"field": champ_mesure("fact_passage", "Taux ponctualite observe"), "direction": "Ascending"}],
    ))
    return "PageLignes", "Lignes et gares", v


# ---------------------------------------------------------------- page 3
def page_methode():
    v = []
    v.append(texte("titre3", 20, 10, 840, 74, [
        paragraphe([("Ce que le chiffre ne dit pas tout seul", {"taille": "18pt", "gras": "600", "couleur": BLEU})]),
        paragraphe([("Trois corrections appliquées aux données avant toute publication", {"taille": "9pt", "couleur": GRIS})]),
    ]))
    v.append(segment("seg-jour3", "fact_passage", "Jour", "Jour de service", 760, 14, 160, 88, "jour", liste_deroulante=True))
    v.append(segment("seg-type3", "fact_passage", "Type de jour", "Type de jour", 940, 14, 320, 88, "type"))

    v.append(carte("ecart", "Ecart de convention (pts)", "Écart entre les deux conventions (pts)", 20, 112, 290, 96, AMBRE))
    v.append(carte("part-obs", "Part observee", "Passages retenus dans le taux", 330, 112, 290, 96, BLEU))
    v.append(carte("median", "Retard median observe (min)", "Retard médian (min)", 640, 112, 290, 96, ENCRE))
    v.append(carte("p99", "Retard p99 observe (min)", "Retard du 99e centile (min)", 950, 112, 290, 96, ENCRE))

    objets_pop = {
        "categoryAxis": [{"properties": {"showAxisTitle": litt(False), "labelColor": couleur(ENCRE), "fontSize": litt(9)}}],
        "valueAxis": [{"properties": {"showAxisTitle": litt(False), "start": litt(0.8), "end": litt(0.95), "gridlineColor": couleur("#EDF0F3"), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
        "labels": [{"properties": {"show": litt(True), "color": couleur(ENCRE), "fontSize": litt(9)}}],
        "legend": [{"properties": {"show": litt(True), "position": litt("Bottom"), "showTitle": litt(False), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
    }
    v.append(visuel(
        "populations", "clusteredBarChart", 20, 216, 600, 236,
        roles={"Y": [mes("Taux ponctualite observe", nom_affiche="Passages observés"), mes("Taux vus apres coup", nom_affiche="Vus après coup"), mes("Taux passages a venir", nom_affiche="Encore à venir")]},
        titre="Trois populations, trois taux différents",
        sous_titre="Les passages vus après coup sont biaisés vers les trains longs : ils sont exclus du taux publié",
        objets=objets_pop,
    ))

    objets_couv = {
        "categoryAxis": [{"properties": {"showAxisTitle": litt(False), "gridlineShow": litt(False), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
        "valueAxis": [{"properties": {"showAxisTitle": litt(False), "gridlineColor": couleur("#EDF0F3"), "labelColor": couleur(GRIS), "fontSize": litt(9)}}],
        "dataPoint": [{"properties": {"fill": couleur("#8FB0C4")}}],
        "labels": [{"properties": {"show": litt(False)}}],
    }
    v.append(visuel(
        "couverture", "columnChart", 640, 216, 620, 236,
        roles={
            "Category": [col("fact_passage", "Heure", actif=True)],
            "Y": [mes("Part observee")],
        },
        titre="Ce que le collecteur a réellement vu, heure par heure",
        sous_titre="Une heure sous-couverte ne fabrique plus de faux résultat : elle est écartée",
        objets=objets_couv,
        tri=[{"field": champ_colonne("fact_passage", "Heure"), "direction": "Ascending"}],
    ))

    v.append(texte("notes", 20, 464, 1240, 214, [
        paragraphe([("Les trois corrections", {"taille": "12pt", "gras": "600", "couleur": BLEU})]),
        paragraphe([("1. Le seuil tombe sur un palier du flux. ", {"taille": "9pt", "gras": "600"}),
                    ("Les retards ne sont annoncés que par multiples de 5 minutes, jamais négatifs, et l'heure d'arrivée prévue n'ajoute aucune précision : elle vaut l'horaire théorique plus le retard annoncé dans 99,97 % des cas. Un passage annoncé à 5 minutes peut donc être en retard de 5 à 10 minutes. Le taux est publié entre deux bornes, jamais seul.", {"taille": "9pt", "couleur": GRIS})], espace_avant=8),
        paragraphe([("2. Les heures non collectées se faisaient passer pour des mesures. ", {"taille": "9pt", "gras": "600"}),
                    ("Le flux garde un train tant qu'il roule : après une coupure du collecteur, seuls les trains longs restent visibles. Une dégradation de l'après-midi s'est révélée n'être que cet artefact. Chaque passage porte donc un indicateur de couverture, et le taux n'utilise que les heures surveillées à plus de 50 %.", {"taille": "9pt", "couleur": GRIS})], espace_avant=6),
        paragraphe([("3. Un train supprimé n'est pas un train ponctuel. ", {"taille": "9pt", "gras": "600"}),
                    ("Les trajets supprimés restent dans la table de faits mais ne sont jamais comptés ponctuels ; les trajets ajoutés hors horaire, qui n'ont pas d'heure théorique, en sont exclus. Quinze contrôles automatisés vérifient ces règles à chaque exécution, dont le taux de jointure jour par jour.", {"taille": "9pt", "couleur": GRIS})], espace_avant=6),
    ], fond="#FFFFFF"))
    return "PageMethode", "Méthode et qualité", v


# ---------------------------------------------------------------- page 4
def degrade_ponctualite():
    """Colour each point by how far it sits from the network's own rate.

    A diverging scale with a neutral midpoint at the network average (92 %): the
    typical station stays quiet grey, and only the ones that stand out, on
    either side, take a colour. The first version went red to amber to navy, and
    amber blended into navy as a muddy olive exactly where most stations sit.
    """
    def etape(hexa, valeur):
        return {"color": {"Literal": {"Value": f"'{hexa}'"}}, "value": {"Literal": {"Value": f"{valeur}D"}}}
    return [{
        "properties": {"fill": {"solid": {"color": {"expr": {"FillRule": {
            "Input": champ_mesure("fact_passage", "Taux ponctualite observe"),
            "FillRule": {"linearGradient3": {
                "min": etape(ROUGE, 0.80),
                "mid": etape("#C3CAD3", 0.92),
                "max": etape(BLEU, 1.0),
                "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}},
            }},
        }}}}}},
        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]},
    }]


def page_carte():
    """A map without a map service: every station placed by its own coordinates.

    Power BI's map visuals send coordinates to Bing or Azure Maps, which is why
    they ship disabled. A scatter of longitude against latitude needs nothing
    outside the report, and with a few thousand stations the outline of the
    network, and of the country, draws itself. The plot is kept close to square
    so that one degree of longitude, about 0.68 of a degree of latitude at 47 N,
    does not stretch the country sideways.
    """
    v = []
    v.append(texte("titre4", 20, 10, 500, 74, [
        paragraphe([("Où se trouvent les gares en retard", {"taille": "18pt", "gras": "600", "couleur": BLEU})]),
        paragraphe([("Chaque gare placée par ses coordonnées, sans service cartographique externe", {"taille": "9pt", "couleur": GRIS})]),
    ]))
    v.append(segment("seg-jour4", "fact_passage", "Jour", "Jour de service", 540, 14, 150, 88, "jour", liste_deroulante=True))
    v.append(segment("seg-type4", "fact_passage", "Type de jour", "Type de jour", 705, 14, 220, 88, "type"))
    v.append(segment("seg-mode4", "dim_route", "mode", "Mode", 940, 14, 320, 88, "mode"))

    cache = {"show": litt(False), "gridlineShow": litt(False), "showAxisTitle": litt(False)}
    objets_carte = {
        # Bornes de la France métropolitaine : les gares étrangères desservies
        # par les liaisons transfrontalières sortent du cadre.
        "categoryAxis": [{"properties": dict(cache, start=litt(-5.2), end=litt(10.3))}],
        "valueAxis": [{"properties": dict(cache, start=litt(41.3), end=litt(51.2))}],
        "dataPoint": degrade_ponctualite(),
        "fillPoint": [{"properties": {"show": litt(True)}}],
    }
    v.append(visuel(
        "carte", "scatterChart", 20, 112, 620, 592,
        roles={
            "Category": [col("dim_station", "stop_name", actif=True, nom_affiche="Gare")],
            "X": [mes("Longitude gare", nom_affiche="Longitude")],
            "Y": [mes("Latitude gare", nom_affiche="Latitude")],
            "Tooltips": [
                mes("Passages observes", nom_affiche="Passages observés"),
                mes("Taux ponctualite observe", nom_affiche="Taux (0 ou 5 min)"),
                mes("Taux ponctualite strict", nom_affiche="Taux strict"),
            ],
        },
        titre="Le réseau, gare par gare",
        sous_titre="Gris : dans la moyenne du réseau (92 %) · rouge : en dessous · bleu : au-dessus",
        objets=objets_carte,
    ))

    v.append(carte("gares", "Gares mesurees", "Gares d'au moins 20 passages", 660, 112, 290, 96, ENCRE))
    v.append(carte("taux4", "Taux ponctualite observe", "Ponctualité (0 ou 5 min annoncés)", 970, 112, 290, 96, BLEU))

    v.append(visuel(
        "table-gares", "tableEx", 660, 220, 600, 330,
        roles={"Values": [
            col("dim_station", "stop_name", nom_affiche="Gare"),
            mes("Passages observes (min 20)", nom_affiche="Passages"),
            mes("Taux ponctualite observe", nom_affiche="Taux (0 ou 5 min)"),
            mes("Taux ponctualite strict", nom_affiche="Taux strict"),
        ]},
        titre="Cliquez une gare pour la situer sur la carte",
        sous_titre="Triée de la moins ponctuelle à la plus ponctuelle",
        objets={
            "grid": [{"properties": {"gridVertical": litt(False), "outlineColor": couleur("#E4E7EB")}}],
            "columnHeaders": [{"properties": {"fontColor": couleur(GRIS), "fontSize": litt(9), "bold": litt(True)}}],
            "values": [{"properties": {"fontSize": litt(9), "fontColor": couleur(ENCRE)}}],
            "total": [{"properties": {"totals": litt(False)}}],
        },
        tri=[{"field": champ_mesure("fact_passage", "Taux ponctualite observe"), "direction": "Ascending"}],
    ))

    v.append(texte("note-carte", 660, 562, 600, 142, [
        paragraphe([("Lire la carte", {"taille": "11pt", "gras": "600", "couleur": BLEU})]),
        paragraphe([("Aucun fond de carte : la forme du pays vient des gares elles-mêmes. Une gare n'apparaît qu'à partir de 20 passages observés dans la sélection en cours, et les gares étrangères des liaisons transfrontalières sortent du cadre. Les segments filtrent la carte, le tableau et les chiffres ensemble.", {"taille": "9pt", "couleur": GRIS})]),
    ], fond="#FFFFFF"))
    return "PageCarte", "Carte des gares", v


def ecrire_page(nom, affichage, visuels):
    dossier = PAGES / nom
    if dossier.exists():
        shutil.rmtree(dossier)  # start clean: Power BI may have added visuals
    (dossier / "visuals").mkdir(parents=True, exist_ok=True)
    page = {
        "$schema": SCHEMA_PAGE,
        "name": nom,
        "displayName": affichage,
        "displayOption": "FitToPage",
        "height": 720,
        "width": 1280,
        "objects": {
            "background": [{"properties": {"color": couleur(FOND), "transparency": litt(0)}}],
            "outspace": [{"properties": {"color": couleur(FOND)}}],
        },
    }
    (dossier / "page.json").write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")
    for vis in visuels:
        d = dossier / "visuals" / vis["name"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "visual.json").write_text(json.dumps(vis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  {affichage:20} {len(visuels)} visuels")


def theme():
    return {
        "name": "PonctualiteTheme",
        "dataColors": [BLEU, AMBRE, "#3E7C8C", ROUGE, "#6E7A87", "#8FB0C4", "#C9A227", "#4F6D7A", "#9AA5B1", "#2E7D5B"],
        "background": "#FFFFFF",
        "foreground": ENCRE,
        "tableAccent": BLEU,
        "good": "#2E7D5B",
        "neutral": AMBRE,
        "bad": ROUGE,
        "maximum": BLEU,
        "center": "#8FB0C4",
        "minimum": "#EDF0F3",
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": ENCRE},
            "header": {"fontFace": "Segoe UI Semibold", "fontSize": 10, "color": ENCRE},
            "label": {"fontFace": "Segoe UI", "fontSize": 9, "color": GRIS},
            "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 28, "color": BLEU},
        },
        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": "#FFFFFF"}}, "transparency": 0}],
                    "border": [{"show": True, "color": {"solid": {"color": "#E4E7EB"}}, "radius": 6}],
                    "dropShadow": [{"show": False}],
                    "visualHeader": [{"show": False}],
                    "title": [{"show": True, "fontColor": {"solid": {"color": ENCRE}}, "fontSize": 12, "bold": True, "alignment": "left"}],
                    "legend": [{"show": True, "position": "TopCenter", "showTitle": False, "fontSize": 9, "labelColor": {"solid": {"color": GRIS}}}],
                    "categoryAxis": [{"showAxisTitle": False, "gridlineShow": False, "fontSize": 9, "labelColor": {"solid": {"color": GRIS}}}],
                    "valueAxis": [{"showAxisTitle": False, "fontSize": 9, "labelColor": {"solid": {"color": GRIS}}, "gridlineColor": {"solid": {"color": "#EDF0F3"}}}],
                    "labels": [{"fontSize": 9, "color": {"solid": {"color": GRIS}}}],
                }
            },
            "page": {
                "*": {
                    "background": [{"color": {"solid": {"color": FOND}}, "transparency": 0}],
                    "outspace": [{"color": {"solid": {"color": FOND}}}],
                }
            },
            "slicer": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": "#FFFFFF"}}, "transparency": 0}],
                    "items": [{"fontColor": {"solid": {"color": ENCRE}}, "background": {"solid": {"color": "#FFFFFF"}}, "fontSize": 9}],
                }
            },
        },
    }


def main():
    # theme
    res = RACINE / "StaticResources" / "RegisteredResources"
    res.mkdir(parents=True, exist_ok=True)
    (res / "PonctualiteTheme.json").write_text(json.dumps(theme(), indent=2, ensure_ascii=False), encoding="utf-8")

    rapport = json.loads((RACINE / "definition" / "report.json").read_text(encoding="utf-8"))
    # reportVersionAtImport is required on the custom theme as well: without it
    # Power BI refuses to open the report at all.
    rapport["themeCollection"]["customTheme"] = {
        "name": "PonctualiteTheme",
        "type": "RegisteredResources",
        "reportVersionAtImport": rapport["themeCollection"]["baseTheme"]["reportVersionAtImport"],
    }
    paquets = [p for p in rapport.get("resourcePackages", []) if p["name"] != "RegisteredResources"]
    paquets.append({
        "name": "RegisteredResources",
        "type": "RegisteredResources",
        "items": [{"name": "PonctualiteTheme", "path": "PonctualiteTheme.json", "type": "CustomTheme"}],
    })
    rapport["resourcePackages"] = paquets
    rapport["settings"] = {"useEnhancedTooltips": True}
    (RACINE / "definition" / "report.json").write_text(json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8")

    # pages: the single page of the first report is replaced
    ancienne = PAGES / "ReportSection1"
    if ancienne.exists():
        shutil.rmtree(ancienne)

    pages = [page_vue(), page_lignes(), page_carte(), page_methode()]
    for nom, affichage, visuels in pages:
        ecrire_page(nom, affichage, visuels)

    (PAGES / "pages.json").write_text(json.dumps({
        "$schema": SCHEMA_PAGES,
        "pageOrder": [p[0] for p in pages],
        "activePageName": pages[0][0],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print("rapport genere")


main()
