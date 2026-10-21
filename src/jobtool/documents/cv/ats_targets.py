"""Cibles ATS attendues dans le texte extrait d'un PDF de CV.

Module VOLONTAIREMENT léger : aucune dépendance au client LLM ni à la génération,
pour pouvoir l'importer (scripts SANS clé, contrôles, tests) sans tirer mistralai. Les
fonctions sont duck-typées (pas d'import de schéma) afin de rester neutres ; elles
attendent juste un `base` (avec .profile.name) et un `cv` (avec
.experiences[].bullets[].text).
"""

from __future__ import annotations


def bullet_fragment(text: str, words: int = 6) -> str:
    """Fragment court (premiers mots) d'une bullet, robuste aux retours à la ligne."""
    return " ".join(text.split()[:words])


def expected_strings(base, cv) -> list[str]:
    """Nom du profil + un fragment de chaque bullet du CV (cibles du contrôle ATS)."""
    expected = [base.profile.name]
    for exp in cv.experiences:
        for bullet in exp.bullets:
            expected.append(bullet_fragment(bullet.text))
    return expected
