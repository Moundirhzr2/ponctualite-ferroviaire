"""Generate Ponctualite.Report/report.json for the PBIP project.

Power BI's report layout nests JSON inside JSON-encoded strings, so the layout
is described here as plain Python objects and serialised once, correctly,
rather than hand-escaped.

INCLUDE_VISUALS controls what is emitted. It defaults to False, and that is a
tested decision rather than a placeholder: the visual definitions below produce
structurally valid JSON that Power BI Desktop 2.157 nonetheless refuses to
render ("Une erreur s'est produite lors du rendu du rapport"). The private
layout schema evidently expects more than the documented shape, and guessing at
it is not a good use of time when dragging four visuals onto a canvas takes two
minutes.

So the report ships as a single empty page, which loads cleanly, and the model
underneath it arrives fully wired — tables, relationships, measures, and the
latitude/longitude data categories. Build the visuals in the UI following
docs/powerbi.md. The definitions below are kept for anyone who wants to take
the layout format further.

Run from anywhere:  python powerbi/generate_report.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT_JSON = HERE / "Ponctualite.Report" / "report.json"

PAGE_WIDTH, PAGE_HEIGHT = 1280, 720

# See the module docstring: the generated visuals do not render in Desktop 2.157.
INCLUDE_VISUALS = False

FACT = "fact_passage"
STATION = "dim_station"
ROUTE = "dim_route"


def source_ref(alias):
    return {"Expression": {"SourceRef": {"Source": alias}}}


def measure(alias, entity, name):
    return {"Measure": dict(source_ref(alias), Property=name), "Name": f"{entity}.{name}"}


def column(alias, entity, name):
    return {"Column": dict(source_ref(alias), Property=name), "Name": f"{entity}.{name}"}


def visual(name, visual_type, position, entities, select, projections, objects=None):
    """One visual container. `entities` maps alias -> table name."""
    single = {
        "visualType": visual_type,
        "projections": projections,
        "prototypeQuery": {
            "Version": 2,
            "From": [
                {"Name": alias, "Entity": entity, "Type": 0}
                for alias, entity in entities.items()
            ],
            "Select": select,
        },
        "drillFilterOtherVisuals": True,
    }
    if objects:
        single["objects"] = objects

    config = {
        "name": name,
        "layouts": [{"id": 0, "position": dict(position, z=position.get("z", 0))}],
        "singleVisual": single,
    }

    return dict(position, config=json.dumps(config), filters="[]")


def title(text):
    return {"title": [{"properties": {
        "show": {"expr": {"Literal": {"Value": "true"}}},
        "text": {"expr": {"Literal": {"Value": f"'{text}'"}}},
    }}]}


def build():
    cards = [
        ("Taux de ponctualite", "Ponctualite (< 5 min)"),
        ("Passages mesurables", "Passages mesures"),
        ("Retard moyen (min)", "Retard moyen (min)"),
        ("Trains supprimes", "Trains supprimes"),
    ]

    visuals = []
    for index, (measure_name, label) in enumerate(cards):
        visuals.append(visual(
            name=f"card{index}",
            visual_type="card",
            position={"x": 16 + index * 310, "y": 16, "width": 296, "height": 120},
            entities={"f": FACT},
            select=[measure("f", FACT, measure_name)],
            projections={"Values": [{"queryRef": f"{FACT}.{measure_name}"}]},
            objects=title(label),
        ))

    # Punctuality across the day.
    visuals.append(visual(
        name="hourly",
        visual_type="lineChart",
        position={"x": 16, "y": 152, "width": 620, "height": 280},
        entities={"f": FACT},
        select=[
            column("f", FACT, "scheduled_hour"),
            measure("f", FACT, "Taux de ponctualite"),
        ],
        projections={
            "Category": [{"queryRef": f"{FACT}.scheduled_hour"}],
            "Y": [{"queryRef": f"{FACT}.Taux de ponctualite"}],
        },
        objects=title("Ponctualite par heure theorique d'arrivee"),
    ))

    # Worst lines. Sorting and the >= 20 call filter are applied in the UI.
    visuals.append(visual(
        name="routes",
        visual_type="clusteredBarChart",
        position={"x": 652, "y": 152, "width": 612, "height": 280},
        entities={"f": FACT, "r": ROUTE},
        select=[
            column("r", ROUTE, "route_long_name"),
            measure("f", FACT, "Taux de ponctualite"),
        ],
        projections={
            "Category": [{"queryRef": f"{ROUTE}.route_long_name"}],
            "Y": [{"queryRef": f"{FACT}.Taux de ponctualite"}],
        },
        objects=title("Ponctualite par ligne"),
    ))

    # Geography: stop_name is categorised as Place, lat/lon carry the mapping.
    visuals.append(visual(
        name="stations",
        visual_type="map",
        position={"x": 16, "y": 448, "width": 620, "height": 256},
        entities={"f": FACT, "s": STATION},
        select=[
            column("s", STATION, "stop_name"),
            measure("f", FACT, "Passages mesurables"),
        ],
        projections={
            "Category": [{"queryRef": f"{STATION}.stop_name"}],
            "Size": [{"queryRef": f"{FACT}.Passages mesurables"}],
        },
        objects=title("Gares observees"),
    ))

    # Station detail as a table, easier to read than the map for ranking.
    visuals.append(visual(
        name="stationTable",
        visual_type="tableEx",
        position={"x": 652, "y": 448, "width": 612, "height": 256},
        entities={"f": FACT, "s": STATION},
        select=[
            column("s", STATION, "stop_name"),
            measure("f", FACT, "Passages mesurables"),
            measure("f", FACT, "Taux de ponctualite"),
        ],
        projections={"Values": [
            {"queryRef": f"{STATION}.stop_name"},
            {"queryRef": f"{FACT}.Passages mesurables"},
            {"queryRef": f"{FACT}.Taux de ponctualite"},
        ]},
        objects=title("Detail par gare"),
    ))

    section = {
        "name": "ReportSection1",
        "displayName": "Ponctualite",
        "filters": "[]",
        "ordinal": 0,
        "visualContainers": visuals if INCLUDE_VISUALS else [],
        "config": json.dumps({}),
        "displayOption": 1,
        "width": PAGE_WIDTH,
        "height": PAGE_HEIGHT,
    }

    # Power BI expects the baseline theme to be declared as a resource package;
    # a report.json without one fails to render even with no visuals on it.
    theme = {
        "resourcePackage": {
            "name": "SharedResources",
            "type": 2,
            "items": [
                {"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": 202}
            ],
            "disabled": False,
        }
    }

    return {
        "config": json.dumps({
            "version": "5.43",
            "themeCollection": {"baseTheme": {"name": "CY24SU10", "version": "5.43", "type": 2}},
            "activeSectionIndex": 0,
            "defaultDrillFilterOtherVisuals": True,
        }),
        "layoutOptimization": 0,
        "resourcePackages": [theme],
        "sections": [section],
    }


def main():
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(build(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {REPORT_JSON.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
