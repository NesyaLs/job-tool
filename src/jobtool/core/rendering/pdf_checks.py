"""Contrôle ATS d'un PDF : le texte est-il réellement extractible ?

Indépendant du LLM. On extrait le texte du PDF via le binaire `pdftotext` (paquet
poppler-utils) et on vérifie que des chaînes attendues (nom, fragments de bullets)
s'y retrouvent. Si une bullet visible à l'écran n'apparaît pas dans l'extraction,
c'est un signal d'alerte ATS (PDF image, ordre cassé, ligature, etc.).
"""

from __future__ import annotations

import shutil
import subprocess

_INSTALL_HINT = (
    "binaire `pdftotext` introuvable. Installe-le (paquet poppler-utils) :\n"
    "  - Debian/Ubuntu : sudo apt install poppler-utils\n"
    "  - Fedora        : sudo dnf install poppler-utils\n"
    "  - macOS (brew)  : brew install poppler\n"
    "Puis vérifie avec : pdftotext -v"
)

_PDFINFO_HINT = (
    "binaire `pdfinfo` introuvable (paquet poppler-utils, comme pdftotext).\n"
    "  - Debian/Ubuntu : sudo apt install poppler-utils\n"
    "Puis vérifie avec : pdfinfo -v"
)


class AtsCheckError(RuntimeError):
    """Erreur lisible du contrôle ATS (binaire absent, extraction en échec)."""


def extract_pdf_text(pdf_path: str) -> str:
    """Extrait le texte d'un PDF via `pdftotext -layout <pdf> -` (stdout)."""
    if shutil.which("pdftotext") is None:
        raise AtsCheckError(_INSTALL_HINT)

    try:
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise AtsCheckError(_INSTALL_HINT)

    if result.returncode != 0:
        raise AtsCheckError(
            "échec de l'extraction pdftotext :\n"
            + (result.stderr.strip() or "(pas de détail)")
        )

    return result.stdout


# Apostrophes typographiques -> droite. Un template peut afficher « L’Oréal » pour une
# donnée « L'Oréal » : c'est le même texte pour un lecteur comme pour un ATS.
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'"})


def _normalize_apostrophes(text: str) -> str:
    return text.translate(_APOSTROPHES)


def check_pdf_ats(pdf_path: str, expected_strings: list[str]) -> list[str]:
    """Retourne les chaînes attendues ABSENTES du texte extrait (vide = PDF lisible).

    La comparaison ignore la forme des apostrophes (droite ou typographique).
    """
    text = _normalize_apostrophes(extract_pdf_text(pdf_path))
    return [
        expected
        for expected in expected_strings
        if _normalize_apostrophes(expected) not in text
    ]


def count_pdf_pages(pdf_path: str) -> int:
    """Nombre de pages d'un PDF, via `pdfinfo` (ligne « Pages: N »)."""
    if shutil.which("pdfinfo") is None:
        raise AtsCheckError(_PDFINFO_HINT)

    try:
        result = subprocess.run(
            ["pdfinfo", pdf_path],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise AtsCheckError(_PDFINFO_HINT)

    if result.returncode != 0:
        raise AtsCheckError(
            "échec de pdfinfo :\n" + (result.stderr.strip() or "(pas de détail)")
        )

    # pdfinfo peut émettre des avertissements ; on cherche la ligne « Pages: N ».
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())

    raise AtsCheckError("pdfinfo n'a pas renvoyé de nombre de pages exploitable.")


def page_size_pt(pdf_path: str) -> tuple[float, float]:
    """(largeur, hauteur) de la page en points PDF, via `pdfinfo` (ligne « Page size: »).

    Sert à exprimer les boîtes des champs éditables en POURCENTAGES de la page : un
    aperçu affiché à n'importe quelle taille garde alors ses zones bien superposées.
    """
    if shutil.which("pdfinfo") is None:
        raise AtsCheckError(_PDFINFO_HINT)

    try:
        result = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True)
    except FileNotFoundError:
        raise AtsCheckError(_PDFINFO_HINT)

    if result.returncode != 0:
        raise AtsCheckError(
            "échec de pdfinfo :\n" + (result.stderr.strip() or "(pas de détail)")
        )

    # Format attendu : « Page size:      595.276 x 841.89 pts (A4) ».
    for line in result.stdout.splitlines():
        if line.startswith("Page size:"):
            parts = line.split(":", 1)[1].split()
            try:
                return float(parts[0]), float(parts[2])
            except (IndexError, ValueError):
                break

    raise AtsCheckError("pdfinfo n'a pas renvoyé de taille de page exploitable.")


def check_single_page(pdf_path: str) -> list[str]:
    """Retourne une violation lisible si le PDF fait plus d'1 page (vide = 1 page).

    Ne lève pas sur la condition « > 1 page » (c'est un rapport, comme check_budget) ;
    une erreur d'environnement (pdfinfo absent) reste signalée via AtsCheckError.
    """
    pages = count_pdf_pages(pdf_path)
    if pages > 1:
        return [f"le CV fait {pages} pages, attendu 1"]
    return []
