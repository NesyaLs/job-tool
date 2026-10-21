"""Validateur d'anti-fabrication.

Pièce critique du projet : vérifier qu'un CV produit par le LLM ne référence
QUE des éléments réellement présents dans la base. La fonction NE LÈVE PAS
d'exception — elle retourne une liste de violations (vide = tout bon) — pour que
l'étape LLM (plus tard) puisse décider quoi en faire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from jobtool.core.experience_base.models import ExperienceBase

if TYPE_CHECKING:
    # Référence de type seulement : core ne doit PAS importer cv/ à l'exécution.
    # OK car `from __future__ import annotations` garde les annotations en chaînes.
    from jobtool.documents.cv.schema import TailoredCV


class Violation(BaseModel):
    """Un manquement à la traçabilité, avec un message lisible."""

    kind: str  # unknown_experience | unknown_achievement | duplicate_achievement
    #              | invented_hard_skill | invented_soft_skill | invented_interest
    #              | missing_pinned_experience
    message: str

    def __str__(self) -> str:
        return self.message


def check_traceability(base: ExperienceBase, cv: TailoredCV) -> list[Violation]:
    """Retourne la liste des violations de traçabilité du CV vis-à-vis de la base."""
    violations: list[Violation] = []

    experiences_by_id = {exp.id: exp for exp in base.experiences}

    # Règles 1 & 2 : chaque expérience existe, et chaque bullet pointe vers un
    # achievement appartenant bien à CETTE expérience (vérifié via la structure
    # de la base, pas en parsant la convention d'id "{exp}.{n}").
    for tailored_exp in cv.experiences:
        base_exp = experiences_by_id.get(tailored_exp.source_experience_id)
        if base_exp is None:
            violations.append(
                Violation(
                    kind="unknown_experience",
                    message=f"expérience inconnue : {tailored_exp.source_experience_id}",
                )
            )
            continue  # sans l'expérience réelle, impossible de valider ses bullets

        achievement_ids = {ach.id for ach in base_exp.achievements}
        for bullet in tailored_exp.bullets:
            if bullet.source_id not in achievement_ids:
                violations.append(
                    Violation(
                        kind="unknown_achievement",
                        message=(
                            f"achievement {bullet.source_id} introuvable dans "
                            f"l'expérience {tailored_exp.source_experience_id}"
                        ),
                    )
                )

    # Règle 3 : aucun achievement utilisé plus d'une fois dans tout le CV.
    seen: set[str] = set()
    for tailored_exp in cv.experiences:
        for bullet in tailored_exp.bullets:
            if bullet.source_id in seen:
                violations.append(
                    Violation(
                        kind="duplicate_achievement",
                        message=f"achievement utilisé en double : {bullet.source_id}",
                    )
                )
            else:
                seen.add(bullet.source_id)

    # Règle 4 : hard skills, soft skills et centres d'intérêt doivent être
    # SÉLECTIONNÉS à l'identique dans les listes maîtres de la base (aucune
    # invention/reformulation).
    master_hard_skills = set(base.hard_skills)
    for skill in cv.hard_skills:
        if skill not in master_hard_skills:
            violations.append(
                Violation(
                    kind="invented_hard_skill", message=f"hard skill inventé : {skill}"
                )
            )

    master_soft_skills = set(base.soft_skills)
    for skill in cv.soft_skills:
        if skill not in master_soft_skills:
            violations.append(
                Violation(
                    kind="invented_soft_skill", message=f"soft skill inventé : {skill}"
                )
            )

    master_interests = set(base.interests)
    for interest in cv.interests:
        if interest not in master_interests:
            violations.append(
                Violation(
                    kind="invented_interest",
                    message=f"centre d'intérêt inventé : {interest}",
                )
            )

    # Règle 5 : toute expérience ÉPINGLÉE de la base doit figurer dans le CV.
    present_experience_ids = {texp.source_experience_id for texp in cv.experiences}
    for exp in base.experiences:
        if exp.pinned and exp.id not in present_experience_ids:
            violations.append(
                Violation(
                    kind="missing_pinned_experience",
                    message=f"expérience épinglée absente : {exp.id}",
                )
            )

    return violations
