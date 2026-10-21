"""Vérifications déterministes de la PASSE 2 de la lettre (le CODE qui VÉRIFIE).

Le LLM PROPOSE le TEXTE des paragraphes (jugement, ton, reformulation) ; ici on applique
des BORNES DURES et on renvoie les manquements comme `Violation` (réutilise core.traceability)
pour que run_correction_loop les repasse au modèle. On ne répare jamais : on détecte, on
redemande. Même format de Violation que cover_letter/routing.py.

Quatre familles de bornes (l'essentiel ; la relecture humaine reste le filet final) :
  - PLACEHOLDERS RÉSIDUELS : aucun marqueur « [qqch] » ne doit survivre dans un champ texte
    (un « [entreprise] » non substitué qui partirait à l'envoi serait CRITIQUE).
  - LONGUEUR : un plafond de caractères PAR champ (constantes nommées). Le LLM ne mesure
    JAMAIS ; le code mesure (len) et la boucle renvoie « raccourcis ».
  - COHÉRENCE MODE / INSERTS : un insert est présent SSI sa liste d'ids (passe 1) est non
    vide. En générique (listes vides), les DEUX inserts doivent être None.
  - ANTI-CHIFFRE INVENTÉ (insert_projet) : aucun NOMBRE absent du TEXTE des achievements
    sources ne doit apparaître dans l'insert projet (le risque principal d'une reformulation
    = un chiffre fabriqué). Garde volontairement SIMPLE et conservatrice (cf. _numbers) : on
    ne tente AUCUNE vérif sémantique de la reformulation, impossible à faire sûrement.

entreprise / domaine : PAS de traçabilité (données d'offre) — cohérent avec la passe 1. La
phrase libre sur l'entreprise dans paragraphe_vous est l'UNIQUE contenu non tracé, assumé.
"""

from __future__ import annotations

import re

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.traceability import Violation
from jobtool.documents.cover_letter.schema import (
    CoverLetterDraftOutput,
    CoverLetterRoutingOutput,
)

# --- Plafonds de longueur PAR champ (constantes nommées, modifiables ici) -----
# Valeurs pour une lettre d'½ page sobre (≈ 1500-1800 caractères de corps utile).
MAX_VOUS_CHARS = 700
MAX_MOI_CHARS = 800
MAX_NOUS_CHARS = 400
MAX_INSERT_INTERET_CHARS = 350
MAX_INSERT_PROJET_CHARS = 450
MAX_FORMULE_CHARS = 120

# Un placeholder résiduel = un trou « [ ... ] » laissé dans le texte (sans saut de ligne
# interne, pour ne pas confondre avec une vraie phrase entre crochets sur plusieurs lignes).
_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]+\]")
# Un « nombre » = une suite de chiffres (on compare des suites brutes : 12, 2025, 100...).
_NUMBER_RE = re.compile(r"\d+")


def _numbers(text: str) -> set[str]:
    """Suites de chiffres présentes dans `text` (comparaison brute, volontairement simple)."""
    return set(_NUMBER_RE.findall(text or ""))


def _is_absent(value: str | None) -> bool:
    """Un insert est « absent » s'il vaut None ou ne contient que des espaces."""
    return value is None or not value.strip()


def achievement_text_by_id(base: ExperienceBase) -> dict[str, str]:
    """Map id d'achievement -> son TEXTE exact (toutes expériences confondues)."""
    return {ach.id: ach.text for exp in base.experiences for ach in exp.achievements}


def _text_fields(draft: CoverLetterDraftOutput) -> list[tuple[str, str | None]]:
    """(label, valeur) de tous les champs textuels du draft, inserts None compris."""
    return [
        ("paragraphe_vous", draft.paragraphe_vous),
        ("insert_interet", draft.insert_interet),
        ("paragraphe_moi", draft.paragraphe_moi),
        ("insert_projet", draft.insert_projet),
        ("paragraphe_nous", draft.paragraphe_nous),
        ("formule", draft.formule),
    ]


