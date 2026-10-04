"""Shared fixtures: an in-memory database with the pipeline's schema, and helpers
that build the smallest inputs each rule needs.

The tests never touch data/punctuality.db or the network. Every rule under test
takes a connection or a path as an argument, so a few hand-written rows are
enough to pin down behaviour that the real data only exercises by accident.
"""

import csv
import io
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

import build_marts
import ingest_sncf
import load_gtfs


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.executescript(ingest_sncf.SCHEMA)
    connection.executescript(load_gtfs.SCHEMA)
    yield connection
    connection.close()


def observe(connection, *, trip="T1", stop="S1", sequence=1, delay=0, arrival_time=1_000,
            observed_at=2_000, service_date="2026-09-14", relationship="SCHEDULED"):
    """Insert one observation through the collector's own upsert."""
    before = connection.total_changes
    connection.execute(ingest_sncf.UPSERT, (
        service_date, trip, stop, sequence, None, relationship,
        delay, None, arrival_time, None, observed_at,
    ))
    return connection.total_changes - before


def reference(connection, *, trips, route_type=2):
    """Static timetable: {trip_id: [(stop_id, "HH:MM:SS"), ...]} on one route."""
    connection.execute("INSERT OR IGNORE INTO gtfs_agency VALUES ('A', 'SNCF')")
    connection.execute(
        "INSERT OR IGNORE INTO gtfs_route VALUES ('R1', 'A', 'L1', 'Mulhouse - Bâle', ?)",
        (route_type,),
    )
    for trip_id, calls in trips.items():
        connection.execute(
            "INSERT INTO gtfs_trip VALUES (?, 'R1', 'SVC', NULL, 0, 'v1')", (trip_id,)
        )
        for sequence, (stop_id, arrival) in enumerate(calls, start=1):
            connection.execute(
                "INSERT OR IGNORE INTO gtfs_stop VALUES (?, ?, 47.7, 7.3, 0, NULL)",
                (stop_id, f"Gare {stop_id}"),
            )
            connection.execute(
                "INSERT INTO gtfs_stop_time VALUES (?, ?, ?, ?, ?)",
                (trip_id, stop_id, sequence, arrival, arrival),
            )


def collector_ran(connection, start_utc, runs, every_minutes=5):
    """Record collector runs every five minutes from an ISO UTC timestamp."""
    start = datetime.fromisoformat(start_utc)
    for index in range(runs):
        started = (start + timedelta(minutes=every_minutes * index)).isoformat(timespec="seconds")
        connection.execute(
            "INSERT INTO ingest_run VALUES (?, 0, 0, 0, 0, 0)", (started,)
        )


def build_model(connection):
    """Run the star-schema build exactly as build_marts.main() does."""
    for statement in build_marts.STATEMENTS:
        connection.execute(statement)
    build_marts.build_collection_coverage(connection)
    build_marts.flag_collected_calls(connection)


def fact(connection, trip="T1", stop="S1"):
    row = connection.execute(
        "SELECT * FROM fact_passage WHERE trip_id = ? AND stop_id = ?", (trip, stop)
    ).fetchone()
    if row is None:
        return None
    columns = [d[0] for d in connection.execute("SELECT * FROM fact_passage LIMIT 0").description]
    return dict(zip(columns, row))


def write_gtfs(path, *, feed_version, trips, stops=None):
    """A minimal GTFS archive. trips = {trip_id: [(stop_id, "HH:MM:SS"), ...]}."""
    stops = stops or sorted({stop for calls in trips.values() for stop, _ in calls})

    def table(header, rows):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        writer.writerows(rows)
        return buffer.getvalue()

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("agency.txt", table(["agency_id", "agency_name"], [["A", "SNCF"]]))
        archive.writestr("routes.txt", table(
            ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
            [["R1", "A", "L1", "Mulhouse - Bâle", "2"]],
        ))
        archive.writestr("stops.txt", table(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"],
            [[stop, f"Gare {stop}", "47.7", "7.3", "0", ""] for stop in stops],
        ))
        archive.writestr("trips.txt", table(
            ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id"],
            [["R1", "SVC", trip, "", "0"] for trip in trips],
        ))
        archive.writestr("stop_times.txt", table(
            ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
            [[trip, time, time, stop, str(sequence)]
             for trip, calls in trips.items()
             for sequence, (stop, time) in enumerate(calls, start=1)],
        ))
        archive.writestr("feed_info.txt", table(
            ["feed_publisher_name", "feed_lang", "feed_version"], [["SNCF", "fr", feed_version]],
        ))
    return path
