"""Measure how well the live GTFS-RT feed joins to the static GTFS reference.

This is the metric the whole project rests on: a punctuality figure is only
meaningful if the real-time trips can be matched to their scheduled times.
Run it before building anything downstream, and again whenever the static
GTFS is refreshed.

A raw match rate is misleading here. GTFS-RT marks some trips ADDED, meaning
they are deliberately absent from the static schedule, so counting them as
join failures understates the real reconciliation quality. They are reported
separately.
"""

import csv
import io
import zipfile
from collections import Counter
from pathlib import Path

import requests
from google.transit import gtfs_realtime_pb2

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "data" / "gtfs" / "versions"


def latest_version():
    """The live feed is compared with the newest archived schedule."""
    archives = sorted(VERSIONS.glob("*.zip"))
    if not archives:
        raise SystemExit(f"no GTFS version in {VERSIONS} - run download_gtfs.py first")
    return archives[-1]
RT_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"

# gtfs-realtime TripDescriptor.ScheduleRelationship
RELATIONSHIP = {
    0: "SCHEDULED", 1: "ADDED", 2: "UNSCHEDULED",
    3: "CANCELED", 5: "REPLACEMENT", 6: "DUPLICATED", 7: "DELETED",
}
# Trips the static schedule is not expected to contain.
NOT_IN_SCHEDULE = {"ADDED", "UNSCHEDULED", "DUPLICATED"}


def static_ids(zip_path):
    """Collect the trip and stop identifiers the static feed declares."""
    trips, stops = set(), set()
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open("trips.txt") as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, "utf-8-sig")):
                trips.add(row["trip_id"])
        with archive.open("stops.txt") as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, "utf-8-sig")):
                stops.add(row["stop_id"])
    return trips, stops


def live_trips(url):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(requests.get(url, timeout=60).content)

    trips, stops = [], []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        descriptor = entity.trip_update.trip
        trips.append((
            descriptor.trip_id,
            RELATIONSHIP.get(descriptor.schedule_relationship, "UNKNOWN"),
        ))
        for stop_time in entity.trip_update.stop_time_update:
            if stop_time.stop_id:
                stops.append(stop_time.stop_id)
    return trips, stops


def main():
    known_trips, known_stops = static_ids(latest_version())
    print(f"static GTFS: {len(known_trips):,} trips, {len(known_stops):,} stops")

    trips, stops = live_trips(RT_URL)
    print(f"live feed:   {len(trips):,} trips, {len(stops):,} stop references\n")

    breakdown = Counter()
    for trip_id, relationship in trips:
        found = "found" if trip_id in known_trips else "absent"
        breakdown[(relationship, found)] += 1

    print("trips by schedule_relationship:")
    for (relationship, found), count in sorted(breakdown.items()):
        print(f"  {relationship:12} {found:7} {count:6,}")

    # Only trips the schedule is meant to contain count towards the join rate.
    expected = [t for t, r in trips if r not in NOT_IN_SCHEDULE]
    matched = [t for t in expected if t in known_trips]
    rate = 100 * len(matched) / len(expected) if expected else 0.0

    excluded = len(trips) - len(expected)
    print(
        f"\ntrip join rate (excluding {excluded:,} trips flagged as not scheduled): "
        f"{len(matched):,}/{len(expected):,} = {rate:.2f}%"
    )

    stop_matched = sum(1 for s in stops if s in known_stops)
    stop_rate = 100 * stop_matched / len(stops) if stops else 0.0
    print(f"stop join rate: {stop_matched:,}/{len(stops):,} = {stop_rate:.2f}%")


if __name__ == "__main__":
    main()
