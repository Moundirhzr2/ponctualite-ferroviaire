"""Load every archived static SNCF GTFS version into historised reference tables.

The SNCF export is a rolling window: each daily version starts at its own
publication date and drops the trips before it. Loading only the newest version
silently orphans observations from earlier days. On 2026-09-12, replacing the
11 September export with the 12 September one dropped Friday's join rate from
99.85% to 94.45%, while the aggregate quality check, at 96.28%, let it through.

So versions accumulate instead of replacing each other. They are applied in
feed_version order; a trip carried by a newer version takes its whole definition
from that version (its stop pattern is replaced, not merged, so a removed stop
cannot linger); trips that later versions no longer carry are kept, because
observations still point at them.

Loading is incremental: versions already applied are recorded and skipped. A
version older than one already applied forces a full rebuild, since applying it
late would let stale definitions overwrite newer ones.

Versions live in data/gtfs/versions/, written there by download_gtfs.py.
"""

import csv
import io
import logging
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "data" / "gtfs" / "versions"
DB_PATH = ROOT / "data" / "punctuality.db"
BATCH = 20_000

REFERENCE_TABLES = ["gtfs_agency", "gtfs_route", "gtfs_trip", "gtfs_stop", "gtfs_stop_time"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS gtfs_agency (
    agency_id TEXT PRIMARY KEY, agency_name TEXT
);
CREATE TABLE IF NOT EXISTS gtfs_route (
    route_id TEXT PRIMARY KEY, agency_id TEXT, route_short_name TEXT,
    route_long_name TEXT, route_type INTEGER
);
CREATE TABLE IF NOT EXISTS gtfs_trip (
    trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT,
    trip_headsign TEXT, direction_id INTEGER, feed_version TEXT
);
CREATE TABLE IF NOT EXISTS gtfs_stop (
    stop_id TEXT PRIMARY KEY, stop_name TEXT, stop_lat REAL, stop_lon REAL,
    location_type INTEGER, parent_station TEXT
);
CREATE TABLE IF NOT EXISTS gtfs_stop_time (
    trip_id TEXT, stop_id TEXT, stop_sequence INTEGER,
    arrival_time TEXT, departure_time TEXT,
    PRIMARY KEY (trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS gtfs_stop_time_trip_stop ON gtfs_stop_time (trip_id, stop_id);
CREATE INDEX IF NOT EXISTS gtfs_trip_route ON gtfs_trip (route_id);
CREATE TABLE IF NOT EXISTS gtfs_version_applied (
    file TEXT PRIMARY KEY, feed_version TEXT, trips INTEGER, applied_at TEXT
);
"""


def rows(archive, member, fields):
    with archive.open(member) as handle:
        for row in csv.DictReader(io.TextIOWrapper(handle, "utf-8-sig")):
            # Empty GTFS fields become NULL rather than empty strings.
            yield tuple(row.get(field) or None for field in fields)


def batched(iterable):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


def feed_version(path):
    with zipfile.ZipFile(path) as archive:
        with archive.open("feed_info.txt") as handle:
            info = next(csv.DictReader(io.TextIOWrapper(handle, "utf-8-sig")))
    return info["feed_version"]


def is_legacy_schema(connection):
    """Tables from the pre-historisation loader have no primary keys to upsert on."""
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gtfs_trip'"
    ).fetchone()
    tracked = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gtfs_version_applied'"
    ).fetchone()
    return bool(exists) and not tracked


def reset(connection):
    for table in REFERENCE_TABLES + ["gtfs_version_applied"]:
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def apply_version(connection, path, version):
    with zipfile.ZipFile(path) as archive:
        for batch in batched(rows(archive, "agency.txt", ["agency_id", "agency_name"])):
            connection.executemany("INSERT OR REPLACE INTO gtfs_agency VALUES (?, ?)", batch)

        for batch in batched(rows(archive, "routes.txt", [
            "route_id", "agency_id", "route_short_name", "route_long_name", "route_type",
        ])):
            connection.executemany("INSERT OR REPLACE INTO gtfs_route VALUES (?, ?, ?, ?, ?)", batch)

        for batch in batched(rows(archive, "stops.txt", [
            "stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station",
        ])):
            connection.executemany("INSERT OR REPLACE INTO gtfs_stop VALUES (?, ?, ?, ?, ?, ?)", batch)

        connection.execute("DROP TABLE IF EXISTS temp.incoming_trip")
        connection.execute("CREATE TEMP TABLE incoming_trip (trip_id TEXT PRIMARY KEY)")
        trips = 0
        for batch in batched(rows(archive, "trips.txt", [
            "trip_id", "route_id", "service_id", "trip_headsign", "direction_id",
        ])):
            connection.executemany(
                "INSERT OR REPLACE INTO gtfs_trip VALUES (?, ?, ?, ?, ?, ?)",
                [row + (version,) for row in batch],
            )
            connection.executemany(
                "INSERT OR IGNORE INTO incoming_trip VALUES (?)", [(row[0],) for row in batch]
            )
            trips += len(batch)

        # Replace, never merge, the stop pattern of every trip this version defines.
        connection.execute(
            "DELETE FROM gtfs_stop_time WHERE trip_id IN (SELECT trip_id FROM incoming_trip)"
        )
        for batch in batched(rows(archive, "stop_times.txt", [
            "trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time",
        ])):
            connection.executemany("INSERT OR REPLACE INTO gtfs_stop_time VALUES (?, ?, ?, ?, ?)", batch)

        connection.execute("DROP TABLE temp.incoming_trip")

    connection.execute(
        "INSERT OR REPLACE INTO gtfs_version_applied VALUES (?, ?, ?, ?)",
        (path.name, version, trips, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    return trips


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    available = sorted(
        ((feed_version(path), path) for path in VERSIONS.glob("*.zip")),
        key=lambda item: (item[0], item[1].name),
    )
    if not available:
        logging.error("no GTFS version in %s - run download_gtfs.py first", VERSIONS)
        return 1

    # A long timeout lets the scheduled ingester wait for this load instead of failing.
    connection = sqlite3.connect(DB_PATH, timeout=120)
    connection.execute("PRAGMA journal_mode = WAL")

    if is_legacy_schema(connection):
        logging.info("legacy reference tables found, rebuilding from all versions")
        reset(connection)
    connection.executescript(SCHEMA)

    applied = {
        name: version
        for name, version in connection.execute("SELECT file, feed_version FROM gtfs_version_applied")
    }
    pending = [(version, path) for version, path in available if path.name not in applied]
    newest_applied = max(applied.values(), default="")

    if any(version < newest_applied for version, _ in pending):
        logging.info("a version older than one already applied appeared, rebuilding")
        reset(connection)
        connection.executescript(SCHEMA)
        pending = available

    if not pending:
        logging.info("all %d GTFS versions already applied", len(available))

    for version, path in pending:
        with connection:  # one transaction per version keeps the write lock short
            trips = apply_version(connection, path, version)
        logging.info("applied %s (feed_version %s, %d trips)", path.name, version, trips)

    for table in REFERENCE_TABLES:
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        logging.info("%-16s %8d rows", table, count)

    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
