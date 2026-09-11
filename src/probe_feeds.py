"""Probe candidate GTFS-RT feeds and report what they actually contain.

Written after the Solea trip-updates feed turned out to be permanently empty:
never build a pipeline on a feed before confirming it carries entities.

Usage:
    python src/probe_feeds.py                 # probe the built-in candidates
    python src/probe_feeds.py <url> [<url>]   # probe specific URLs
"""

import sys
from datetime import datetime, timezone

import requests
from google.transit import gtfs_realtime_pb2

CANDIDATES = {
    "sncf-trip-updates": "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates",
    "solea-trip-updates": "https://proxy.transport.data.gouv.fr/resource/solea-mulhouse-gtfs-rt",
}


def probe(label, url):
    try:
        response = requests.get(url, timeout=45)
        response.raise_for_status()
    except Exception as error:
        return f"{label}: UNREACHABLE - {error}"

    payload = response.content
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(payload)
    except Exception as error:
        return f"{label}: NOT A VALID GTFS-RT FEED - {error} ({len(payload)} bytes)"

    trips = sum(1 for e in feed.entity if e.HasField("trip_update"))
    vehicles = sum(1 for e in feed.entity if e.HasField("vehicle"))
    alerts = sum(1 for e in feed.entity if e.HasField("alert"))

    age = ""
    if feed.header.timestamp:
        seconds = datetime.now(timezone.utc).timestamp() - feed.header.timestamp
        age = f", header {seconds:.0f}s old"

    # A populated trip_update carries stop-level predictions; that is what
    # punctuality is computed from, so report whether they are actually there.
    stop_updates = 0
    delays = 0
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        for stop_time in entity.trip_update.stop_time_update:
            stop_updates += 1
            if stop_time.HasField("arrival") and stop_time.arrival.HasField("delay"):
                delays += 1

    return (
        f"{label}: {len(feed.entity)} entities "
        f"({trips} trip updates, {vehicles} vehicles, {alerts} alerts), "
        f"{stop_updates} stop-time updates of which {delays} carry a delay, "
        f"{len(payload):,} bytes{age}"
    )


def main():
    if len(sys.argv) > 1:
        targets = {url: url for url in sys.argv[1:]}
    else:
        targets = CANDIDATES

    for label, url in targets.items():
        print(probe(label, url))


if __name__ == "__main__":
    main()
