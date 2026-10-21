"""Format de réponse du LLM (structured output) et modèles de propositions par nature.

Deux niveaux, volontairement :

1. `InterviewTurn` / `ProposalDraft` : ce que le LLM RENVOIE. `ProposalDraft` est PLAT (tous
   les champs de toutes les natures, optionnels) : un schéma à union discriminée est mal
   supporté par le structured output, et un champ manquant doit devenir un message de
   correction lisible, pas un rejet opaque côté fournisseur.
2. `PAYLOADS` : un modèle STRICT par nature, aligné sur `ExperienceBase` (mêmes formats de
   date, mêmes types d'expérience). Un brouillon n'est une proposition que s'il s'y valide.
   Les modifications faites par l'utilisateur passent par les MÊMES modèles.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from jobtool.core.experience_base.models import ExperienceType, _check_year_month
from jobtool.interview.models import ProposalKind, Section

# --- Ce que renvoie le LLM ----------------------------------------------------


class ProposalDraft(BaseModel):
    """Une proposition telle que le LLM la formule. Champs selon `kind` (voir le prompt)."""

    kind: ProposalKind
    sources: list[
        str
    ] = []  # ids des messages utilisateur (« m3 ») qui fondent la proposition
    # identite
    field: str | None = None
    value: str | None = None
    # formation
    degree: str | None = None
    school: str | None = None
    # formation + experience
    start: str | None = None
    end: str | None = None
    # experience
    experience_type: ExperienceType | None = None
    title: str | None = None
    organization: str | None = None
    summary: str | None = None
    # realisation
    experience_ref: str | None = None  # id d'expérience existante, ou « pN » en attente
    text: str | None = None
    skills: list[str] = []
    # competence + langue
    name: str | None = None
    category: Literal["hard", "soft"] | None = None
    realisation_ref: str | None = (
        None  # id de réalisation existante, ou « pN » en attente
    )
    evidence: str | None = None  # soft skill : l'exemple concret qui l'appuie
    level: str | None = None


class InterviewTurn(BaseModel):
    """Une réponse de l'intervieweur."""

    message: str
    proposals: list[ProposalDraft] = []
    #: Le LLM JUGE que la section en cours est couverte. C'est le code qui déplace le curseur.
    section_complete: bool = False
    #: Faits de contexte sans champ dans le profil (situation, recherche). Jamais écrits.
    context_notes: list[str] = []


class ContextSummary(BaseModel):
    """Résumé des messages sortis de la fenêtre d'historique."""

    summary: str


# --- Modèles stricts par nature ---------------------------------------------


def _clean(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("champ vide")
    return value


class _Payload(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


IDENTITY_FIELDS = ("name", "email", "phone", "location", "link")


class IdentitePayload(_Payload):
    field: Literal["name", "email", "phone", "location", "link"]
    value: str

    @field_validator("value")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _clean(v)


class FormationPayload(_Payload):
    degree: str
    school: str
    start: str
    end: str

    @field_validator("degree", "school")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _clean(v)

    @field_validator("start")
    @classmethod
    def _start(cls, v: str) -> str:
        return _check_year_month(v, allow_present=False)

    @field_validator("end")
    @classmethod
    def _end(cls, v: str) -> str:
        return _check_year_month(v, allow_present=True)


class LanguePayload(_Payload):
    name: str
    level: str = ""

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _clean(v)


class ExperiencePayload(_Payload):
    experience_type: ExperienceType
    title: str
    organization: str
    start: str
    end: str
    summary: str | None = None

    @field_validator("title", "organization")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _clean(v)

    @field_validator("start")
    @classmethod
    def _start(cls, v: str) -> str:
        return _check_year_month(v, allow_present=False)

    @field_validator("end")
    @classmethod
    def _end(cls, v: str) -> str:
        return _check_year_month(v, allow_present=True)


class RealisationPayload(_Payload):
    experience_ref: str
    text: str
    skills: list[str] = []

    @field_validator("text", "experience_ref")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _clean(v)

    @field_validator("skills")
    @classmethod
    def _skills(cls, v: list[str]) -> list[str]:
        return [" ".join(s.split()) for s in v if s and s.strip()]


class CompetencePayload(_Payload):
    name: str
    category: Literal["hard", "soft"]
    realisation_ref: str | None = None
    evidence: str | None = None

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _clean(v)


PAYLOADS: dict[ProposalKind, type[_Payload]] = {
    ProposalKind.identite: IdentitePayload,
    ProposalKind.formation: FormationPayload,
    ProposalKind.langue: LanguePayload,
    ProposalKind.experience: ExperiencePayload,
    ProposalKind.realisation: RealisationPayload,
    ProposalKind.competence: CompetencePayload,
}

#: Section de l'entretien à laquelle se rattache une nature de proposition (affichage et
#: progression). Une expérience de type projet est rangée dans « projets » par `section_of`.
_KIND_SECTION: dict[ProposalKind, Section] = {
    ProposalKind.identite: Section.situation,
    ProposalKind.langue: Section.situation,
    ProposalKind.formation: Section.formation,
    ProposalKind.experience: Section.experiences,
    ProposalKind.realisation: Section.experiences,
    ProposalKind.competence: Section.competences,
}


def section_of(kind: ProposalKind, payload: dict, fallback: Section) -> Section:
    if kind is ProposalKind.experience and payload.get("experience_type") in (
        "school_project",
        "personal_project",
    ):
        return Section.projets
    if kind is ProposalKind.realisation:
        return (
            fallback
            if fallback in (Section.experiences, Section.projets)
            else (Section.experiences)
        )
    return _KIND_SECTION[kind]


def validate_payload(kind: ProposalKind, data: dict) -> tuple[dict | None, list[str]]:
    """Valide `data` contre le modèle de `kind`. Renvoie (payload normalisé, erreurs)."""
    model = PAYLOADS[kind]
    fields = {k: v for k, v in data.items() if k in model.model_fields}
    try:
        payload = model.model_validate(fields)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(p) for p in err['loc']) or kind.value} : {err['msg']}"
            for err in exc.errors()
        ]
        return None, errors
    return payload.model_dump(mode="json"), []


def draft_fields(draft: ProposalDraft) -> dict:
    """Les champs renseignés d'un brouillon (sans `kind` ni `sources`)."""
    data = draft.model_dump(mode="json", exclude={"kind", "sources"})
    return {k: v for k, v in data.items() if v not in (None, [], "")}


__all__ = [
    "IDENTITY_FIELDS",
    "PAYLOADS",
    "CompetencePayload",
    "ContextSummary",
    "ExperiencePayload",
    "FormationPayload",
    "IdentitePayload",
    "InterviewTurn",
    "LanguePayload",
    "ProposalDraft",
    "RealisationPayload",
    "draft_fields",
    "section_of",
    "validate_payload",
]
