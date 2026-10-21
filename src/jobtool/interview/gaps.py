"""Détection DÉTERMINISTE des manques d'un profil existant.

Le code repère ce qui manque (section vide, projet sans réalisation, réalisation sans
compétence…) ; le LLM reçoit cette liste et choisit comment creuser. Un manque n'est jamais
« deviné » par le modèle : c'est une mesure, donc du code.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobtool.core.experience_base.models import PROJECT_TYPES, ExperienceBase
from jobtool.interview.models import SECTION_ORDER, Section

#: En dessous, une liste de compétences est jugée « à étoffer ».
MIN_HARD_SKILLS = 5


@dataclass(frozen=True)
class Gap:
    section: Section
    kind: str  # identifiant stable du type de manque (tests, front)
    message: str  # formulation lisible, montrée au LLM et à l'utilisateur
    target_id: str | None = None  # expérience ou réalisation concernée


def _section_of_experience(exp_type: str) -> Section:
    return Section.projets if exp_type in PROJECT_TYPES else Section.experiences


def find_gaps(base: ExperienceBase | None) -> list[Gap]:
    """Manques du profil, dans l'ordre du plan de l'entretien. Profil absent : tout manque."""
    if base is None or is_empty(base):
        return [Gap(s, "empty_section", "section vide") for s in SECTION_ORDER]

    gaps: list[Gap] = []
    profile = base.profile
    missing_identity = [
        label
        for label, value in (
            ("nom", profile.name),
            ("e-mail", profile.email),
            ("téléphone", profile.phone),
            ("ville", profile.location),
        )
        if not (value or "").strip()
    ]
    if missing_identity:
        gaps.append(
            Gap(
                Section.situation,
                "identity_incomplete",
                "coordonnées manquantes : " + ", ".join(missing_identity),
            )
        )
    if not base.languages:
        gaps.append(Gap(Section.situation, "no_language", "aucune langue renseignée"))
    if not base.education:
        gaps.append(Gap(Section.formation, "empty_section", "aucune formation"))

    jobs = [e for e in base.experiences if e.type.value not in PROJECT_TYPES]
    projects = [e for e in base.experiences if e.type.value in PROJECT_TYPES]
    if not jobs:
        gaps.append(Gap(Section.experiences, "empty_section", "aucune expérience"))
    if not projects:
        gaps.append(Gap(Section.projets, "empty_section", "aucun projet"))

    for exp in base.experiences:
        section = _section_of_experience(exp.type.value)
        label = f"« {exp.title} » ({exp.organization})"
        if not exp.achievements:
            gaps.append(
                Gap(
                    section, "no_achievement", f"{label} n'a aucune réalisation", exp.id
                )
            )
            continue
        if not exp.summary:
            gaps.append(
                Gap(section, "no_summary", f"{label} n'a pas de résumé", exp.id)
            )
        bare = [a for a in exp.achievements if not a.skills]
        if bare:
            gaps.append(
                Gap(
                    section,
                    "achievement_without_skills",
                    f"{label} : {len(bare)} réalisation(s) sans compétence associée",
                    exp.id,
                )
            )

    if len(base.hard_skills) < MIN_HARD_SKILLS:
        gaps.append(
            Gap(
                Section.competences,
                "few_hard_skills",
                f"seulement {len(base.hard_skills)} compétence(s) technique(s)",
            )
        )
    if not base.soft_skills:
        gaps.append(Gap(Section.competences, "no_soft_skill", "aucune soft skill"))
    return gaps


def is_empty(base: ExperienceBase | None) -> bool:
    """Profil sans contenu (ni expérience, ni formation, ni compétence)."""
    return base is None or not (
        base.experiences or base.education or base.hard_skills or base.soft_skills
    )


def sections_with_gaps(gaps: list[Gap]) -> list[Section]:
    """Sections concernées par au moins un manque, dans l'ordre du plan."""
    concerned = {g.section for g in gaps}
    return [s for s in SECTION_ORDER if s in concerned]


__all__ = ["MIN_HARD_SKILLS", "Gap", "find_gaps", "is_empty", "sections_with_gaps"]
