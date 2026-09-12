"""Archive the published static SNCF GTFS whenever a new version appears.

SNCF republishes the export daily as a rolling window that drops past trips.
Each publication is kept as its own file in data/gtfs/versions/, named by its
Last-Modified time, and load_gtfs.py layers them. Nothing is ever overwritten:
the first version of this script replaced the single local copy in place, which
destroyed the 11 September export and cost Friday five points of join rate.

A version is validated as a GTFS archive before it is kept, and written through
a temporary file, so a truncated download never enters the archive.

Exit code 0 means the archive holds the latest publication, whether or not a
new one was added.
"""

import email.utils
import logging
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "data" / "gtfs" / "versions"
GTFS_URL = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"

REQUIRED_MEMBERS = {"agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt"}


def published_stamp():
    response = requests.head(GTFS_URL, timeout=60, allow_redirects=True)
    response.raise_for_status()
    header = response.headers.get("Last-Modified")
    if not header:
        raise RuntimeError("the server did not send Last-Modified; cannot version the download")
    published = email.utils.parsedate_to_datetime(header).astimezone(timezone.utc)
    return published.strftime("%Y%m%d%H%M%S")


def is_valid_gtfs(path):
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None and REQUIRED_MEMBERS <= set(archive.namelist())
    except zipfile.BadZipFile:
        return False


def download(target):
    partial = target.with_suffix(".zip.part")
    with requests.get(GTFS_URL, timeout=300, stream=True) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                handle.write(chunk)

    if not is_valid_gtfs(partial):
        partial.unlink(missing_ok=True)
        raise RuntimeError("downloaded archive is not a valid GTFS; nothing was kept")
    os.replace(partial, target)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    VERSIONS.mkdir(parents=True, exist_ok=True)
    have_any = any(VERSIONS.glob("*.zip"))

    try:
        stamp = published_stamp()
    except Exception as error:
        if have_any:
            logging.warning("could not check for a new GTFS (%s); using the archived versions", error)
            return 0
        logging.error("no archived GTFS and the source is unreachable: %s", error)
        return 1

    target = VERSIONS / f"sncf-gtfs-{stamp}.zip"
    if target.exists():
        logging.info("latest GTFS publication (%s) already archived", stamp)
        return 0

    logging.info("new GTFS publication %s, downloading", stamp)
    try:
        download(target)
    except Exception as error:
        logging.error("download failed: %s", error)
        return 0 if have_any else 1

    logging.info("archived %s (%.1f MB)", target.name, target.stat().st_size / 1048576)
    return 0


if __name__ == "__main__":
    sys.exit(main())
