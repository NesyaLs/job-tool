"""Vérifications déterministes de la PASSE 1 de la lettre (le CODE qui VÉRIFIE).

Le LLM PROPOSE (entreprise, domaine, mode, ids) ; ici on applique des BORNES DURES et on
renvoie les manquements comme `Violation` (réutilise core.traceability.Violation) pour que
run_correction_loop les repasse au modèle. On ne répare jamais : on détecte et on redemande.

Trois familles de bornes :
  - TRAÇABILITÉ : chaque id de projets_ids ∈ achievements de PROJET de la base ; chaque id
    de interets_ids ∈ centres d'intérêt de la base (l'intitulé EST l'id). Inconnu = violation.
  - COHÉRENCE DU MODE : « cible » exige >= 1 id VALIDE ; « generique » exige ZÉRO id.
  - HYGIÈNE DE FORMAT (entreprise, domaine) : non vide, <= max, pas de retour à la ligne.
    PAS de traçabilité dessus (ce sont des données d'offre, pas des faits du profil).
"""

from __future__ import annotations

from jobtool.core.experience_base.models import PROJECT_TYPES, ExperienceBase
from jobtool.core.traceability import Violation
from jobtool.documents.cover_letter.schema import CoverLetterRoutingOutput

# Bornes de format (constantes nommées, modifiables ici).
MAX_ENTREPRISE_CHARS = 100
MAX_DOMAINE_CHARS = 80

# Une lettre ne parle que d'UN SEUL projet distinct (sinon elle vire au catalogue).
# C'est le nombre de PROJETS DISTINCTS qui est plafonné, PAS le nombre d'achievements :
# plusieurs achievements d'un même projet restent valides.
MAX_PROJETS_DISTINCTS_LETTRE = 1


def achievement_to_project(base: ExperienceBase) -> dict[str, str]:
    """Map chaque id d'achievement de PROJET vers l'id de SON expérience (le projet).

    Lien établi par APPARTENANCE (l'achievement vit dans l'expérience), pas par
    découpage de chaîne du source_id : c'est la même résolution by_id que le CV
    (cf. experiences_by_id dans cv/render.py), pas une heuristique fragile.
    """
    return {
        ach.id: exp.id
        for exp in base.experiences
        if exp.type.value in PROJECT_TYPES
        for ach in exp.achievements
    }


def project_achievement_ids(base: ExperienceBase) -> set[str]:
    """Ids d'achievements appartenant à une expérience de type PROJET (école/perso)."""
    return set(achievement_to_project(base))


def interest_ids(base: ExperienceBase) -> set[str]:
    """Centres d'intérêt de la base : l'intitulé exact tient lieu d'id."""
    return set(base.interests)


def _check_format(label: str, value: str, max_chars: int) -> list[Violation]:
    """Hygiène de format d'un champ d'offre (entreprise/domaine) : non vide, mono-ligne, borné."""
    if not value or not value.strip():
        return [Violation(kind="format_vide", message=f"{label} : vide")]
    issues: list[Violation] = []
    if "\n" in value or "\r" in value:
        issues.append(
            Violation(
                kind="format_multiligne",
                message=f"{label} : contient un retour à la ligne",
            )
        )
    if len(value) > max_chars:
        issues.append(
            Violation(
                kind="format_trop_long",
                message=f"{label} : trop long ({len(value)}>{max_chars} caractères)",
            )
        )
    return issues


