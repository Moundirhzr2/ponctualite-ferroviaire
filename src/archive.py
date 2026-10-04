"""Keep the local archive complete, daily, without anyone having to remember.

Two sources forget. Supabase deletes observations older than seven days to stay
inside the free plan, and every SNCF GTFS publication drops the trips that
started before it; the national access point keeps only the last 25 of them.
Both are recoverable for a while and then never again, so these two steps are
the ones that cannot wait for the next time someone runs refresh.py.

Five days of observations, 21 to 25 September 2026, were lost exactly that way:
the weekly refresh was not run, and Supabase had moved on. Automating the two
archiving steps removes the only cause of permanent loss in the pipeline.

What stays in refresh.py is everything that can be rebuilt at any time from the
archive: the GTFS load, the star schema, the quality gate, the Power BI export.
Nothing is lost by running those later.

Output goes to data/archive.log, because the scheduled task runs without a
console and a job nobody can inspect is a job nobody trusts.

    python src/archive.py
"""

import logging
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ingest_sncf import DB_PATH
from sync_supabase import STATE_SCHEMA, set_state

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
LOG = ROOT / "data" / "archive.log"

# Ordered: the observations expire first, so they are fetched first. If the run
# is cut short, the step that was lost is the one with 25 days of slack.
STEPS = [
    ("sync_supabase.py", "hosted observations"),
    ("download_gtfs.py", "published GTFS reference"),
]


def setup_logging():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(LOG, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def run(script, label):
    """Run one step, forwarding what it printed into the archive log."""
    result = subprocess.run(
        [sys.executable, str(SRC / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in (result.stdout or "").splitlines():
        if line.strip():
            logging.info("  %s", line.rstrip())
    for line in (result.stderr or "").splitlines():
        if line.strip():
            logging.warning("  %s", line.rstrip())
    if result.returncode != 0:
        logging.error("%s failed (exit %d)", label, result.returncode)
    return result.returncode


def record_success():
    """Timestamp the run, so refresh.py can tell whether archiving still happens.

    A scheduled task can stop without a word: on 4 October 2026 this one was
    found disabled, by nobody who would admit to it, two runs after the last
    success. Nothing was lost because Supabase keeps fourteen days, but nothing
    would have said so either.
    """
    connection = sqlite3.connect(DB_PATH, timeout=120)
    with connection:
        connection.execute(STATE_SCHEMA)
        set_state(connection, "archive.last_success",
                  datetime.now(timezone.utc).isoformat(timespec="seconds"))
    connection.close()


def main():
    setup_logging()
    started = datetime.now(timezone.utc)
    logging.info("archiving %s", ", ".join(label for _, label in STEPS))

    failures = 0
    for script, label in STEPS:
        # Both steps are independent: a failure on one must not skip the other,
        # unlike refresh.py where a failed step invalidates everything after it.
        failures += 1 if run(script, label) != 0 else 0

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if failures:
        logging.error("archive incomplete: %d step(s) failed in %.1f s", failures, elapsed)
        return 1

    record_success()
    logging.info("archive up to date in %.1f s", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
