"""The star schema: which calls count, and how.

Every published rate rests on four flags computed here — is_punctual, is_past,
is_collected, and membership of the fact table itself. Each was the subject of a
correction in the README's journal; these tests keep the corrections in place.
"""

import build_marts
from conftest import build_model, collector_ran, fact, observe, reference


def test_the_five_minute_step_counts_as_punctual_and_the_next_one_does_not(db):
    reference(db, trips={"T1": [("S1", "08:00:00"), ("S2", "08:30:00"), ("S3", "09:00:00")]})
    observe(db, stop="S1", sequence=1, delay=300)
    observe(db, stop="S2", sequence=2, delay=600)
    observe(db, stop="S3", sequence=3, delay=None)
    build_model(db)
    assert fact(db, stop="S1")["is_punctual"] == 1
    assert fact(db, stop="S2")["is_punctual"] == 0
    assert fact(db, stop="S3")["is_punctual"] is None


def test_a_cancelled_train_is_never_counted_as_punctual(db):
    reference(db, trips={"T1": [("S1", "08:00:00")]})
    observe(db, delay=0, relationship="CANCELED")
    build_model(db)
    assert fact(db)["is_punctual"] is None


def test_a_trip_absent_from_the_timetable_stays_out_of_the_fact_table(db):
    # ADDED trips have no theoretical time by construction.
    reference(db, trips={"T1": [("S1", "08:00:00")]})
    observe(db, trip="EXTRA", relationship="ADDED")
    build_model(db)
    assert fact(db, trip="EXTRA") is None


def test_a_call_is_past_only_once_a_reading_was_taken_after_its_arrival(db):
    reference(db, trips={"T1": [("S1", "08:00:00"), ("S2", "09:00:00")]})
    observe(db, stop="S1", sequence=1, arrival_time=1_000, observed_at=1_000)
    observe(db, stop="S2", sequence=2, arrival_time=5_000, observed_at=1_000)
    build_model(db)
    assert fact(db, stop="S1")["is_past"] == 1
    assert fact(db, stop="S2")["is_past"] == 0


def test_times_past_midnight_are_read_from_the_string_not_as_a_clock(db):
    reference(db, trips={"T1": [("S1", "25:10:00")]})
    observe(db)
    build_model(db)
    row = fact(db)
    assert row["scheduled_minutes"] == 25 * 60 + 10
    assert row["scheduled_hour"] == 1


def test_coverage_is_bucketed_in_paris_time_across_the_clock_change(db):
    # 06:00 UTC is 08h in Paris in September (UTC+2) and 07h in November (UTC+1).
    collector_ran(db, "2026-09-14T06:00:00+00:00", runs=12)
    collector_ran(db, "2026-11-02T06:00:00+00:00", runs=12)
    build_marts.build_collection_coverage(db)
    covered = set(db.execute(
        "SELECT service_date, hour FROM dim_collection_hour WHERE coverage_pct = 100"
    ))
    assert covered == {("2026-09-14", 8), ("2026-11-02", 7)}


def test_an_hour_the_collector_missed_is_an_explicit_zero(db):
    collector_ran(db, "2026-09-14T06:00:00+00:00", runs=12)
    build_marts.build_collection_coverage(db)
    hours = db.execute(
        "SELECT COUNT(*), SUM(coverage_pct = 0) FROM dim_collection_hour WHERE service_date = '2026-09-14'"
    ).fetchone()
    assert hours == (24, 23)


def test_a_call_counts_only_if_its_hour_was_watched_at_least_half(db):
    reference(db, trips={"T1": [("S1", "08:10:00"), ("S2", "09:10:00")]})
    observe(db, stop="S1", sequence=1)
    observe(db, stop="S2", sequence=2)
    collector_ran(db, "2026-09-14T06:00:00+00:00", runs=6)   # 08h Paris: 50 %
    collector_ran(db, "2026-09-14T07:00:00+00:00", runs=5)   # 09h Paris: 42 %
    build_model(db)
    assert fact(db, stop="S1")["is_collected"] == 1
    assert fact(db, stop="S2")["is_collected"] == 0


def test_a_call_after_midnight_is_judged_against_the_next_calendar_day(db):
    # 24:30 on the 14th happens at 00:30 on the 15th: that is the hour to check.
    reference(db, trips={"T1": [("S1", "24:30:00")]})
    observe(db, service_date="2026-09-14")
    collector_ran(db, "2026-09-14T22:00:00+00:00", runs=12)  # 00h Paris on the 15th
    build_model(db)
    assert fact(db)["is_collected"] == 1


def test_the_same_hour_on_the_wrong_day_does_not_count(db):
    reference(db, trips={"T1": [("S1", "00:30:00")]})
    observe(db, service_date="2026-09-15")
    collector_ran(db, "2026-09-14T22:00:00+00:00", runs=12)  # 00h Paris on the 15th
    collector_ran(db, "2026-09-16T08:00:00+00:00", runs=1)   # extends the covered range
    build_model(db)
    assert fact(db)["is_collected"] == 1

    db.execute("DELETE FROM observation")
    observe(db, service_date="2026-09-16")
    build_model(db)
    assert fact(db)["is_collected"] == 0
