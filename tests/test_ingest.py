"""The collector: decoding the feed, and the change-only upsert.

Both collectors, local and hosted, apply the same rule; these tests pin it down
on the SQLite side, where it runs on every observation that reaches the archive.
"""

from google.transit import gtfs_realtime_pb2

import ingest_sncf
from conftest import observe


def feed_with(*stops, start_date="20260914", relationship=0, timestamp=1_789_000_000):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    entity = feed.entity.add()
    entity.id = "1"
    trip = entity.trip_update.trip
    trip.trip_id = "T1"
    if start_date:
        trip.start_date = start_date
    trip.schedule_relationship = relationship
    for stop_id, delay in stops:
        update = entity.trip_update.stop_time_update.add()
        update.stop_id = stop_id
        update.arrival.time = 1_789_000_500
        if delay is not None:
            update.arrival.delay = delay
    return feed


def test_a_reported_zero_delay_stays_zero_and_a_missing_one_stays_unknown():
    # GTFS-RT is proto2: an unset delay reads back as 0. Without a presence
    # check, "no delay reported" would be counted as "on time".
    rows = list(ingest_sncf.rows_from(feed_with(("S1", 0), ("S2", None)), "20260914"))
    by_stop = {row[2]: row for row in rows}
    assert by_stop["S1"][6] == 0
    assert by_stop["S2"][6] is None


def test_absent_departure_and_route_are_null_not_zero_or_empty():
    (row,) = ingest_sncf.rows_from(feed_with(("S1", 120)), "20260914")
    assert row[4] is None  # route_id: the SNCF feed never sends it
    assert row[7] is None  # departure_delay
    assert row[9] is None  # departure_time


def test_service_date_is_iso_and_falls_back_when_the_feed_omits_it():
    (with_date,) = ingest_sncf.rows_from(feed_with(("S1", 0)), "20260920")
    (without,) = ingest_sncf.rows_from(feed_with(("S1", 0), start_date=None), "20260920")
    assert with_date[0] == "2026-09-14"
    assert without[0] == "2026-09-20"


def test_schedule_relationship_is_named_and_observed_at_is_the_feed_timestamp():
    (row,) = ingest_sncf.rows_from(feed_with(("S1", 0), relationship=3), "20260914")
    assert row[5] == "CANCELED"
    assert row[10] == 1_789_000_000


def test_entities_without_a_trip_update_are_ignored():
    feed = feed_with(("S1", 0))
    alert = feed.entity.add()
    alert.id = "alert"
    alert.alert.header_text.translation.add().text = "Travaux"
    assert len(list(ingest_sncf.rows_from(feed, "20260914"))) == 1


def stored(db):
    return db.execute("SELECT arrival_delay, observed_at FROM observation").fetchone()


def test_a_newer_reading_that_changes_nothing_is_not_written(db):
    assert observe(db, delay=300, observed_at=500) == 1
    assert observe(db, delay=300, observed_at=600) == 0
    assert stored(db) == (300, 500)


def test_a_newer_reading_with_a_new_delay_replaces_the_row(db):
    observe(db, delay=300, observed_at=500)
    assert observe(db, delay=600, observed_at=600) == 1
    assert stored(db) == (600, 600)


def test_an_older_reading_never_overwrites_a_newer_one(db):
    observe(db, delay=600, observed_at=600)
    assert observe(db, delay=0, observed_at=500) == 0
    assert stored(db) == (600, 600)


def test_the_first_reading_after_the_passage_is_kept_even_if_nothing_changed(db):
    # is_past compares arrival_time with observed_at: without this write, a call
    # last seen before the train arrived would stay "not yet happened" forever.
    observe(db, delay=0, arrival_time=1_000, observed_at=900)
    assert observe(db, delay=0, arrival_time=1_000, observed_at=1_100) == 1
    assert observe(db, delay=0, arrival_time=1_000, observed_at=1_200) == 0
    assert stored(db) == (0, 1_100)


def test_a_delay_appearing_where_there_was_none_counts_as_a_change(db):
    observe(db, delay=None, observed_at=500)
    assert observe(db, delay=0, observed_at=600) == 1
    assert stored(db) == (0, 600)
