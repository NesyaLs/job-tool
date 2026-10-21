"""Charge base.example.yaml, le valide via les modèles, et affiche un résumé.

But : prouver que le modèle de données tient debout. En cas de YAML mal formé
ou de données invalides, on affiche un message clair plutôt qu'une stack trace.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Rendre le package src/jobtool importable sans installation (cf. README).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jobtool.core.experience_base.loader import load_base
from jobtool.core.experience_base.models import ExperienceBase

DEFAULT_PATH = PROJECT_ROOT / "data" / "base.example.yaml"


def print_summary(base: ExperienceBase) -> None:
    """Affiche un résumé lisible : profil, compteurs, et tous les ids."""
    achievement_count = sum(len(exp.achievements) for exp in base.experiences)

    print(f"Profil       : {base.profile.name} <{base.profile.email}>")
    print(f"Expériences  : {len(base.experiences)}")
    print(f"Achievements : {achievement_count}")
    print()
    for exp in base.experiences:
        print(f"[{exp.id}] {exp.title} — {exp.organization} ({exp.start} → {exp.end})")
        for ach in exp.achievements:
            print(f"    - {ach.id}")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    base = load_base(path)
    print(f"OK : {path} est valide.\n")
    print_summary(base)


if __name__ == "__main__":
    main()
