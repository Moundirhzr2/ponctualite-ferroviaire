"""The timetable reference: layering SNCF's rolling-window publications.

Each export starts at its own publication date and drops earlier trips, so
versions are layered rather than replaced (journal, 12 September), and the
archive refills its own gaps from the national access point (18 September).
"""

import logging

import download_gtfs
import load_gtfs
from conftest import write_gtfs


def stops_of(connection, trip):
    return [row[0] for row in connection.execute(
        "SELECT stop_id FROM gtfs_stop_time WHERE trip_id = ? ORDER BY stop_sequence", (trip,)
    )]


def test_a_newer_version_replaces_a_trips_stop_pattern_instead_of_merging_it(db, tmp_path):
    old = write_gtfs(tmp_path / "a.zip", feed_version="2026-09-10",
                     trips={"T1": [("S1", "08:00:00"), ("S2", "08:20:00"), ("S3", "08:40:00")]})
    new = write_gtfs(tmp_path / "b.zip", feed_version="2026-09-11",
                     trips={"T1": [("S1", "08:00:00"), ("S3", "08:40:00")]})
    load_gtfs.apply_version(db, old, "2026-09-10")
    load_gtfs.apply_version(db, new, "2026-09-11")
    assert stops_of(db, "T1") == ["S1", "S3"]  # S2 must not linger


def test_a_trip_dropped_by_a_newer_version_is_kept(db, tmp_path):
    # Observations from the day before still point at it.
    old = write_gtfs(tmp_path / "a.zip", feed_version="2026-09-10",
                     trips={"T1": [("S1", "08:00:00")], "T2": [("S2", "09:00:00")]})
    new = write_gtfs(tmp_path / "b.zip", feed_version="2026-09-11",
                     trips={"T1": [("S1", "08:00:00")]})
    load_gtfs.apply_version(db, old, "2026-09-10")
    load_gtfs.apply_version(db, new, "2026-09-11")
    versions = dict(db.execute("SELECT trip_id, feed_version FROM gtfs_trip"))
    assert versions == {"T1": "2026-09-11", "T2": "2026-09-10"}
    assert stops_of(db, "T2") == ["S2"]


def test_empty_gtfs_fields_become_null(db, tmp_path):
    archive = write_gtfs(tmp_path / "a.zip", feed_version="v", trips={"T1": [("S1", "08:00:00")]})
    load_gtfs.apply_version(db, archive, "v")
    assert db.execute("SELECT parent_station FROM gtfs_stop").fetchone() == (None,)


def test_feed_version_is_read_from_feed_info(tmp_path):
    archive = write_gtfs(tmp_path / "a.zip", feed_version="2026-09-17", trips={"T1": [("S1", "08:00:00")]})
    assert load_gtfs.feed_version(archive) == "2026-09-17"


def test_last_modified_becomes_a_utc_file_stamp():
    assert download_gtfs.stamp_of("Fri, 18 Sep 2026 18:11:15 GMT") == "20260918181115"


def test_only_complete_gtfs_archives_are_accepted(tmp_path):
    complete = write_gtfs(tmp_path / "ok.zip", feed_version="v", trips={"T1": [("S1", "08:00:00")]})
    assert download_gtfs.is_valid_gtfs(complete)

    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(complete.read_bytes()[:200])
    assert not download_gtfs.is_valid_gtfs(truncated)

    import zipfile
    partial = tmp_path / "partial.zip"
    with zipfile.ZipFile(partial, "w") as archive:
        archive.writestr("agency.txt", "agency_id\n")
    assert not download_gtfs.is_valid_gtfs(partial)


def test_gaps_are_filled_forward_from_the_oldest_archived_version(tmp_path, monkeypatch):
    for stamp in ("20260910000000", "20260912000000"):
        (tmp_path / f"sncf-gtfs-{stamp}.zip").write_bytes(b"archived")
    monkeypatch.setattr(download_gtfs, "VERSIONS", tmp_path)
    monkeypatch.setattr(download_gtfs, "historised_versions", lambda: [
        ("20260908000000", "url-08"),   # before the archive starts: not wanted
        ("20260911000000", "url-11"),   # a hole: recovered
        ("20260912000000", "url-12"),   # already archived
        ("20260913000000", "url-13"),   # newer: recovered
    ])
    downloaded = []
    monkeypatch.setattr(download_gtfs, "download", lambda url, target: downloaded.append(url)
                        or target.write_bytes(b"recovered"))
    download_gtfs.fill_gaps()
    assert downloaded == ["url-11", "url-13"]


def test_an_unreachable_history_leaves_the_archive_alone(tmp_path, monkeypatch, caplog):
    (tmp_path / "sncf-gtfs-20260910000000.zip").write_bytes(b"archived")
    monkeypatch.setattr(download_gtfs, "VERSIONS", tmp_path)

    def unreachable():
        raise ConnectionError("transport.data.gouv.fr is down")

    monkeypatch.setattr(download_gtfs, "historised_versions", unreachable)
    with caplog.at_level(logging.WARNING):
        download_gtfs.fill_gaps()
    assert "gaps left as they are" in caplog.text
    assert [path.name for path in tmp_path.iterdir()] == ["sncf-gtfs-20260910000000.zip"]
