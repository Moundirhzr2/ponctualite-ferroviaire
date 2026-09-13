"""Pull the hosted collector's data from Supabase into the local SQLite database.

Collection runs in Supabase (supabase/functions/ingest-sncf), independent of
this machine being awake. Everything downstream — the GTFS reference, the star
schema, the quality gate, the Power BI export — still runs locally on
data/punctuality.db. This script is the bridge.

Observations are fetched with a keyset cursor on sync_seq, a number the database
stamps on every insert and update, so each call asks only for rows changed since
the last one it stored. They are merged with the same rule as the local
collector: a reading replaces a row only when it is fresher. Cursor and rows are
committed together, page by page, so an interrupted sync resumes where it
stopped and never skips a row.

Only successful collector runs are copied. They feed dim_collection_hour, the
record of which hours were actually watched.

Configuration is read from .env at the repository root (never committed):

    SUPABASE_URL=https://<project-ref>.supabase.co
    SUPABASE_PUBLISHABLE_KEY=<publishable key from the project's API settings>

Without it the sync is skipped, so the rest of the pipeline still runs on a
clone that has no Supabase project.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ingest_sncf import UPSERT, connect

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
PAGE_SIZE = 1000  # Supabase's default cap on rows per API response

OBSERVATION_COLUMNS = [
    "service_date", "trip_id", "stop_id", "stop_sequence", "route_id",
    "schedule_relationship", "arrival_delay", "departure_delay",
    "arrival_time", "departure_time", "observed_at",
]
RUN_COLUMNS = [
    "started_at", "header_timestamp", "payload_bytes",
    "entities", "stop_time_updates", "rows_written",
]
# A run is copied once it finishes; re-reading a day of runs on every sync
# catches any that were still in progress last time, at negligible cost.
RUN_OVERLAP = timedelta(days=1)

STATE_SCHEMA = "CREATE TABLE IF NOT EXISTS sync_state (name TEXT PRIMARY KEY, value TEXT NOT NULL)"


def read_env():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


class Supabase:
    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/rest/v1"
        self.session = requests.Session()
        self.session.headers.update({"apikey": key, "Accept": "application/json"})

    def select(self, table, params):
        response = self.session.get(f"{self.base}/{table}", params=params, timeout=60)
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"Supabase refused the request (HTTP {response.status_code}): "
                "check SUPABASE_PUBLISHABLE_KEY in .env"
            )
        response.raise_for_status()
        return response.json()


def get_state(connection, name, default):
    row = connection.execute("SELECT value FROM sync_state WHERE name = ?", (name,)).fetchone()
    return row[0] if row else default


def set_state(connection, name, value):
    connection.execute(
        "INSERT INTO sync_state (name, value) VALUES (?, ?) "
        "ON CONFLICT (name) DO UPDATE SET value = excluded.value",
        (name, str(value)),
    )


def sync_observations(api, connection):
    cursor = int(get_state(connection, "observation.sync_seq", 0))
    fetched = 0
    while True:
        page = api.select("observation", {
            "select": ",".join(OBSERVATION_COLUMNS + ["sync_seq"]),
            "sync_seq": f"gt.{cursor}",
            "order": "sync_seq.asc",
            "limit": PAGE_SIZE,
        })
        if not page:
            break
        with connection:  # rows and cursor land together, or not at all
            connection.executemany(
                UPSERT, [tuple(row[column] for column in OBSERVATION_COLUMNS) for row in page]
            )
            cursor = page[-1]["sync_seq"]
            set_state(connection, "observation.sync_seq", cursor)
        fetched += len(page)
        if len(page) < PAGE_SIZE:
            break
    return fetched


def normalise_timestamp(value):
    """Store run times like the local collector does: UTC, to the second."""
    return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat(timespec="seconds")


def sync_runs(api, connection):
    last = get_state(connection, "ingest_run.started_at", "1970-01-01T00:00:00+00:00")
    since = (datetime.fromisoformat(last) - RUN_OVERLAP).isoformat()
    copied = 0
    while True:
        page = api.select("ingest_run", {
            "select": ",".join(RUN_COLUMNS),
            "status": "eq.ok",
            "started_at": f"gt.{since}",
            "order": "started_at.asc",
            "limit": PAGE_SIZE,
        })
        if not page:
            break
        with connection:
            for run in page:
                values = [normalise_timestamp(run["started_at"])] + [run[c] for c in RUN_COLUMNS[1:]]
                before = connection.total_changes
                connection.execute(
                    "INSERT OR IGNORE INTO ingest_run VALUES (?, ?, ?, ?, ?, ?)", values
                )
                copied += connection.total_changes - before
            newest = normalise_timestamp(page[-1]["started_at"])
            if newest > last:
                last = newest
                set_state(connection, "ingest_run.started_at", last)
        since = page[-1]["started_at"]
        if len(page) < PAGE_SIZE:
            break
    return copied


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    settings = read_env()
    url, key = settings.get("SUPABASE_URL"), settings.get("SUPABASE_PUBLISHABLE_KEY")
    if not url or not key:
        logging.info("Supabase not configured (.env), sync skipped - using local data only")
        return 0

    api = Supabase(url, key)
    connection = connect()
    connection.execute(STATE_SCHEMA)

    try:
        observations = sync_observations(api, connection)
        runs = sync_runs(api, connection)
    except Exception as error:
        logging.error("sync failed: %s", error)
        return 1
    finally:
        connection.close()

    logging.info("synced %d observation changes and %d collector runs from Supabase", observations, runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
