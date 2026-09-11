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

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

STEPS = [
    ("build_marts.py", "Reconstruction du schema en etoile"),
    ("quality_checks.py", "Controle des invariants"),
    ("export_powerbi.py", "Export CSV pour Power BI"),
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

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