def check_routing(
    base: ExperienceBase, output: CoverLetterRoutingOutput
) -> list[Violation]:
    """Retourne la liste des violations de la décision de routage (vide = OK)."""
    issues: list[Violation] = []
    proj_ids = project_achievement_ids(base)
    int_ids = interest_ids(base)

    # --- Traçabilité des ids -------------------------------------------------
    for pid in output.projets_ids:
        if pid not in proj_ids:
            issues.append(
                Violation(
                    kind="unknown_project_achievement",
                    message=f"id de projet inconnu : {pid}",
                )
            )
    for iid in output.interets_ids:
        if iid not in int_ids:
            issues.append(
                Violation(
                    kind="unknown_interest", message=f"centre d'intérêt inconnu : {iid}"
                )
            )

    # --- Cohérence du mode (sur les ids VALIDES après filtrage) --------------
    valid_proj = [p for p in output.projets_ids if p in proj_ids]
    valid_int = [i for i in output.interets_ids if i in int_ids]
    if output.mode == "cible" and not (valid_proj or valid_int):
        issues.append(
            Violation(
                kind="incoherent_mode",
                message="mode ciblé annoncé sans matériel tracé (aucun id projet/intérêt valide)",
            )
        )
    if output.mode == "generique" and (output.projets_ids or output.interets_ids):
        issues.append(
            Violation(
                kind="incoherent_mode",
                message="mode générique mais des ids sont fournis (projets_ids et interets_ids doivent être vides)",
            )
        )

    # --- Borne : AU PLUS 1 projet distinct (interets_ids NON concernés) ------
    # On compte les PROJETS distincts (expériences) couverts par les ids VALIDES,
    # pas le nombre d'ids : 3 achievements du même projet = 1 projet distinct.
    ach_to_proj = achievement_to_project(base)
    distinct_projects = {ach_to_proj[p] for p in valid_proj}
    if len(distinct_projects) > MAX_PROJETS_DISTINCTS_LETTRE:
        issues.append(
            Violation(
                kind="too_many_projects",
                message=(
                    f"trop de projets distincts : {len(distinct_projects)} > "
                    f"{MAX_PROJETS_DISTINCTS_LETTRE} (une lettre ne parle que d'un seul projet)"
                ),
            )
        )

    # --- Hygiène de format (PAS de traçabilité) ------------------------------
    issues += _check_format("entreprise", output.entreprise, MAX_ENTREPRISE_CHARS)
    issues += _check_format("domaine", output.domaine, MAX_DOMAINE_CHARS)

    return issues


def build_correction(base: ExperienceBase, issues: list[Violation]) -> str:
    """Message de correction repassé au modèle (violations + aide ciblée + consigne).

    `base` sert UNIQUEMENT à re-lister, dans le MESSAGE, les ids d'achievement VALIDES (aide
    au LLM). La borne `check_routing` n'est PAS touchée : un id invalide reste rejeté. Quand un
    id de projet est rejeté, on nomme explicitement la confusion id d'EXPÉRIENCE (« projet-xxx »)
    vs id d'ACHIEVEMENT (« projet-xxx.N ») et on redonne la liste autorisée.
    """
    lines = [
        "Ta décision de routage précédente doit être corrigée.",
        "",
        "Problèmes détectés :",
    ]
    lines += [f"- {v}" for v in issues]

    # Aide CIBLÉE quand un id de projet est rejeté : la cause habituelle est que le LLM a
    # donné l'id d'EXPÉRIENCE (« projet-xxx ») au lieu d'un id d'ACHIEVEMENT (« projet-xxx.N »).
    if any(v.kind == "unknown_project_achievement" for v in issues):
        valid_ids = sorted(project_achievement_ids(base))
        lines += [
            "",
            "Un id de projet DOIT être un id d'ACHIEVEMENT au format « projet-xxx.N » (ex. "
            "« projet-noyaux-hpc.1 »). « projet-xxx » SANS suffixe .N est un id d'EXPÉRIENCE : "
            "il est INVALIDE ici — remplace-le par UN de ses achievements .N.",
            "Ids d'achievement de PROJET valides (recopie-les EXACTEMENT, sans en inventer) :",
            *[f"  - {vid}" for vid in valid_ids],
        ]

    lines += [
        "",
        "Corrige : n'utilise QUE des ids EXISTANTS parmi ceux listés dans le contexte plus "
        "haut (projets_ids = source_id d'achievements de PROJET ; interets_ids = intitulés "
        "EXACTS de centres d'intérêt), sans en inventer. Respecte la cohérence du mode : "
        "« cible » => au moins un id valide ; « generique » => aucun id. Garde entreprise et "
        "domaine courts et sur UNE seule ligne.",
    ]
    return "\n".join(lines)
