"""Contrôle ATS autonome sur le PDF de démo (sans appel LLM).

Vérifie que le texte de output/cv_demo.pdf est réellement extractible : on attend
d'y retrouver le nom du profil et un fragment de chaque bullet de l'exemple
data/tailored.example.json. Lance d'abord scripts/render_demo.py si le PDF manque.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Rendre le package src/jobtool importable sans installation (cf. README).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jobtool.core.experience_base.loader import load_base
from jobtool.core.rendering.pdf_checks import AtsCheckError, check_pdf_ats
from jobtool.documents.cv.ats_targets import expected_strings
from jobtool.documents.cv.schema import TailoredCV

DATA_DIR = PROJECT_ROOT / "data"
BASE_PATH = DATA_DIR / "base.example.yaml"
CV_PATH = DATA_DIR / "tailored.example.json"
PDF_PATH = PROJECT_ROOT / "output" / "cv_demo.pdf"


def main() -> None:
    base = load_base(BASE_PATH)
    cv = TailoredCV.model_validate_json(CV_PATH.read_text(encoding="utf-8"))

    if not PDF_PATH.exists():
        raise SystemExit(
            f"Erreur : {PDF_PATH} introuvable.\n"
            "Lance d'abord : python scripts/render_demo.py"
        )

    expected = expected_strings(base, cv)
    print(f"Contrôle ATS sur {PDF_PATH}")
    print(f"{len(expected)} fragment(s) attendu(s).")

    try:
        missing = check_pdf_ats(str(PDF_PATH), expected)
    except AtsCheckError as exc:
        raise SystemExit(f"Erreur de contrôle ATS : {exc}")

    if not missing:
        print("PDF ATS OK — tous les fragments sont extractibles du PDF.")
    else:
        print(f"{len(missing)} fragment(s) NON retrouvé(s) dans l'extraction :")
        for fragment in missing:
            print(f"  - {fragment!r}")


if __name__ == "__main__":
    main()
