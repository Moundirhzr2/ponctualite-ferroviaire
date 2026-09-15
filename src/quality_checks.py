"""Assert the pipeline's invariants. Exits non-zero when any of them breaks.

Two kinds of check live here. Structural ones catch a broken build: duplicate
dimension keys, orphan facts, impossible values. Methodological ones pin down
the decisions the figures depend on — cancelled trips excluded from the rate,
ADDED trips absent from the fact table. Those are easy to undo by accident
while editing a query, and undoing them inflates the punctuality rate without
anything looking wrong, so they are tested rather than merely documented.

Run after build_marts.py, before trusting any number.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "punctuality.db"

MIN_JOIN_RATE = 95.0  # observations (excluding ADDED) that must reach the fact table

# label -> (sql returning a single number, predicate on that number)
CHECKS = [
    (
        "dim_station.stop_id unique",
        "SELECT COUNT(*) - COUNT(DISTINCT stop_id) FROM dim_station",
        lambda n: n == 0,
        "duplicate keys break the Power BI relationship",
    ),
    (
        "dim_route.route_id unique",
        "SELECT COUNT(*) - COUNT(DISTINCT route_id) FROM dim_route",
        lambda n: n == 0,
        "duplicate keys break the Power BI relationship",
    ),
    (
        "no orphan station keys",
        """SELECT COUNT(*) FROM fact_passage f
           LEFT JOIN dim_station d ON d.stop_id = f.stop_id WHERE d.stop_id IS NULL""",
        lambda n: n == 0,
        "fact rows pointing at a station that does not exist",
    ),
    (
        "no orphan route keys",
        """SELECT COUNT(*) FROM fact_passage f
           LEFT JOIN dim_route d ON d.route_id = f.route_id WHERE d.route_id IS NULL""",
        lambda n: n == 0,
        "fact rows pointing at a route that does not exist",
    ),
    (
        "is_punctual is 0, 1 or NULL",
        "SELECT COUNT(*) FROM fact_passage WHERE is_punctual NOT IN (0, 1)",
        lambda n: n == 0,
        "the flag must stay boolean for AVG() to mean a rate",
    ),
    (
        "scheduled_hour within 0-23",
        "SELECT COUNT(*) FROM fact_passage WHERE scheduled_hour NOT BETWEEN 0 AND 23",
        lambda n: n == 0,
        "GTFS times past 24:00 must be folded back into a clock hour",
    ),
    (
        "every fact row has a theoretical time",
        "SELECT COUNT(*) FROM fact_passage WHERE scheduled_arrival IS NULL",
        lambda n: n == 0,
        "a passage with no schedule cannot be measured against one",
    ),
    (
        "is_collected is 0 or 1",
        "SELECT COUNT(*) FROM fact_passage WHERE is_collected NOT IN (0, 1)",
        lambda n: n == 0,
        "the flag excludes survivorship samples from hourly figures and must stay boolean",
    ),
    (
        "no collected call in an unwatched hour",
        """SELECT COUNT(*) FROM fact_passage f
           WHERE f.is_collected = 1 AND NOT EXISTS (
               SELECT 1 FROM dim_collection_hour h
               WHERE h.service_date = date(f.service_date, '+' || (f.scheduled_minutes / 1440) || ' days')
                 AND h.hour = f.scheduled_hour AND h.coverage_pct >= 50)""",
        lambda n: n == 0,
        "a call flagged as collected must fall in an hour the collector watched",
    ),
    (
        "is_past is 0 or 1",
        "SELECT COUNT(*) FROM fact_passage WHERE is_past NOT IN (0, 1)",
        lambda n: n == 0,
        "the flag separates realized calls from predicted ones and must stay boolean",
    ),
    (
        "cancelled trips excluded from the rate",
        """SELECT COUNT(*) FROM fact_passage
           WHERE schedule_relationship = 'CANCELED' AND is_punctual IS NOT NULL""",
        lambda n: n == 0,
        "a cancelled train is not a punctual one; counting it inflates the rate",
    ),
    (
        "ADDED trips absent from the fact table",
        "SELECT COUNT(*) FROM fact_passage WHERE schedule_relationship = 'ADDED'",
        lambda n: n == 0,
        "ADDED trips have no theoretical time by construction",
    ),
    # The three checks below pin down the feed's resolution rather than the
    # build. Every published rate is a share of calls under a 5-minute
    # threshold, and the feed reports delays in 5-minute steps, so the
    # threshold sits exactly on a step: 8.8% of observed calls are announced at
    # exactly 5 minutes and decide the headline figure on their own. The
    # README publishes both bounds because of it. If the feed ever changes
    # resolution these checks fail, which is the signal to rewrite that framing
    # rather than to keep quoting a number that no longer means the same thing.
    (
        "delays are whole minutes",
        "SELECT COUNT(*) FROM fact_passage WHERE arrival_delay_s % 60 <> 0",
        lambda n: n == 0,
        "sub-minute values would mean the feed changed resolution",
    ),
    (
        "no negative delay",
        "SELECT COUNT(*) FROM fact_passage WHERE arrival_delay_s < 0",
        lambda n: n == 0,
        "the feed never reports an early train, so a null delay means 'not late', "
        "not 'exactly on time'; negative values would change that reading",
    ),
    (
        "5-minute steps, % of exceptions",
        """SELECT CAST(ROUND(100.0 * SUM(CASE WHEN arrival_delay_s % 300 <> 0 THEN 1 ELSE 0 END)
                            / COUNT(*)) AS INT)
           FROM fact_passage
           WHERE arrival_delay_s IS NOT NULL AND arrival_delay_s <> 0""",
        lambda n: n <= 5,
        "delays stopped coming in 5-minute steps: a sharper rate is now possible "
        "and the two bounds published in the README are obsolete",
    ),
]


def run_checks(connection):
    failures = 0
    for label, sql, predicate, why in CHECKS:
        value = connection.execute(sql).fetchone()[0]
        passed = predicate(value)
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {label:42} ({value})")
        if not passed:
            print(f"         -> {why}")
            failures += 1
    return failures


def join_rates_by_day(connection):
    """Share of measurable observations that reached the fact table, per service day.

    Checked per day, never in aggregate. An aggregate hides a broken day behind
    healthy ones: on 2026-09-12 a reference reload dropped Friday to 94.45%
    while the pooled rate read 96.28% and passed.
    """
    eligible = dict(connection.execute("""
        SELECT service_date, COUNT(DISTINCT trip_id || '|' || stop_id)
        FROM observation WHERE schedule_relationship <> 'ADDED'
        GROUP BY service_date"""))
    kept = dict(connection.execute(
        "SELECT service_date, COUNT(*) FROM fact_passage GROUP BY service_date"
    ))
    return {
        day: 100.0 * kept.get(day, 0) / total
        for day, total in sorted(eligible.items()) if total
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    connection = sqlite3.connect(DB_PATH)
    print("invariants")
    failures = run_checks(connection)

    print(f"\n  join rate per service day (min {MIN_JOIN_RATE}% each)")
    for day, rate in join_rates_by_day(connection).items():
        passed = rate >= MIN_JOIN_RATE
        print(f"  [{'PASS' if passed else 'FAIL'}] {day}  {rate:.2f}%")
        if not passed:
            failures += 1

    connection.close()

    print()
    if failures:
        print(f"{failures} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
