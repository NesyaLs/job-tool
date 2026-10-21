"""Charge la base + un CV adapté, lance check_traceability, affiche le résultat.

Réutilise la logique de chargement/validation de l'étape 1 (load_base.load_base).
Démontre aussi, en mémoire, les 3 cas de violation attendus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Rendre le package src/jobtool importable sans installation (cf. README).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pydantic import ValidationError

from jobtool.core.experience_base.loader import load_base
from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.traceability import Violation, check_traceability
from jobtool.documents.cv.schema import TailoredBullet, TailoredCV, TailoredExperience

DATA_DIR = PROJECT_ROOT / "data"
BASE_PATH = DATA_DIR / "base.example.yaml"
CV_PATH = DATA_DIR / "tailored.example.json"


def load_cv(path: Path) -> TailoredCV:
    """Lit + parse + valide un CV adapté, avec messages lisibles en cas d'erreur."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"Erreur : fichier introuvable : {path}")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Erreur : JSON mal formé dans {path}\n{exc}")

    try:
        return TailoredCV.model_validate(data)
    except ValidationError as exc:
        raise SystemExit(f"Erreur : CV invalide dans {path}\n{exc}")


def report(title: str, violations: list[Violation]) -> None:
    """Affiche le résultat d'un check de façon lisible."""
    print(f"=== {title} ===")
    if not violations:
        print("  0 violation — CV traçable.")
    else:
        print(f"  {len(violations)} violation(s) :")
        for violation in violations:
            print(f"    - {violation}")
    print()


def main() -> None:
    base: ExperienceBase = load_base(BASE_PATH)
    cv = load_cv(CV_PATH)

    # 1. Cas nominal : le fichier d'exemple doit être traçable.
    report(
        "tailored.example.json (attendu : 0 violation)", check_traceability(base, cv)
    )

    # 2. source_id d'achievement inventé.
    cv_fake_achievement = TailoredCV(
        experiences=[
            TailoredExperience(
                source_experience_id="stage-licorne-2025",
                bullets=[
                    TailoredBullet(source_id="stage-licorne-2025.999", text="inventé")
                ],
            )
        ]
    )
    report(
        "achievement inventé (attendu : violation)",
        check_traceability(base, cv_fake_achievement),
    )

    # 3. Bullet rattaché à la MAUVAISE expérience (l'achievement existe, mais ailleurs).
    cv_wrong_experience = TailoredCV(
        experiences=[
            TailoredExperience(
                source_experience_id="projet-perso-teleportation",
                bullets=[
                    TailoredBullet(
                        source_id="stage-licorne-2025.1", text="mauvais parent"
                    )
                ],
            )
        ]
    )
    report(
        "bullet sur la mauvaise expérience (attendu : violation)",
        check_traceability(base, cv_wrong_experience),
    )

    # 4. Même achievement utilisé deux fois dans le CV.
    cv_duplicate = TailoredCV(
        experiences=[
            TailoredExperience(
                source_experience_id="stage-licorne-2025",
                bullets=[
                    TailoredBullet(
                        source_id="stage-licorne-2025.1", text="première fois"
                    ),
                    TailoredBullet(
                        source_id="stage-licorne-2025.1", text="seconde fois"
                    ),
                ],
            )
        ]
    )
    report(
        "achievement utilisé deux fois (attendu : violation)",
        check_traceability(base, cv_duplicate),
    )


if __name__ == "__main__":
    main()
