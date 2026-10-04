"""The bridge from Supabase to the local archive.

The sync pages through the hosted table on a sequence the database stamps on
every write, commits rows and cursor together, and names the days the archive
is missing. A fake API stands in for Supabase, so nothing here needs a network.
"""

import logging

import pytest

import sync_supabase


class FakeSupabase:
    """Answers the two queries the sync makes, the way PostgREST would."""

    def __init__(self, observations=(), runs=(), fail_on_call=None):
        self.observations = sorted(observations, key=lambda row: row["sync_seq"])
        self.runs = list(runs)
        self.calls = 0
        self.fail_on_call = fail_on_call

    def select(self, table, params):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise ConnectionError("network dropped mid-sync")
        limit = params["limit"]
        if table == "observation":
            after = int(params["sync_seq"].removeprefix("gt."))
            return [row for row in self.observations if row["sync_seq"] > after][:limit]
        since = params["started_at"].removeprefix("gt.")
        return [run for run in self.runs if run["started_at"] > since][:limit]


def hosted(sequence, *, stop="S1", delay=0, day="2026-09-14"):
    return {
        "service_date": day, "trip_id": "T1", "stop_id": stop, "stop_sequence": 1,
        "route_id": None, "schedule_relationship": "SCHEDULED", "arrival_delay": delay,
        "departure_delay": None, "arrival_time": 1_000, "departure_time": None,
        "observed_at": 1_000 + sequence, "sync_seq": sequence,
    }


@pytest.fixture
def archive(db):
    db.execute(sync_supabase.STATE_SCHEMA)
    return db


def cursor(connection):
    return int(sync_supabase.get_state(connection, "observation.sync_seq", 0))


def test_pages_until_a_short_page_and_remembers_where_it_stopped(archive, monkeypatch):
    monkeypatch.setattr(sync_supabase, "PAGE_SIZE", 2)
    api = FakeSupabase([hosted(n, stop=f"S{n}") for n in (10, 20, 30, 40, 50)])
    assert sync_supabase.sync_observations(api, archive) == 5
    assert cursor(archive) == 50
    assert archive.execute("SELECT COUNT(*) FROM observation").fetchone() == (5,)
    assert sync_supabase.sync_observations(api, archive) == 0  # nothing new


def test_an_interrupted_sync_keeps_what_it_committed_and_resumes(archive, monkeypatch):
    monkeypatch.setattr(sync_supabase, "PAGE_SIZE", 2)
    rows = [hosted(n, stop=f"S{n}") for n in (10, 20, 30, 40)]
    with pytest.raises(ConnectionError):
        sync_supabase.sync_observations(FakeSupabase(rows, fail_on_call=2), archive)
    assert cursor(archive) == 20  # the first page and its cursor landed together

    assert sync_supabase.sync_observations(FakeSupabase(rows), archive) == 2
    assert archive.execute("SELECT COUNT(*) FROM observation").fetchone() == (4,)


def test_a_hosted_correction_reaches_the_archive(archive):
    sync_supabase.sync_observations(FakeSupabase([hosted(10, delay=300)]), archive)
    sync_supabase.sync_observations(FakeSupabase([hosted(10, delay=300), hosted(11, delay=600)]), archive)
    assert archive.execute("SELECT arrival_delay FROM observation").fetchone() == (600,)


def test_runs_are_stored_in_utc_and_never_twice(archive):
    runs = [{"started_at": "2026-09-14T08:05:00+02:00", "header_timestamp": 1,
             "payload_bytes": 1, "entities": 1, "stop_time_updates": 1, "rows_written": 1}]
    assert sync_supabase.sync_runs(FakeSupabase(runs=runs), archive) == 1
    assert sync_supabase.sync_runs(FakeSupabase(runs=runs), archive) == 0  # overlap re-reads it
    assert archive.execute("SELECT started_at FROM ingest_run").fetchone() == ("2026-09-14T06:05:00+00:00",)


def test_missing_days_are_named(archive, caplog):
    for day in ("2026-09-20", "2026-09-21", "2026-09-24"):
        sync_supabase.sync_observations(FakeSupabase([hosted(int(day[-2:]), day=day)]), archive)
    with caplog.at_level(logging.WARNING):
        sync_supabase.report_gaps(archive)
    assert "2 day(s) missing" in caplog.text
    assert "2026-09-22, 2026-09-23" in caplog.text


def test_a_complete_archive_raises_no_warning(archive, caplog):
    for day in ("2026-09-20", "2026-09-21"):
        sync_supabase.sync_observations(FakeSupabase([hosted(int(day[-2:]), day=day)]), archive)
    with caplog.at_level(logging.WARNING):
        sync_supabase.report_gaps(archive)
    assert caplog.text == ""
