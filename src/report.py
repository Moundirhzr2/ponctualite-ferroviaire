"""Print the punctuality indicators from the star schema.

A console counterpart to the Power BI report, useful for sanity-checking the
figures before they are charted. Cancelled trips are excluded throughout: the
fact table leaves is_punctual NULL for them.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "punctuality.db"

MIN_CALLS = 20  # below this a station's rate is noise, not a signal

# Rough bounding box for the Grand Est region.
GRAND_EST = (47.4, 49.7, 4.9, 8.35)


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def scalar(connection, sql, params=()):
    return connection.execute(sql, params).fetchone()


def percentile(connection, fraction):
    """SQLite has no percentile function; take the nth row of an ordered scan.

    The mean is a poor summary here: a handful of very late trains pull it well
    above what most passengers experience, while the median hides the tail
    entirely. Reporting both, plus p90, describes the distribution honestly.
    """
    row = connection.execute("""
        SELECT arrival_delay_s FROM fact_passage
        WHERE is_punctual IS NOT NULL
        ORDER BY arrival_delay_s
        LIMIT 1 OFFSET CAST(
            (SELECT COUNT(*) FROM fact_passage WHERE is_punctual IS NOT NULL) * ? AS INTEGER
        )""", (fraction,)).fetchone()
    return row[0] if row else None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    connection = sqlite3.connect(DB_PATH)

    section("Perimetre")
    dates = connection.execute(
        "SELECT service_date, COUNT(*) FROM fact_passage GROUP BY 1 ORDER BY 1"
    ).fetchall()
    for date, count in dates:
        print(f"  {date}  {count:,} passages")
    total, measurable, cancelled = scalar(connection, """
        SELECT COUNT(*),
               SUM(is_punctual IS NOT NULL),
               SUM(schedule_relationship = 'CANCELED')
        FROM fact_passage""")
    print(f"  total {total:,} passages, {measurable:,} mesurables, {cancelled:,} supprimes")

    section("Ponctualite globale (arrivee a moins de 5 min)")
    rate, avg_delay, worst = scalar(connection, """
        SELECT 100.0 * AVG(is_punctual), AVG(arrival_delay_s), MAX(arrival_delay_s)
        FROM fact_passage WHERE is_punctual IS NOT NULL""")
    print(f"  taux           : {rate:.1f}%")
    print(f"  retard moyen   : {avg_delay / 60:.1f} min")
    print(f"  mediane        : {percentile(connection, 0.50) / 60:.1f} min")
    print(f"  p90            : {percentile(connection, 0.90) / 60:.1f} min")
    print(f"  p99            : {percentile(connection, 0.99) / 60:.1f} min")
    print(f"  retard maximal : {worst // 60} min")

    section("Par tranche horaire")
    for hour, calls, rate in connection.execute("""
        SELECT scheduled_hour, COUNT(*), 100.0 * AVG(is_punctual)
        FROM fact_passage WHERE is_punctual IS NOT NULL
        GROUP BY 1 HAVING COUNT(*) >= ? ORDER BY 1""", (MIN_CALLS,)):
        bar = "#" * int(round((100 - rate) / 2))
        print(f"  {hour:02d}h  {calls:5,} passages  {rate:5.1f}%  {bar}")

    section(f"Gares les moins ponctuelles (min. {MIN_CALLS} passages)")
    for name, calls, rate, avg in connection.execute("""
        SELECT s.stop_name, COUNT(*), 100.0 * AVG(f.is_punctual), AVG(f.arrival_delay_s)
        FROM fact_passage f JOIN dim_station s ON s.stop_id = f.stop_id
        WHERE f.is_punctual IS NOT NULL
        GROUP BY s.stop_name HAVING COUNT(*) >= ?
        ORDER BY 3 ASC LIMIT 10""", (MIN_CALLS,)):
        print(f"  {name[:34]:36} {calls:4,} passages  {rate:5.1f}%  moy {avg / 60:5.1f} min")

    section(f"Lignes les moins ponctuelles (min. {MIN_CALLS} passages)")
    for name, mode, calls, rate in connection.execute("""
        SELECT r.route_long_name, r.mode, COUNT(*), 100.0 * AVG(f.is_punctual)
        FROM fact_passage f JOIN dim_route r ON r.route_id = f.route_id
        WHERE f.is_punctual IS NOT NULL
        GROUP BY r.route_id HAVING COUNT(*) >= ?
        ORDER BY 4 ASC LIMIT 10""", (MIN_CALLS,)):
        print(f"  {name[:38]:40} {mode:8} {calls:4,}  {rate:5.1f}%")

    section("Focus Grand Est")
    lat_min, lat_max, lon_min, lon_max = GRAND_EST
    row = scalar(connection, """
        SELECT COUNT(*), 100.0 * AVG(f.is_punctual), AVG(f.arrival_delay_s)
        FROM fact_passage f JOIN dim_station s ON s.stop_id = f.stop_id
        WHERE f.is_punctual IS NOT NULL
          AND s.stop_lat BETWEEN ? AND ? AND s.stop_lon BETWEEN ? AND ?""",
        (lat_min, lat_max, lon_min, lon_max))
    calls, rate, avg = row
    if calls:
        print(f"  {calls:,} passages  {rate:.1f}% ponctuels  retard moyen {avg / 60:.1f} min")

    print()
    for name, calls, rate in connection.execute("""
        SELECT s.stop_name, COUNT(*), 100.0 * AVG(f.is_punctual)
        FROM fact_passage f JOIN dim_station s ON s.stop_id = f.stop_id
        WHERE f.is_punctual IS NOT NULL AND s.stop_name LIKE '%Mulhouse%'
        GROUP BY s.stop_name ORDER BY 2 DESC LIMIT 5"""):
        print(f"  {name[:34]:36} {calls:4,} passages  {rate:5.1f}%")

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
