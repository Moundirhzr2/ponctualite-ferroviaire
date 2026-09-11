"""Load the static SNCF GTFS archive into SQLite reference tables.

The real-time feed carries delays but no schedule and no names: a stop arrives
as an opaque id, and `stop_sequence` is never populated. Everything needed to
turn an observation into an analysable passage — the theoretical arrival time,
the position of the stop along the trip, the station name and its coordinates —
comes from here.

Re-run whenever the static archive is refreshed; tables are replaced wholesale.
"""

import csv
import io
import logging
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GTFS_ZIP = ROOT / "data" / "gtfs" / "sncf-gtfs.zip"
DB_PATH = ROOT / "data" / "punctuality.db"
BATCH = 20_000

# table -> (member file, [(column, sql type, gtfs field)])
SPECS = {
    "gtfs_agency": ("agency.txt", [
        ("agency_id", "TEXT", "agency_id"),
        ("agency_name", "TEXT", "agency_name"),
    ]),
    "gtfs_route": ("routes.txt", [
        ("route_id", "TEXT", "route_id"),
        ("agency_id", "TEXT", "agency_id"),
        ("route_short_name", "TEXT", "route_short_name"),
        ("route_long_name", "TEXT", "route_long_name"),
        ("route_type", "INTEGER", "route_type"),
    ]),
    "gtfs_trip": ("trips.txt", [
        ("trip_id", "TEXT", "trip_id"),
        ("route_id", "TEXT", "route_id"),
        ("service_id", "TEXT", "service_id"),
        ("trip_headsign", "TEXT", "trip_headsign"),
        ("direction_id", "INTEGER", "direction_id"),
    ]),
    "gtfs_stop": ("stops.txt", [
        ("stop_id", "TEXT", "stop_id"),
        ("stop_name", "TEXT", "stop_name"),
        ("stop_lat", "REAL", "stop_lat"),
        ("stop_lon", "REAL", "stop_lon"),
        ("location_type", "INTEGER", "location_type"),
        ("parent_station", "TEXT", "parent_station"),
    ]),
    "gtfs_stop_time": ("stop_times.txt", [
        ("trip_id", "TEXT", "trip_id"),
        ("stop_id", "TEXT", "stop_id"),
        ("stop_sequence", "INTEGER", "stop_sequence"),
        ("arrival_time", "TEXT", "arrival_time"),
        ("departure_time", "TEXT", "departure_time"),
    ]),
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS gtfs_stop_time_trip_stop ON gtfs_stop_time (trip_id, stop_id)",
    "CREATE INDEX IF NOT EXISTS gtfs_trip_route ON gtfs_trip (route_id)",
]


def load_table(connection, archive, table, member, columns):
    names = [name for name, _, _ in columns]
    definition = ", ".join(f"{name} {sql_type}" for name, sql_type, _ in columns)
    placeholders = ", ".join("?" * len(columns))

    connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute(f"CREATE TABLE {table} ({definition})")

    insert = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})"
    total = 0
    with archive.open(member) as handle:
        reader = csv.DictReader(io.TextIOWrapper(handle, "utf-8-sig"))
        batch = []
        for row in reader:
            # Empty GTFS fields become NULL rather than empty strings.
            batch.append(tuple(row.get(field) or None for _, _, field in columns))
            if len(batch) >= BATCH:
                connection.executemany(insert, batch)
                total += len(batch)
                batch = []
        if batch:
            connection.executemany(insert, batch)
            total += len(batch)

    return total


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    if not GTFS_ZIP.exists():
        logging.error("missing %s - download the static GTFS first", GTFS_ZIP)
        return 1

    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = OFF")  # bulk load, recreated on demand

    with zipfile.ZipFile(GTFS_ZIP) as archive, connection:
        for table, (member, columns) in SPECS.items():
            count = load_table(connection, archive, table, member, columns)
            logging.info("%-16s %8d rows", table, count)
        for statement in INDEXES:
            connection.execute(statement)

    connection.close()
    logging.info("loaded into %s", DB_PATH.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
