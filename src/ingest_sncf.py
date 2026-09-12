"""Ingest the SNCF GTFS-RT trip updates into a local SQLite database.

Storage design: the feed is ~1.6 MB per call, so archiving raw payloads every
two minutes would cost roughly 0.8 GB per service day. Instead the payload is
decoded on arrival and only the stop-level observations are kept, upserted on
(service_date, trip_id, stop_id, stop_sequence). Successive calls overwrite the
same row with a fresher reading, so the table converges to one row per scheduled
stop per day holding the last delay observed before the train actually passed —
the best available estimate of the realized delay.

SQLite keeps the project runnable with no server. The schema is plain SQL and
the connection is created in one place, so moving to PostgreSQL later is a
localised change.
"""

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.transit import gtfs_realtime_pb2

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "punctuality.db"
RUN_LOG = ROOT / "data" / "ingest.log"
RT_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"

RELATIONSHIP = {
    0: "SCHEDULED", 1: "ADDED", 2: "UNSCHEDULED",
    3: "CANCELED", 5: "REPLACEMENT", 6: "DUPLICATED", 7: "DELETED",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS observation (
    service_date          TEXT    NOT NULL,
    trip_id               TEXT    NOT NULL,
    stop_id               TEXT    NOT NULL,
    stop_sequence         INTEGER NOT NULL,
    route_id              TEXT,
    schedule_relationship TEXT,
    arrival_delay         INTEGER,
    departure_delay       INTEGER,
    arrival_time          INTEGER,
    departure_time        INTEGER,
    observed_at           INTEGER NOT NULL,
    PRIMARY KEY (service_date, trip_id, stop_id, stop_sequence)
);

CREATE INDEX IF NOT EXISTS observation_date_route
    ON observation (service_date, route_id);

CREATE TABLE IF NOT EXISTS ingest_run (
    started_at        TEXT PRIMARY KEY,
    header_timestamp  INTEGER,
    payload_bytes     INTEGER,
    entities          INTEGER,
    stop_time_updates INTEGER,
    rows_written      INTEGER
);
"""

UPSERT = """
INSERT INTO observation (
    service_date, trip_id, stop_id, stop_sequence, route_id,
    schedule_relationship, arrival_delay, departure_delay,
    arrival_time, departure_time, observed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (service_date, trip_id, stop_id, stop_sequence) DO UPDATE SET
    route_id              = excluded.route_id,
    schedule_relationship = excluded.schedule_relationship,
    arrival_delay         = excluded.arrival_delay,
    departure_delay       = excluded.departure_delay,
    arrival_time          = excluded.arrival_time,
    departure_time        = excluded.departure_time,
    observed_at           = excluded.observed_at
WHERE excluded.observed_at > observation.observed_at
"""


def setup_logging():
    """Log to a file always, and to the console only when there is one."""
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(RUN_LOG, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # SQLite allows one writer. The default 5 s wait makes a scheduled run fail
    # outright if it lands during a reference reload; waiting it out is cheaper
    # than losing five minutes of observations.
    connection = sqlite3.connect(DB_PATH, timeout=120)
    connection.executescript(SCHEMA)
    return connection


def optional(message, field):
    """GTFS-RT leaves unset numeric fields at 0; distinguish that from a real 0."""
    return getattr(message, field) if message.HasField(field) else None


def rows_from(feed, fallback_date):
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue

        update = entity.trip_update
        trip = update.trip
        service_date = trip.start_date or fallback_date
        if len(service_date) == 8:  # YYYYMMDD -> YYYY-MM-DD
            service_date = f"{service_date[:4]}-{service_date[4:6]}-{service_date[6:]}"

        relationship = RELATIONSHIP.get(trip.schedule_relationship, "UNKNOWN")

        for stop_time in update.stop_time_update:
            arrival = stop_time.arrival if stop_time.HasField("arrival") else None
            departure = stop_time.departure if stop_time.HasField("departure") else None

            yield (
                service_date,
                trip.trip_id,
                stop_time.stop_id,
                stop_time.stop_sequence,
                trip.route_id or None,
                relationship,
                optional(arrival, "delay") if arrival else None,
                optional(departure, "delay") if departure else None,
                optional(arrival, "time") if arrival else None,
                optional(departure, "time") if departure else None,
                feed.header.timestamp,
            )


def main():
    setup_logging()

    started = datetime.now(timezone.utc)
    try:
        response = requests.get(RT_URL, timeout=90)
        response.raise_for_status()
    except Exception as error:
        logging.error("fetch failed: %s", error)
        return 1

    payload = response.content
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(payload)
    except Exception as error:
        logging.error("could not parse feed (%d bytes): %s", len(payload), error)
        return 1

    rows = list(rows_from(feed, started.strftime("%Y%m%d")))

    connection = connect()
    with connection:
        before = connection.total_changes
        connection.executemany(UPSERT, rows)
        written = connection.total_changes - before
        connection.execute(
            "INSERT OR REPLACE INTO ingest_run VALUES (?, ?, ?, ?, ?, ?)",
            (
                started.isoformat(timespec="seconds"),
                feed.header.timestamp,
                len(payload),
                len(feed.entity),
                len(rows),
                written,
            ),
        )
    connection.close()

    logging.info(
        "%d entities, %d stop-time updates, %d rows written, %.2f MB",
        len(feed.entity), len(rows), written, len(payload) / 1048576,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
