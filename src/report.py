"""Print the punctuality indicators from the star schema.

A console counterpart to the Power BI report, used to sanity-check the figures
before they are charted.

Every rate here is computed on one population: calls actually observed
happening. That means already realised (is_past) and scheduled in an hour the
collector was watching (is_collected), with cancelled trips excluded through
is_punctual being NULL. Calls seen only after the fact are a survivorship
sample: the feed drops finished trips, so what remains visible is long and
often late trains. Pooling them in moved the worst-line ranking substantially,
so they are shown as context only, never ranked. The Power BI measure
'Taux ponctualite observe' applies the same rule.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "punctuality.db"

MIN_CALLS = 20  # below this a rate is noise, not a signal
MIN_COVERAGE_PCT = 50  # keep equal to build_marts.MIN_COVERAGE_PCT; used only to count covered days

# Rough bounding box for the Grand Est region.
GRAND_EST = (47.4, 49.7, 4.9, 8.35)

# The one population every rate is computed on. Prefix with the fact alias.
OBSERVED = "{f}is_punctual IS NOT NULL AND {f}is_past = 1 AND {f}is_collected = 1"


def observed(alias=""):
    return OBSERVED.format(f=f"{alias}." if alias else "")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def scalar(connection, sql, params=()):
    return connection.execute(sql, params).fetchone()


def percentile(connection, fraction):
    """SQLite has no percentile function; take the nth row of an ordered scan.

    The mean is a poor summary here: a handful of very late trains pull it well
    above what most passengers experience, while the median hides the tail
    entirely. Reporting both, plus p90 and p99, describes the distribution.
    """
    row = connection.execute(f"""
        SELECT arrival_delay_s FROM fact_passage
        WHERE {observed()}
        ORDER BY arrival_delay_s
        LIMIT 1 OFFSET CAST(
            (SELECT COUNT(*) FROM fact_passage WHERE {observed()}) * ? AS INTEGER
        )""", (fraction,)).fetchone()
    return row[0] if row else None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    connection = sqlite3.connect(DB_PATH)

    section("Perimetre")
    for date, count, watched in connection.execute("""
        SELECT service_date, COUNT(*), SUM(is_collected = 1 AND is_past = 1)
        FROM fact_passage GROUP BY 1 ORDER BY 1"""):
        print(f"  {date}  {count:,} passages, dont {watched:,} observes pendant une heure surveillee")
    total, measurable, observed_calls, cancelled = scalar(connection, f"""
        SELECT COUNT(*),
               SUM(is_punctual IS NOT NULL),
               SUM({observed()}),
               SUM(schedule_relationship = 'CANCELED')
        FROM fact_passage""")
    print(f"  total {total:,} passages, {measurable:,} mesurables, "
          f"{observed_calls:,} observes, {cancelled:,} supprimes")

    section("Ponctualite globale (arrivee a moins de 5 min, passages observes)")
    rate, avg_delay, worst = scalar(connection, f"""
        SELECT 100.0 * AVG(is_punctual), AVG(arrival_delay_s), MAX(arrival_delay_s)
        FROM fact_passage WHERE {observed()}""")
    print(f"  taux           : {rate:.1f}%  sur {observed_calls:,} passages observes")
    print(f"  retard moyen   : {avg_delay / 60:.1f} min")
    print(f"  mediane        : {percentile(connection, 0.50) / 60:.1f} min")
    print(f"  p90            : {percentile(connection, 0.90) / 60:.1f} min")
    print(f"  p99            : {percentile(connection, 0.99) / 60:.1f} min")
    print(f"  retard maximal : {worst // 60} min")

    # Context only: the populations the headline deliberately leaves out.
    print("\n  pour comparaison, populations exclues du taux :")
    for label, where in [
        ("tous passages mesurables     ", "is_punctual IS NOT NULL"),
        ("vus apres coup (heure manquee)", "is_punctual IS NOT NULL AND is_past = 1 AND is_collected = 0"),
        ("encore a venir (prevision)   ", "is_punctual IS NOT NULL AND is_past = 0"),
    ]:
        calls, share = scalar(connection, f"SELECT COUNT(*), 100.0 * AVG(is_punctual) FROM fact_passage WHERE {where}")
        if calls:
            print(f"    {label} {share:5.1f}% sur {calls:,}")

    total_days = connection.execute(
        "SELECT COUNT(DISTINCT service_date) FROM dim_collection_hour"
    ).fetchone()[0]
    section("Par tranche horaire (passages observes)")
    print("  un passage ne compte que si le collecteur tournait a son heure, ce jour-la")
    # Driven by dim_collection_hour so an hour no day covered still prints.
    for hour, covered_days, calls, rate in connection.execute(f"""
        SELECT h.hour,
               (SELECT COUNT(*) FROM dim_collection_hour c
                 WHERE c.hour = h.hour AND c.coverage_pct >= ?),
               (SELECT COUNT(*) FROM fact_passage f
                 WHERE f.scheduled_hour = h.hour AND {observed('f')}),
               (SELECT 100.0 * AVG(f.is_punctual) FROM fact_passage f
                 WHERE f.scheduled_hour = h.hour AND {observed('f')})
        FROM (SELECT DISTINCT hour FROM dim_collection_hour) h
        ORDER BY h.hour""", (MIN_COVERAGE_PCT,)):
        if covered_days == 0:
            note, shown = "aucune journee collectee a cette heure", "   -  "
        elif calls < MIN_CALLS:
            note, shown = "trop peu de passages", "   -  "
        else:
            note, shown = "#" * int(round((100 - rate) / 2)), f"{rate:5.1f}%"
        print(f"  {hour:02d}h  {covered_days}/{total_days} jour(s)  {calls:5,} passages  {shown}  {note}")

    section(f"Gares les moins ponctuelles (passages observes, min. {MIN_CALLS})")
    for name, calls, rate, avg in connection.execute(f"""
        SELECT MIN(s.stop_name), COUNT(*), 100.0 * AVG(f.is_punctual), AVG(f.arrival_delay_s)
        FROM fact_passage f JOIN dim_station s ON s.stop_id = f.stop_id
        WHERE {observed('f')}
        GROUP BY s.stop_name COLLATE NOCASE HAVING COUNT(*) >= ?
        ORDER BY 3 ASC LIMIT 10""", (MIN_CALLS,)):
        print(f"  {name[:34]:36} {calls:4,} passages  {rate:5.1f}%  moy {avg / 60:5.1f} min")

    section(f"Lignes les moins ponctuelles (passages observes, min. {MIN_CALLS})")
    # Grouped by line name, not route_id. SNCF splits some lines across several
    # route_ids; ranking the fragments separately let a small, late fragment fall
    # under the call threshold and disappear, flattering the line. It also made
    # this ranking disagree with Power BI, whose axis is the line name. Names are
    # also compared case-insensitively, as Power BI groups text: SNCF spells some
    # lines both "STRASBOURG - NIEDERBRONN..." and "Strasbourg - Niederbronn...".
    for name, modes, calls, rate in connection.execute(f"""
        SELECT MIN(r.route_long_name), GROUP_CONCAT(DISTINCT r.mode), COUNT(*), 100.0 * AVG(f.is_punctual)
        FROM fact_passage f JOIN dim_route r ON r.route_id = f.route_id
        WHERE {observed('f')}
        GROUP BY r.route_long_name COLLATE NOCASE HAVING COUNT(*) >= ?
        ORDER BY 4 ASC LIMIT 10""", (MIN_CALLS,)):
        print(f"  {name[:38]:40} {modes[:14]:14} {calls:4,}  {rate:5.1f}%")

    section("Focus Grand Est (passages observes)")
    lat_min, lat_max, lon_min, lon_max = GRAND_EST
    calls, rate, avg = scalar(connection, f"""
        SELECT COUNT(*), 100.0 * AVG(f.is_punctual), AVG(f.arrival_delay_s)
        FROM fact_passage f JOIN dim_station s ON s.stop_id = f.stop_id
        WHERE {observed('f')}
          AND s.stop_lat BETWEEN ? AND ? AND s.stop_lon BETWEEN ? AND ?""",
        (lat_min, lat_max, lon_min, lon_max))
    if calls:
        print(f"  {calls:,} passages  {rate:.1f}% ponctuels  retard moyen {avg / 60:.1f} min")

    print()
    for name, calls, rate in connection.execute(f"""
        SELECT s.stop_name, COUNT(*), 100.0 * AVG(f.is_punctual)
        FROM fact_passage f JOIN dim_station s ON s.stop_id = f.stop_id
        WHERE {observed('f')} AND s.stop_name LIKE '%Mulhouse%'
        GROUP BY s.stop_name ORDER BY 2 DESC LIMIT 5"""):
        flag = "" if calls >= MIN_CALLS else "  (trop peu de passages)"
        print(f"  {name[:34]:36} {calls:4,} passages  {rate:5.1f}%{flag}")

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
