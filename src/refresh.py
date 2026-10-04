"""Rebuild everything the report reads, in order, stopping at the first failure.

The ingester writes observations continuously; the star schema, the quality gate
and the CSV export are batch steps that have to follow it. Running them by hand
invites two mistakes: forgetting one, and — worse — exporting after a failed
quality check, which produces a report that looks perfectly normal and is wrong.

Fail-fast is the point. If the quality gate rejects the model, the export does
not run, so Power BI keeps showing the previous, trustworthy data rather than
silently picking up a broken refresh.

    python src/refresh.py
"""

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DB_PATH = ROOT / "data" / "punctuality.db"

# The Windows task that runs src/archive.py every four hours, and how stale its
# last success may get before this script says so. Supabase keeps fourteen days,
# so a day leaves thirteen to notice and fix it.
ARCHIVE_TASK = "SncfArchiveSync"
ARCHIVE_MAX_AGE = timedelta(hours=24)

STEPS = [
    ("sync_supabase.py", "Synchronisation de la collecte hebergee (Supabase)"),
    ("download_gtfs.py", "Verification du GTFS theorique"),
    ("load_gtfs.py", "Chargement du GTFS theorique"),
    ("build_marts.py", "Reconstruction du schema en etoile"),
    ("quality_checks.py", "Controle des invariants"),
    ("export_powerbi.py", "Export CSV pour Power BI"),
]


def task_enabled_from_xml(xml):
    """Read the task-level Enabled flag from schtasks /XML output.

    The XML is read rather than the status column because that column is
    localised ("Désactivé" on this machine). Triggers carry their own Enabled
    flags, so only the one under Settings counts; absent means enabled.
    """
    settings = re.search(r"<Settings>.*?</Settings>", xml, re.S)
    if settings is None:
        return True
    flag = re.search(r"<Enabled>(true|false)</Enabled>", settings.group(0))
    return flag is None or flag.group(1) == "true"


def archive_task_enabled():
    """True or False when the archive task exists on this Windows machine, else None."""
    if os.name != "nt":
        return None
    result = subprocess.run(["schtasks", "/Query", "/TN", ARCHIVE_TASK, "/XML"], capture_output=True)
    if result.returncode != 0:
        return None
    # schtasks writes its XML as UTF-16, with or without a byte order mark.
    for encoding in ("utf-16", "utf-16-le", "utf-8"):
        text = result.stdout.decode(encoding, errors="replace")
        if "<Task" in text:
            return task_enabled_from_xml(text)
    return None


def last_archive_success():
    if not DB_PATH.exists():
        return None
    connection = sqlite3.connect(DB_PATH)
    try:
        row = connection.execute(
            "SELECT value FROM sync_state WHERE name = 'archive.last_success'"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        connection.close()
    return datetime.fromisoformat(row[0]) if row else None


def format_age(delta):
    hours = int(delta.total_seconds() // 3600)
    days, hours = divmod(hours, 24)
    return f"{days} j {hours} h" if days else f"{hours} h"


def archiving_warnings(task_enabled, last_success, now):
    """What, if anything, says the automatic archiving has stopped.

    task_enabled is None when there is no task to look at (another machine, a
    fresh clone): with no success recorded either, there is no automation to
    warn about, and silence is right.
    """
    warnings = []
    if task_enabled is False:
        warnings.append(f"la tâche planifiée {ARCHIVE_TASK} est désactivée")
    if last_success is None:
        if task_enabled is not None:
            warnings.append("aucun archivage réussi n'a été enregistré")
    elif now - last_success > ARCHIVE_MAX_AGE:
        warnings.append(
            f"dernier archivage réussi il y a {format_age(now - last_success)} "
            f"({last_success:%d/%m %H:%M} UTC)"
        )
    return warnings


def report_archiving():
    warnings = archiving_warnings(
        archive_task_enabled(), last_archive_success(), datetime.now(timezone.utc)
    )
    if not warnings:
        return
    print("\n!!! L'ARCHIVAGE AUTOMATIQUE NE TOURNE PLUS")
    for warning in warnings:
        print(f"    - {warning}")
    print("    Supabase ne garde que 14 jours : au-delà, les observations sont perdues.")
    print("    Cette exécution rattrape le retard, mais la tâche doit repartir :")
    print(f"    schtasks /Change /TN {ARCHIVE_TASK} /ENABLE")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # An alert, not a gate: the refresh catches up whatever the task missed.
    report_archiving()

    for script, label in STEPS:
        print(f"\n=== {label} ===", flush=True)
        result = subprocess.run([sys.executable, str(SRC / script)])
        if result.returncode != 0:
            print(
                f"\nECHEC sur {script} (code {result.returncode}) - arret.\n"
                "L'export n'a pas ete regenere : le rapport continue d'afficher\n"
                "les donnees precedentes plutot qu'un resultat douteux.",
                file=sys.stderr,
            )
            return result.returncode

    print("\nPipeline a jour. Dans Power BI : Accueil > Actualiser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
