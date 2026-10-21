"""Service de l'entretien guidé : orchestre session, base d'expériences et modèle (printless).

Il reçoit tout déjà cloisonné sur le compte : le dépôt de la base d'expériences, celui de la
session, l'enregistreur de consommation et le journal. Il ne connaît ni compte, ni mode
(SQLite local ou Postgres partagé).

Écritures : un tour de parole écrit la session UNE fois ; une acceptation (même groupée,
depuis le récapitulatif) écrit la base UNE fois puis la session UNE fois. Le LLM n'écrit
jamais dans la base : seule `accept` le fait, sur décision de l'utilisateur.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.experience_base.store import ExperienceBaseMissing, ExperienceBaseRepo
from jobtool.core.llm.client import MODEL
from jobtool.interview import engine
from jobtool.interview.engine import InterviewError
from jobtool.interview.gaps import find_gaps
from jobtool.interview.models import (
    SECTION_LABELS,
    SECTION_ORDER,
    InterviewSession,
    Message,
    Proposal,
    Section,
)
from jobtool.interview.store import InterviewSessionStore
from jobtool.services.llm_journal_service import GenerationJournal, noop_journal
from jobtool.services.usage_service import UsageRecorder, noop_recorder

#: Type d'appel dans `llm_usage` et `llm_runs` (même vocabulaire que « cv », « letter »).
USAGE_KIND = "interview"


# --- Vue renvoyée au front ------------------------------------------------------


class SectionView(BaseModel):
    id: Section
    label: str
    status: str


class GapView(BaseModel):
    section: Section
    kind: str
    message: str
    target_id: str | None = None


class InterviewView(BaseModel):
    """Tout ce dont la page d'entretien a besoin, en une réponse."""

    messages: list[Message]
    proposals: list[Proposal]
    sections: list[SectionView]
    current: Section
    progress_done: int
    progress_total: int
    finished: bool
    turns: int
    max_turns: int
    context_notes: list[str]
    provenance: dict[str, list[str]]
    gaps: list[GapView]
    #: La base d'expériences telle qu'elle est (None : compte sans profil). Le front
    #: l'affiche dans la colonne « profil en direct ».
    profile: dict[str, Any] | None


class InterviewService:
    """Entretien d'UN compte."""

    def __init__(
        self,
        repo: ExperienceBaseRepo,
        sessions: InterviewSessionStore,
        client_factory: Callable[[], object],
        record_usage: UsageRecorder = noop_recorder,
        journal: GenerationJournal = noop_journal,
    ) -> None:
        """`client_factory` n'est appelée qu'au moment d'un tour : consulter la session,
        accepter ou refuser une proposition fonctionnent sans clé d'API."""
        self._repo = repo
        self._sessions = sessions
        self._client_factory = client_factory
        self._record_usage = record_usage
        self._journal = journal

    # --- Lecture ------------------------------------------------------------

    def view(self) -> InterviewView:
        """La session en cours (créée si besoin, avec son message d'ouverture)."""
        base = self._base()
        session = self._sessions.load()
        if session is None:
            session = engine.start_session(base)
            self._sessions.save(session)
        return self._view(session, base)

    # --- Conversation -------------------------------------------------------

    def send(self, text: str) -> InterviewView:
        """Un tour de parole. Lève `InterviewError` ; la session est alors inchangée."""
        base = self._base()
        session = self._load_or_start(base)
        client = self._client_factory()
        with self._journal.record(USAGE_KIND):
            session = engine.run_turn(session, base, text, client)
        self._record_usage(USAGE_KIND, MODEL)
        self._sessions.save(session)
        return self._view(session, base)

    def restart(self) -> InterviewView:
        """Nouvel entretien. Le PROFIL est conservé ; seule la conversation repart de zéro."""
        base = self._base()
        session = engine.start_session(base)
        self._sessions.save(session)
        return self._view(session, base)

    # --- Plan ---------------------------------------------------------------

    def goto(self, section: Section) -> InterviewView:
        base = self._base()
        session = self._load_or_start(base)
        engine.goto_section(session, section)
        self._sessions.save(session)
        return self._view(session, base)

    def skip(self, section: Section) -> InterviewView:
        base = self._base()
        session = self._load_or_start(base)
        engine.skip_section(session, section)
        self._sessions.save(session)
        return self._view(session, base)

    # --- Décisions ----------------------------------------------------------

    def accept(self, decisions: list[tuple[str, dict | None]]) -> InterviewView:
        """Accepte une ou plusieurs propositions (éventuellement modifiées). Tout ou rien."""
        if not decisions:
            raise InterviewError("aucune proposition à accepter")
        base = self._base()
        session = self._load_or_start(base)
        session, new_base = engine.accept(session, base, decisions)
        # La base d'abord : si son écriture échoue, la session garde les propositions en
        # attente et l'utilisateur peut réessayer. L'inverse marquerait « acceptées » des
        # propositions absentes du profil.
        self._repo.save(new_base)
        self._sessions.save(session)
        return self._view(session, new_base)

    def reject(self, proposal_ids: list[str]) -> InterviewView:
        base = self._base()
        session = engine.reject(self._load_or_start(base), proposal_ids)
        self._sessions.save(session)
        return self._view(session, base)

    # --- Interne ------------------------------------------------------------

    def _base(self) -> ExperienceBase | None:
        try:
            return self._repo.load()
        except ExperienceBaseMissing:
            return None

    def _load_or_start(self, base: ExperienceBase | None) -> InterviewSession:
        session = self._sessions.load()
        return session if session is not None else engine.start_session(base)

    @staticmethod
    def _view(session: InterviewSession, base: ExperienceBase | None) -> InterviewView:
        done, total = engine.progress(session)
        return InterviewView(
            messages=session.messages,
            proposals=session.proposals,
            sections=[
                SectionView(id=s, label=SECTION_LABELS[s], status=session.sections[s])
                for s in SECTION_ORDER
            ],
            current=session.current,
            progress_done=done,
            progress_total=total,
            finished=engine.is_finished(session),
            turns=session.turns,
            max_turns=engine.MAX_TURNS,
            context_notes=session.context_notes,
            provenance=session.provenance,
            gaps=[
                GapView(
                    section=g.section,
                    kind=g.kind,
                    message=g.message,
                    target_id=g.target_id,
                )
                for g in find_gaps(base)
            ],
            profile=None if base is None else base.model_dump(mode="json"),
        )


__all__ = ["USAGE_KIND", "InterviewError", "InterviewService", "InterviewView"]
