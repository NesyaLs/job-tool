"""Application des propositions ACCEPTÉES à la base d'expériences (fonctions pures).

C'est le seul endroit où une proposition devient une donnée du profil, et il n'est appelé
que sur décision explicite de l'utilisateur. Plusieurs propositions acceptées ensemble sont
appliquées en UNE passe, puis la base est écrite une seule fois par l'appelant.

Les ids stables de la base (`Experience.id`, `Achievement.id`, convention « {exp}.{n} »)
sont attribués ICI, par le code. Une réalisation qui cite une expérience encore en attente
(« p3 ») est rattachée à l'id créé pour elle — ce qui impose d'accepter l'expérience avant,
ou en même temps.
"""

from __future__ import annotations

import re
import unicodedata

from jobtool.core.experience_base.models import (
    Achievement,
    Education,
    Experience,
    ExperienceBase,
    ExperienceType,
    Language,
    Profile,
)
from jobtool.interview.checks import existing_skill, skill_key
from jobtool.interview.models import Proposal, ProposalKind


class ApplyError(ValueError):
    """Proposition inapplicable en l'état (référence non encore acceptée, doublon…)."""


#: Ordre d'application dans un lot : ce qui est référencé passe avant ce qui référence.
APPLY_ORDER = {
    ProposalKind.identite: 0,
    ProposalKind.formation: 1,
    ProposalKind.langue: 2,
    ProposalKind.experience: 3,
    ProposalKind.realisation: 4,
    ProposalKind.competence: 5,
}


def empty_base() -> ExperienceBase:
    """Base d'un compte qui n'en a pas encore : identité vide, à compléter."""
    return ExperienceBase(profile=Profile(name="", email=""), experiences=[])


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "exp"


def _unique(candidate: str, taken: set[str]) -> str:
    if candidate not in taken:
        return candidate
    n = 2
    while f"{candidate}-{n}" in taken:
        n += 1
    return f"{candidate}-{n}"


def education_key(degree: str, school: str) -> str:
    """Id de provenance d'une formation (le modèle `Education` n'a pas d'id propre)."""
    return f"education:{degree}|{school}"


def apply_proposals(
    base: ExperienceBase | None, proposals: list[Proposal], resolved: dict[str, str]
) -> tuple[ExperienceBase, dict[str, str]]:
    """Applique `proposals` (déjà acceptées) et renvoie (nouvelle base, pN -> id créé).

    `resolved` : propositions acceptées AUPARAVANT et l'id qu'elles ont créé, pour résoudre
    les références « pN » vers des éléments déjà dans la base.
    """
    data = (base or empty_base()).model_copy(deep=True)
    created: dict[str, str] = {}
    refs = dict(resolved)
    for proposal in sorted(proposals, key=lambda p: APPLY_ORDER[p.kind]):
        element_id = _apply_one(data, proposal, refs)
        created[proposal.id] = element_id
        refs[proposal.id] = element_id
    # Revalidation complète : unicité des ids, formats de date, doublons de listes maîtres.
    return ExperienceBase.model_validate(data.model_dump()), created


def _apply_one(base: ExperienceBase, proposal: Proposal, refs: dict[str, str]) -> str:
    p = proposal.payload
    kind = proposal.kind

    if kind is ProposalKind.identite:
        field, value = p["field"], p["value"]
        if field == "link":
            links = list(base.profile.links or [])
            if value not in links:
                links.append(value)
            base.profile.links = links
        else:
            setattr(base.profile, field, value)
        return f"profile:{field}"

    if kind is ProposalKind.formation:
        key = education_key(p["degree"], p["school"])
        if any(education_key(e.degree, e.school) == key for e in base.education):
            raise ApplyError(f"formation déjà présente : {p['degree']} — {p['school']}")
        base.education.append(
            Education(
                degree=p["degree"], school=p["school"], start=p["start"], end=p["end"]
            )
        )
        return key

    if kind is ProposalKind.langue:
        if any(lang.name.lower() == p["name"].lower() for lang in base.languages):
            raise ApplyError(f"langue déjà présente : {p['name']}")
        base.languages.append(Language(name=p["name"], level=p.get("level") or ""))
        return f"language:{p['name']}"

    if kind is ProposalKind.experience:
        taken = {e.id for e in base.experiences}
        exp_id = _unique(f"{_slug(p['title'])}-{p['start'][:4]}", taken)
        base.experiences.append(
            Experience(
                id=exp_id,
                type=ExperienceType(p["experience_type"]),
                title=p["title"],
                organization=p["organization"],
                start=p["start"],
                end=p["end"],
                summary=p.get("summary") or None,
            )
        )
        return exp_id

    if kind is ProposalKind.realisation:
        exp_id = refs.get(p["experience_ref"], p["experience_ref"])
        exp = next((e for e in base.experiences if e.id == exp_id), None)
        if exp is None:
            raise ApplyError(
                "la réalisation se rattache à une expérience pas encore acceptée "
                f"({p['experience_ref']}) : accepte d'abord l'expérience"
            )
        taken = {a.id for e in base.experiences for a in e.achievements}
        n = len(exp.achievements) + 1
        while f"{exp.id}.{n}" in taken:
            n += 1
        ach_id = f"{exp.id}.{n}"
        exp.achievements.append(
            Achievement(id=ach_id, text=p["text"], skills=_dedup(p.get("skills") or []))
        )
        return ach_id

    # compétence
    name, category = p["name"], p["category"]
    known = existing_skill(base, name)
    if known is not None:
        category, name = known
    else:
        target = base.hard_skills if category == "hard" else base.soft_skills
        target.append(name)
    ref = p.get("realisation_ref")
    if ref:
        ach_id = refs.get(ref, ref)
        ach = next(
            (a for e in base.experiences for a in e.achievements if a.id == ach_id),
            None,
        )
        if ach is None:
            raise ApplyError(
                "la compétence se rattache à une réalisation pas encore acceptée "
                f"({ref}) : accepte d'abord la réalisation"
            )
        if skill_key(name) not in {skill_key(s) for s in ach.skills}:
            ach.skills.append(name)
    return f"{category}:{name}"


def _dedup(skills: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in skills:
        if skill_key(s) not in seen:
            seen.add(skill_key(s))
            out.append(s)
    return out


__all__ = [
    "APPLY_ORDER",
    "ApplyError",
    "apply_proposals",
    "education_key",
    "empty_base",
]
