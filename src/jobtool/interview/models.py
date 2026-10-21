"""État d'une session d'entretien : messages, propositions, sections, provenance.

Une session est un document Pydantic sérialisé en JSON (une ligne par compte, cf.
`store.py`), pour la même raison que la base d'expériences : l'état est lu et réécrit en
entier à chaque tour, jamais requêté de l'intérieur.

Les identifiants de messages (`m1`, `m2`…) et de propositions (`p1`, `p2`…) sont attribués
par le CODE, séquentiellement dans la session. Le LLM les voit et peut les citer (source d'une
proposition, expérience en attente à laquelle rattacher une réalisation) ; le code vérifie
qu'ils existent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Section(str, Enum):
    """Sections de l'entretien, dans l'ordre du plan."""

    situation = "situation"
    formation = "formation"
    experiences = "experiences"
    projets = "projets"
    competences = "competences"


#: Ordre du plan et libellés affichés. Les libellés sont regroupés ici (et nulle part
#: ailleurs dans le domaine) : un jour de traduction, c'est la seule table à dédoubler.
SECTION_ORDER: tuple[Section, ...] = tuple(Section)
SECTION_LABELS: dict[Section, str] = {
    Section.situation: "Situation actuelle",
    Section.formation: "Formation",
    Section.experiences: "Expériences",
    Section.projets: "Projets",
    Section.competences: "Compétences",
}

SectionStatus = Literal["todo", "current", "done", "skipped"]


class ProposalKind(str, Enum):
    """Nature d'une proposition, alignée sur les zones de `ExperienceBase`."""

    identite = "identite"  # un champ de `profile` (nom, e-mail, téléphone, ville, lien)
    formation = "formation"  # une ligne de `education`
    langue = "langue"  # une ligne de `languages`
    experience = "experience"  # une ligne de `experiences` (stage, job, projet)
    realisation = "realisation"  # un `Achievement` rattaché à une expérience
    competence = "competence"  # une entrée de hard_skills / soft_skills


ProposalStatus = Literal["pending", "accepted", "rejected"]


class Message(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    text: str
    section: Section
    created_at: str = Field(default_factory=now_iso)


class Proposal(BaseModel):
    """Une proposition du LLM, VALIDÉE par le code, en attente de décision.

    `payload` suit le modèle propre à `kind` (cf. `schema.PAYLOADS`). `warnings` sont les
    signalements du garde-fou ; `blocking` interdit l'acceptation TELLE QUELLE (il faut la
    modifier d'abord). `result_id` est l'élément créé dans la base à l'acceptation.
    """

    id: str
    kind: ProposalKind
    payload: dict[str, Any]
    status: ProposalStatus = "pending"
    sources: list[str] = []  # ids des messages UTILISATEUR d'où vient la proposition
    message_id: str = ""  # message de l'assistant qui l'a formulée
    section: Section = Section.situation
    warnings: list[str] = []
    blocking: bool = False
    edited: bool = False
    result_id: str | None = None
    created_at: str = Field(default_factory=now_iso)
    decided_at: str | None = None


def _fresh_plan() -> dict[Section, SectionStatus]:
    return {s: "todo" for s in SECTION_ORDER}


class InterviewSession(BaseModel):
    """Toute la session d'un compte. Aucune donnée d'un autre compte n'y figure."""

    messages: list[Message] = []
    proposals: list[Proposal] = []
    sections: dict[Section, SectionStatus] = Field(default_factory=_fresh_plan)
    current: Section = Section.situation
    #: Faits de contexte (section « situation actuelle ») sans champ dans le profil : type
    #: de poste recherché, niveau d'études… Utilisés pour guider l'entretien, jamais écrits.
    context_notes: list[str] = []
    #: Résumé des messages sortis de la fenêtre envoyée au LLM, et le dernier message qu'il
    #: couvre. Le résumé est un JUGEMENT (LLM) ; où couper est une MESURE (code).
    summary: str = ""
    summary_upto: int = 0  # nombre de messages couverts par `summary`
    turns: int = 0
    #: Compteur des ids de propositions. Un id est attribué à chaque brouillon PAR POSITION
    #: dans la réponse, qu'il soit retenu ou non : le LLM peut ainsi prévoir l'id d'une
    #: expérience qu'il propose et y rattacher une réalisation dans la même réponse.
    proposal_seq: int = 0
    #: Provenance : id d'élément de la base -> ids des messages utilisateur qui le fondent.
    provenance: dict[str, list[str]] = {}
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    # --- identifiants séquentiels ---------------------------------------------

    def next_message_id(self) -> str:
        return f"m{len(self.messages) + 1}"

    # --- accès ----------------------------------------------------------------

    def proposal(self, proposal_id: str) -> Proposal | None:
        return next((p for p in self.proposals if p.id == proposal_id), None)

    def pending(self) -> list[Proposal]:
        return [p for p in self.proposals if p.status == "pending"]

    def user_message_ids(self) -> set[str]:
        return {m.id for m in self.messages if m.role == "user"}

    def user_texts(self) -> list[str]:
        return [m.text for m in self.messages if m.role == "user"]

    def last_user_message_id(self) -> str | None:
        return next((m.id for m in reversed(self.messages) if m.role == "user"), None)


__all__ = [
    "SECTION_LABELS",
    "SECTION_ORDER",
    "InterviewSession",
    "Message",
    "Proposal",
    "ProposalKind",
    "ProposalStatus",
    "Section",
    "SectionStatus",
    "now_iso",
]
