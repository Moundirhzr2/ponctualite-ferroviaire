"""Build the star schema Power BI reads from.

fact_passage is one row per observed call at a station, carrying the delay
alongside the theoretical arrival it is measured against. dim_station and
dim_route hold the labels and coordinates. Tables are materialised rather than
left as views so the report reads them without re-running the joins.

Method notes, which matter for how the figures should be read:

* Only trips the schedule knows about can be measured. Trips flagged ADDED have
  no theoretical time by construction and are excluded from the fact table.
* Cancelled trips are kept in the fact table but excluded from the punctuality
  indicators: a cancelled train is not a late train, and folding it into an
  average silently flatters the result.
* Punctuality threshold is 5 minutes at arrival, applied per call at a station,
  not per trip.
"""

import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "punctuality.db"

PUNCTUALITY_THRESHOLD_S = 300

# GTFS times run past 24:00:00 for trips continuing after midnight, so the hour
# is parsed off the string rather than read as a clock time.
SCHEDULED_MINUTES = """
    CAST(substr(st.arrival_time, 1, instr(st.arrival_time, ':') - 1) AS INTEGER) * 60
  + CAST(substr(st.arrival_time, instr(st.arrival_time, ':') + 1, 2) AS INTEGER)
"""

STATEMENTS = [
    "DROP TABLE IF EXISTS dim_station",
    """
    CREATE TABLE dim_station AS
    SELECT stop_id, stop_name, stop_lat, stop_lon, parent_station
    FROM gtfs_stop
    """,

    "DROP TABLE IF EXISTS dim_route",
    """
    CREATE TABLE dim_route AS
    SELECT r.route_id,
           r.route_short_name,
           r.route_long_name,
           CASE r.route_type
               WHEN 0 THEN 'Tram'
               WHEN 2 THEN 'Train'
               WHEN 3 THEN 'Autocar'
               ELSE 'Autre'
           END AS mode,
           a.agency_name
    FROM gtfs_route r
    LEFT JOIN gtfs_agency a ON a.agency_id = r.agency_id
    """,

    "DROP TABLE IF EXISTS fact_passage",
    f"""
    CREATE TABLE fact_passage AS
    SELECT
        o.service_date,
        o.trip_id,
        t.route_id,
        o.stop_id,
        MIN(st.stop_sequence)                      AS stop_sequence,
        MIN(st.arrival_time)                       AS scheduled_arrival,
        MIN({SCHEDULED_MINUTES})                   AS scheduled_minutes,
        (MIN({SCHEDULED_MINUTES}) / 60) % 24       AS scheduled_hour,
        o.arrival_delay                            AS arrival_delay_s,
        o.departure_delay                          AS departure_delay_s,
        o.schedule_relationship,
        CASE
            WHEN o.schedule_relationship = 'CANCELED' THEN NULL
            WHEN o.arrival_delay IS NULL              THEN NULL
            WHEN o.arrival_delay <= {PUNCTUALITY_THRESHOLD_S} THEN 1
            ELSE 0
        END                                        AS is_punctual,
        o.observed_at
    FROM observation o
    JOIN gtfs_trip t      ON t.trip_id = o.trip_id
    JOIN gtfs_stop_time st ON st.trip_id = o.trip_id AND st.stop_id = o.stop_id
    GROUP BY o.service_date, o.trip_id, o.stop_id
    """,

    "CREATE INDEX fact_passage_date ON fact_passage (service_date)",
    "CREATE INDEX fact_passage_route ON fact_passage (route_id)",
    "CREATE INDEX fact_passage_stop ON fact_passage (stop_id)",
]


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    connection = sqlite3.connect(DB_PATH)
    with connection:
        for statement in STATEMENTS:
            connection.execute(statement)

        for table in ("dim_station", "dim_route", "fact_passage"):
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            logging.info("%-14s %8d rows", table, count)

        measurable = connection.execute(
            "SELECT COUNT(*) FROM fact_passage WHERE is_punctual IS NOT NULL"
        ).fetchone()[0]
        logging.info("%-14s %8d rows usable for punctuality", "", measurable)

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
