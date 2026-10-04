"""The quality gate: it must pass on a sound model and fail on each broken rule.

A check that cannot fail protects nothing, so every methodological invariant is
also broken on purpose here, once, to prove the gate notices.
"""

import pytest

import quality_checks
from conftest import build_model, collector_ran, observe, reference


@pytest.fixture
def model(db):
    reference(db, trips={"T1": [("S1", "08:00:00"), ("S2", "08:30:00")],
                         "T2": [("S1", "09:00:00")]})
    observe(db, trip="T1", stop="S1", sequence=1, delay=0)
    observe(db, trip="T1", stop="S2", sequence=2, delay=300)
    observe(db, trip="T2", stop="S1", sequence=1, delay=600)
    collector_ran(db, "2026-09-14T06:00:00+00:00", runs=24)
    build_model(db)
    return db


def failures(connection):
    return quality_checks.run_checks(connection)


def test_a_sound_model_passes_every_check(model):
    assert failures(model) == 0


@pytest.mark.parametrize("breakage", [
    "UPDATE fact_passage SET schedule_relationship = 'CANCELED', is_punctual = 1",
    "UPDATE fact_passage SET schedule_relationship = 'ADDED'",
    "UPDATE fact_passage SET arrival_delay_s = 90",
    "UPDATE fact_passage SET arrival_delay_s = -300",
    "UPDATE fact_passage SET is_punctual = 2",
    "UPDATE fact_passage SET scheduled_hour = 24",
    "UPDATE fact_passage SET stop_id = 'NOWHERE'",
    "UPDATE fact_passage SET is_collected = 1, scheduled_hour = 3",
], ids=[
    "cancelled-counted-punctual", "added-trip-in-facts", "sub-minute-delay",
    "negative-delay", "non-boolean-flag", "hour-out-of-range",
    "orphan-station", "collected-in-unwatched-hour",
])
def test_each_broken_rule_is_caught(model, breakage):
    model.execute(breakage)
    assert failures(model) >= 1


def test_the_five_minute_step_check_survives_a_model_with_no_late_train(db):
    # With no non-zero delay the share of off-step delays is 0/0. The gate must
    # read that as nothing to object to, not crash on it.
    reference(db, trips={"T1": [("S1", "08:00:00")]})
    observe(db, delay=0)
    collector_ran(db, "2026-09-14T06:00:00+00:00", runs=12)
    build_model(db)
    assert failures(db) == 0


def test_join_rate_is_computed_per_day_never_pooled(db):
    # One healthy day and one broken one: pooled, they would average out.
    reference(db, trips={"T1": [("S1", "08:00:00")]})
    for day in ("2026-09-14", "2026-09-15"):
        observe(db, service_date=day)
    observe(db, service_date="2026-09-15", trip="GONE", stop="S9")
    build_model(db)
    assert quality_checks.join_rates_by_day(db) == {"2026-09-14": 100.0, "2026-09-15": 50.0}