def check_draft(
    base: ExperienceBase,
    routing: CoverLetterRoutingOutput,
    draft: CoverLetterDraftOutput,
) -> list[Violation]:
    """Retourne la liste des violations du draft (vide = OK). Ne lève jamais."""
    issues: list[Violation] = []

    # --- 1. Placeholders résiduels (CRITIQUE) --------------------------------
    for label, value in _text_fields(draft):
        if value is None:
            continue
        found = _PLACEHOLDER_RE.findall(value)
        if found:
            issues.append(
                Violation(
                    kind="residual_placeholder",
                    message=f"{label} : placeholder non substitué {found[0]} (à remplacer par le vrai texte)",
                )
            )

    # --- 2. Longueur par champ (le code mesure, jamais le LLM) ---------------
    limits = (
        ("paragraphe_vous", draft.paragraphe_vous, MAX_VOUS_CHARS),
        ("insert_interet", draft.insert_interet, MAX_INSERT_INTERET_CHARS),
        ("paragraphe_moi", draft.paragraphe_moi, MAX_MOI_CHARS),
        ("insert_projet", draft.insert_projet, MAX_INSERT_PROJET_CHARS),
        ("paragraphe_nous", draft.paragraphe_nous, MAX_NOUS_CHARS),
        ("formule", draft.formule, MAX_FORMULE_CHARS),
    )
    for label, value, max_chars in limits:
        if value is not None and len(value) > max_chars:
            issues.append(
                Violation(
                    kind="too_long",
                    message=f"{label} : trop long ({len(value)}>{max_chars} caractères), raccourcis",
                )
            )

    # --- 3. Cohérence mode / inserts -----------------------------------------
    # Règle unique : un insert est présent SSI sa liste d'ids (passe 1) est non vide.
    # En générique, passe 1 garantit les deux listes vides -> les deux inserts None.
    _check_insert_presence(
        issues, "insert_interet", draft.insert_interet, bool(routing.interets_ids)
    )
    _check_insert_presence(
        issues, "insert_projet", draft.insert_projet, bool(routing.projets_ids)
    )

    # --- 4. Anti-chiffre inventé sur insert_projet ---------------------------
    # Seulement si un insert projet est présent ET qu'on a des sources tracées : tout
    # nombre de l'insert doit figurer dans le TEXTE des achievements désignés par
    # projets_ids. Conservateur (peut sur-signaler une année), assumé : zéro chiffre
    # fabriqué qui survit. La reformulation elle-même n'est PAS vérifiée (impossible).
    if not _is_absent(draft.insert_projet) and routing.projets_ids:
        text_by_id = achievement_text_by_id(base)
        allowed: set[str] = set()
        for pid in routing.projets_ids:
            allowed |= _numbers(text_by_id.get(pid, ""))
        for num in sorted(_numbers(draft.insert_projet or "")):
            if num not in allowed:
                issues.append(
                    Violation(
                        kind="invented_number",
                        message=(
                            f"insert_projet : nombre « {num} » absent des achievements sources "
                            f"(ne pas inventer de chiffre ; reformule à partir du texte fourni)"
                        ),
                    )
                )

    return issues


def _check_insert_presence(
    issues: list[Violation], label: str, value: str | None, expected: bool
) -> None:
    """Ajoute une violation si la présence de l'insert ne correspond pas à `expected`."""
    absent = _is_absent(value)
    if expected and absent:
        issues.append(
            Violation(
                kind="missing_insert",
                message=f"{label} : attendu (liste d'ids non vide) mais vide/None",
            )
        )
    elif not expected and not absent:
        issues.append(
            Violation(
                kind="unexpected_insert",
                message=f"{label} : doit être None (aucun id correspondant en passe 1)",
            )
        )


def build_correction(issues: list[Violation]) -> str:
    """Message de correction repassé au modèle (liste les violations + la consigne)."""
    lines = [
        "Ton brouillon de lettre précédent doit être corrigé.",
        "",
        "Problèmes détectés :",
    ]
    lines += [f"- {v}" for v in issues]
    lines += [
        "",
        "Corrige SANS rien inventer : remplace tout placeholder « [..] » par le vrai texte ; "
        "raccourcis les champs signalés trop longs (sans bourrage ni formules creuses) ; "
        "respecte la présence des inserts (un insert n'existe que si sa liste d'ids est non "
        "vide, sinon il vaut None) ; n'introduis AUCUN chiffre absent des achievements "
        "sources. Garde un ton sobre, direct et humain.",
    ]
    return "\n".join(lines)
