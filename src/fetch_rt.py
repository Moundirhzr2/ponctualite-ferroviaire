"""Fetch the Solea GTFS-RT feeds, archive the raw payloads, and log what they contained.

This doubles as a diagnostic. The CSV log answers "does the trip-updates feed
ever actually carry data?" without the rest of the pipeline needing to exist.
"""

import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.transit import gtfs_realtime_pb2

FEEDS = {
    "trip-updates": "https://proxy.transport.data.gouv.fr/resource/solea-mulhouse-gtfs-rt",
    "service-alerts": "https://proxy.transport.data.gouv.fr/resource/solea-mulhouse-gtfs-rt-service-alert",
}

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
LOG = ROOT / "data" / "feed_log.csv"
LOG_FIELDS = [
    "fetched_at", "feed", "header_timestamp", "bytes",
    "entities", "trip_updates", "vehicles", "alerts", "archived",
]
RUN_LOG = ROOT / "data" / "ingest.log"


def setup_logging():
    """Log to a file always, and to the console only when there is one.

    Under Task Scheduler the script runs windowless and sys.stdout is None,
    so a bare StreamHandler would swallow every run including the failures.
    """
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(RUN_LOG, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def archive(name, header_ts, payload):
    """Write the raw payload once per distinct header timestamp."""
    stamp = datetime.fromtimestamp(header_ts, timezone.utc)
    target = RAW / stamp.strftime("%Y-%m-%d") / f"{name}-{stamp.strftime('%H%M%S')}.pb"
    if target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target.relative_to(ROOT).as_posix()


def fetch(name, url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.content

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)

    counts = {"trip_updates": 0, "vehicles": 0, "alerts": 0}
    for entity in feed.entity:
        if entity.HasField("trip_update"):
            counts["trip_updates"] += 1
        if entity.HasField("vehicle"):
            counts["vehicles"] += 1
        if entity.HasField("alert"):
            counts["alerts"] += 1

    # A feed with no header timestamp still deserves an archive slot.
    header_ts = feed.header.timestamp or int(datetime.now(timezone.utc).timestamp())

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feed": name,
        "header_timestamp": header_ts,
        "bytes": len(payload),
        "entities": len(feed.entity),
        **counts,
        "archived": archive(name, header_ts, payload) or "",
    }


def log(rows):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def main():
    setup_logging()

    rows = []
    for name, url in FEEDS.items():
        try:
            row = fetch(name, url)
        except Exception as error:
            logging.error("%s: FAILED - %s", name, error)
            continue

        rows.append(row)
        suffix = "" if row["archived"] else "  [same header timestamp, not re-archived]"
        logging.info(
            "%s: %d entities (%d trip updates, %d vehicles, %d alerts), %d bytes%s",
            name, row["entities"], row["trip_updates"], row["vehicles"],
            row["alerts"], row["bytes"], suffix,
        )

    if rows:
        log(rows)


if __name__ == "__main__":
    main()
