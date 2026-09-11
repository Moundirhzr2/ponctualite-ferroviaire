"""Export the star schema to CSV for Power BI.

Power BI reads SQLite only through an ODBC driver that has to be installed and
configured separately; CSV avoids that entirely and keeps the refresh a matter
of re-running this script. Files are written UTF-8 with a BOM, without which
Power BI misreads accented station names.
"""

import csv
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "punctuality.db"
EXPORT_DIR = ROOT / "data" / "export"

TABLES = ["fact_passage", "dim_station", "dim_route"]


def export(connection, table, directory):
    cursor = connection.execute(f"SELECT * FROM {table}")
    columns = [description[0] for description in cursor.description]

    target = directory / f"{table}.csv"
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        rows = 0
        while True:
            batch = cursor.fetchmany(10_000)
            if not batch:
                break
            writer.writerows(batch)
            rows += len(batch)

    return rows, target.stat().st_size


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)

    for table in TABLES:
        rows, size = export(connection, table, EXPORT_DIR)
        logging.info("%-14s %7d rows  %6.1f KB", table, rows, size / 1024)

    connection.close()
    logging.info("exported to %s", EXPORT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
