"""Archive the published static SNCF GTFS, and fill in any publication missed.

SNCF republishes the export daily as a rolling window that drops past trips.
Each publication is kept as its own file in data/gtfs/versions/, named by its
Last-Modified time, and load_gtfs.py layers them. Nothing is ever overwritten:
the first version of this script replaced the single local copy in place, which
destroyed the 11 September export and cost Friday five points of join rate.

Fetching only the current publication is not enough. A day where this script
does not run leaves a hole, and the trips that existed only in that day's export
are gone for good: on 17 September the join rate fell to 94.83% and stopped the
pipeline, because the runs of 16 and 17 September had been skipped. The Point
d'Accès National keeps the last 25 publications of each resource, so the missing
ones are downloaded from there and the archive closes its own gaps. Beyond 25
publications, roughly 25 days, a hole becomes permanent.

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
from datetime import timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "data" / "gtfs" / "versions"
GTFS_URL = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"

# "Réseau SNCF TGV, Intercités et TER" on transport.data.gouv.fr. Resource 83582
# is the GTFS export; 83195 is the NeTEx one and must not be mixed up with it.
DATASET_URL = "https://transport.data.gouv.fr/api/datasets/6853c089b3ed5781f6adfdf7"
GTFS_RESOURCE_ID = 83582

REQUIRED_MEMBERS = {"agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt"}


def stamp_of(last_modified):
    """Publication stamp used as the file name, derived from Last-Modified."""
    published = email.utils.parsedate_to_datetime(last_modified).astimezone(timezone.utc)
    return published.strftime("%Y%m%d%H%M%S")


def published_stamp():
    response = requests.head(GTFS_URL, timeout=60, allow_redirects=True)
    response.raise_for_status()
    header = response.headers.get("Last-Modified")
    if not header:
        raise RuntimeError("the server did not send Last-Modified; cannot version the download")
    return stamp_of(header)


def is_valid_gtfs(path):
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None and REQUIRED_MEMBERS <= set(archive.namelist())
    except zipfile.BadZipFile:
        return False


def download(url, target):
    partial = target.with_suffix(".zip.part")
    with requests.get(url, timeout=300, stream=True) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                handle.write(chunk)

    if not is_valid_gtfs(partial):
        partial.unlink(missing_ok=True)
        raise RuntimeError("downloaded archive is not a valid GTFS; nothing was kept")
    os.replace(partial, target)


def historised_versions():
    """(stamp, url) of every GTFS publication the national access point still keeps."""
    response = requests.get(DATASET_URL, timeout=120)
    response.raise_for_status()

    versions = []
    for entry in response.json().get("history", []):
        if entry.get("resource_id") != GTFS_RESOURCE_ID:
            continue
        payload = entry.get("payload", {})
        last_modified = payload.get("http_headers", {}).get("last-modified")
        url = payload.get("permanent_url")
        if last_modified and url:
            versions.append((stamp_of(last_modified), url))
    return sorted(versions)


def archived_stamps():
    return {path.stem.removeprefix("sncf-gtfs-") for path in VERSIONS.glob("*.zip")}


def fill_gaps():
    """Download the publications missing between the oldest archived one and now.

    Older publications are deliberately left alone: they would extend the
    reference before the first day collected, which no observation can use.
    """
    have = archived_stamps()
    if not have:
        return

    try:
        available = historised_versions()
    except Exception as error:
        logging.warning("could not list the historised versions (%s); gaps left as they are", error)
        return

    oldest = min(have)
    missing = [(stamp, url) for stamp, url in available if stamp > oldest and stamp not in have]
    if not missing:
        logging.info("no gap in the GTFS archive")
        return

    logging.info("%d publication(s) missing from the archive, downloading", len(missing))
    for stamp, url in missing:
        target = VERSIONS / f"sncf-gtfs-{stamp}.zip"
        try:
            download(url, target)
            logging.info("recovered %s (%.1f MB)", target.name, target.stat().st_size / 1048576)
        except Exception as error:
            logging.warning("could not recover %s: %s", stamp, error)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    VERSIONS.mkdir(parents=True, exist_ok=True)
    have_any = any(VERSIONS.glob("*.zip"))

    try:
        stamp = published_stamp()
    except Exception as error:
        if have_any:
            logging.warning("could not check for a new GTFS (%s); using the archived versions", error)
            fill_gaps()
            return 0
        logging.error("no archived GTFS and the source is unreachable: %s", error)
        return 1

    target = VERSIONS / f"sncf-gtfs-{stamp}.zip"
    if target.exists():
        logging.info("latest GTFS publication (%s) already archived", stamp)
    else:
        logging.info("new GTFS publication %s, downloading", stamp)
        try:
            download(GTFS_URL, target)
            logging.info("archived %s (%.1f MB)", target.name, target.stat().st_size / 1048576)
        except Exception as error:
            logging.error("download failed: %s", error)
            if not have_any:
                return 1

    fill_gaps()
    return 0


if __name__ == "__main__":
    sys.exit(main())
