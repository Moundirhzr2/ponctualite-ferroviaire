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
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "punctuality.db"

PUNCTUALITY_THRESHOLD_S = 300

PARIS = ZoneInfo("Europe/Paris")
# Must match the SncfRTIngest scheduled task interval.
COLLECTION_INTERVAL_MIN = 5
EXPECTED_RUNS_PER_HOUR = 60 // COLLECTION_INTERVAL_MIN
# An hour counts as watched when the collector ran for at least half of it.
MIN_COVERAGE_PCT = 50

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
        MAX(o.arrival_time)                        AS arrival_time,
        -- Has this call actually happened yet? arrival_time and observed_at are
        -- both absolute unix timestamps from the feed, so this needs no timezone
        -- arithmetic. A rate computed over calls that are still in the future is
        -- measuring predictions rather than outcomes; the two populations differ
        -- measurably, so they are separable rather than silently pooled.
        CASE
            WHEN MAX(o.arrival_time) IS NULL                THEN 0
            WHEN MAX(o.arrival_time) <= MAX(o.observed_at)  THEN 1
            ELSE 0
        END                                        AS is_past,
        MAX(o.observed_at)                         AS observed_at
    FROM observation o
    JOIN gtfs_trip t      ON t.trip_id = o.trip_id
    JOIN gtfs_stop_time st ON st.trip_id = o.trip_id AND st.stop_id = o.stop_id
    GROUP BY o.service_date, o.trip_id, o.stop_id
    """,

    "CREATE INDEX fact_passage_date ON fact_passage (service_date)",
    "CREATE INDEX fact_passage_route ON fact_passage (route_id)",
    "CREATE INDEX fact_passage_stop ON fact_passage (stop_id)",
]


def build_collection_coverage(connection):
    """Record, for every Paris clock hour, how much of it the collector observed.

    The collector runs on a laptop and stops whenever the machine sleeps. A
    silent gap is indistinguishable from a quiet network: on 2026-09-12 it was
    off from 08:07 to 20:26, and the hourly chart showed that as a handful of
    trains rather than as missing data. Every hour of every collected day gets a
    row, and an hour the collector missed is an explicit zero rather than an
    absent row, so a gap cannot pass for a measurement.

    Runs are bucketed in Europe/Paris time through tzdata, not a fixed offset,
    so the buckets stay right across the October and March clock changes.
    """
    runs = [
        datetime.fromisoformat(started).astimezone(PARIS)
        for (started,) in connection.execute("SELECT started_at FROM ingest_run")
    ]
    connection.execute("DROP TABLE IF EXISTS dim_collection_hour")
    connection.execute("""
        CREATE TABLE dim_collection_hour (
            service_date TEXT    NOT NULL,
            hour         INTEGER NOT NULL,
            runs         INTEGER NOT NULL,
            coverage_pct REAL    NOT NULL,
            PRIMARY KEY (service_date, hour)
        )""")
    if not runs:
        return 0

    counts = Counter((run.date(), run.hour) for run in runs)
    first, last = min(runs).date(), max(runs).date()
    rows = []
    day = first
    while day <= last:
        for hour in range(24):
            observed = counts.get((day, hour), 0)
            coverage = min(100.0, 100.0 * observed / EXPECTED_RUNS_PER_HOUR)
            rows.append((day.isoformat(), hour, observed, round(coverage, 1)))
        day += timedelta(days=1)

    connection.executemany("INSERT INTO dim_collection_hour VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def flag_collected_calls(connection):
    """Mark the calls whose scheduled hour the collector actually watched.

    A call observed only after the fact is a biased sample, not a random one. The
    feed drops a trip once it completes, so a call from an hour the collector
    missed is visible only if its trip was still running when collection resumed
    — which selects long, and often late, trains. On 2026-09-11, with collection
    starting at 18:50, every realised call at 14h came from a trip over three
    hours long (mean 378 min) against 94 min at 18h and 19h, and the 14h rate of
    75.6% described those trains rather than the network at 14h.

    The rule is judged per calendar day and hour, never averaged across days.
    GTFS times past 24:00 belong to the following calendar day, so the day is
    shifted before matching against the collector's Paris-time buckets.
    """
    connection.execute("ALTER TABLE fact_passage ADD COLUMN is_collected INTEGER NOT NULL DEFAULT 0")
    connection.execute("""
        UPDATE fact_passage SET is_collected = 1
        WHERE EXISTS (
            SELECT 1 FROM dim_collection_hour h
            WHERE h.service_date = date(fact_passage.service_date,
                                        '+' || (fact_passage.scheduled_minutes / 1440) || ' days')
              AND h.hour = fact_passage.scheduled_hour
              AND h.coverage_pct >= ?
        )""", (MIN_COVERAGE_PCT,))


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    # Waits for the scheduled ingester rather than failing on SQLite's single writer lock.
    connection = sqlite3.connect(DB_PATH, timeout=120)
    with connection:
        for statement in STATEMENTS:
            connection.execute(statement)
        hours = build_collection_coverage(connection)
        logging.info("%-19s %8d rows", "dim_collection_hour", hours)
        flag_collected_calls(connection)

        for table in ("dim_station", "dim_route", "fact_passage"):
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            logging.info("%-14s %8d rows", table, count)

        measurable, collected = connection.execute(
            "SELECT COUNT(*), SUM(is_collected = 1 AND is_past = 1) "
            "FROM fact_passage WHERE is_punctual IS NOT NULL"
        ).fetchone()
        logging.info("%-14s %8d rows usable for punctuality", "", measurable)
        logging.info("%-14s %8d of them realised in an hour the collector watched", "", collected or 0)

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
