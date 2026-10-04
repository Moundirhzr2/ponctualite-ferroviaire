"""The alert refresh.py raises when the automatic archiving has stopped.

On 4 October 2026 the archive task was found disabled, two runs after its last
success, with nothing to say so. The alert must speak up when that happens and
stay quiet otherwise, including on a machine that never had the task.
"""

from datetime import datetime, timedelta, timezone

import refresh

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def test_a_recent_success_with_the_task_enabled_raises_nothing():
    assert refresh.archiving_warnings(True, NOW - timedelta(hours=5), NOW) == []


def test_a_disabled_task_is_reported_even_right_after_a_success():
    warnings = refresh.archiving_warnings(False, NOW - timedelta(hours=1), NOW)
    assert warnings == ["la tâche planifiée SncfArchiveSync est désactivée"]


def test_a_success_older_than_a_day_is_reported_with_its_age():
    (warning,) = refresh.archiving_warnings(True, NOW - timedelta(days=2, hours=3), NOW)
    assert "il y a 2 j 3 h" in warning
    assert "02/10 09:00 UTC" in warning


def test_just_under_a_day_is_still_fine():
    assert refresh.archiving_warnings(True, NOW - timedelta(hours=23, minutes=59), NOW) == []


def test_a_task_that_never_succeeded_is_reported():
    assert refresh.archiving_warnings(True, None, NOW) == ["aucun archivage réussi n'a été enregistré"]


def test_a_machine_without_the_automation_is_left_alone():
    # A fresh clone, or Linux: no task to read and nothing ever recorded.
    assert refresh.archiving_warnings(None, None, NOW) == []


def test_a_stale_success_is_reported_even_where_the_task_cannot_be_read():
    assert refresh.archiving_warnings(None, NOW - timedelta(days=3), NOW)


def test_only_the_task_level_enabled_flag_counts():
    disabled = (
        "<Task><Triggers><CalendarTrigger><Enabled>true</Enabled></CalendarTrigger></Triggers>"
        "<Settings><StartWhenAvailable>true</StartWhenAvailable><Enabled>false</Enabled></Settings></Task>"
    )
    trigger_off_only = (
        "<Task><Triggers><LogonTrigger><Enabled>false</Enabled></LogonTrigger></Triggers>"
        "<Settings><StartWhenAvailable>true</StartWhenAvailable></Settings></Task>"
    )
    assert refresh.task_enabled_from_xml(disabled) is False
    assert refresh.task_enabled_from_xml(trigger_off_only) is True
