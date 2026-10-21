"""Déroulé de l'entretien : démarrage, tour de parole, plan, décisions sur les propositions.

Fonctions sur une `InterviewSession` et la base d'expériences, sans stockage ni compte :
l'orchestration (lire, écrire, compter la consommation) appartient à
`services/interview_service.py`.

Un tour ne modifie la session qu'en cas de SUCCÈS. Si le modèle échoue (réseau, réponse
inexploitable après nouvel essai), `run_turn` lève `InterviewLLMError` et la session reste
telle qu'avant le message : rien n'est perdu, l'utilisateur peut renvoyer son message.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.llm.client import call_structured
from jobtool.core.llm.correction_loop import run_correction_loop
from jobtool.interview.apply import ApplyError, apply_proposals
from jobtool.interview.checks import check_turn, recheck_edit
from jobtool.interview.gaps import find_gaps, is_empty, sections_with_gaps
from jobtool.interview.models import (
    SECTION_ORDER,
    InterviewSession,
    Message,
    Proposal,
    Section,
    now_iso,
)
from jobtool.interview.prompt import (
    build_messages,
    correction_message,
    opening_message,
    summary_cut,
    summary_messages,
)
from jobtool.interview.schema import ContextSummary, InterviewTurn

logger = logging.getLogger(__name__)

#: Plafond de tours par session (maîtrise du coût). Au-delà, on propose de recommencer.
MAX_TURNS = 80
#: Longueur maximale d'un message de l'utilisateur (caractères).
MAX_USER_CHARS = 4000
#: Nouvelles tentatives quand le code trouve des problèmes dans la réponse du modèle.
MAX_CORRECTIONS = 1
#: Nouvelles tentatives quand l'appel lui-même échoue (réponse non structurée, réseau).
MAX_CALL_RETRIES = 1
#: Nombre maximal de notes de contexte conservées.
MAX_CONTEXT_NOTES = 20


class InterviewError(Exception):
    """Erreur attendue, au message lisible par l'utilisateur."""


class InterviewLLMError(InterviewError):
    """Le modèle n'a pas pu produire de réponse exploitable. La session est intacte."""


class TurnLimitReached(InterviewError):
    """La session a atteint `MAX_TURNS`."""


# --- Démarrage ----------------------------------------------------------------


def start_session(base: ExperienceBase | None) -> InterviewSession:
    """Nouvelle session : plan initialisé selon les manques, message d'ouverture posé.

    Profil vide : on suit le plan depuis « situation actuelle ». Profil existant : on
    commence par la première section qui présente un manque ; les sections sans manque
    restent au programme (on peut toujours y revenir), sans être imposées.
    """
    session = InterviewSession()
    empty = is_empty(base)
    gaps = find_gaps(base)
    concerned = sections_with_gaps(gaps)
    first = SECTION_ORDER[0] if empty or not concerned else concerned[0]
    for section in SECTION_ORDER:
        if not empty and concerned and section not in concerned:
            session.sections[section] = "done"
    _set_current(session, first)
    session.messages.append(
        Message(
            id=session.next_message_id(),
            role="assistant",
            text=opening_message(base, gaps, empty),
            section=first,
        )
    )
    return session


# --- Plan ---------------------------------------------------------------------


def _set_current(session: InterviewSession, section: Section) -> None:
    for s, status in session.sections.items():
        if status == "current":
            session.sections[s] = "todo"
    session.sections[section] = "current"
    session.current = section


def advance(session: InterviewSession) -> None:
    """Marque la section courante comme faite et passe à la suivante non traitée."""
    session.sections[session.current] = "done"
    _move_to_next(session)


def skip_section(session: InterviewSession, section: Section) -> None:
    """Saute une section (elle reste accessible). Si c'est la courante, on avance."""
    session.sections[section] = "skipped"
    if section == session.current:
        _move_to_next(session)


def goto_section(session: InterviewSession, section: Section) -> None:
    """Revient (ou saute) à une section, quel que soit son statut."""
    if session.sections[session.current] == "current":
        session.sections[session.current] = "todo"
    _set_current(session, section)


def _move_to_next(session: InterviewSession) -> None:
    order = list(SECTION_ORDER)
    start = order.index(session.current)
    for section in order[start + 1 :] + order[:start]:
        if session.sections[section] == "todo":
            _set_current(session, section)
            return
    # Plus rien à traiter : le curseur reste sur la section qu'on vient de finir.


def progress(session: InterviewSession) -> tuple[int, int]:
    """(sections traitées, total). Une MESURE : calculée par le code, jamais par le LLM."""
    handled = sum(
        1 for s in SECTION_ORDER if session.sections[s] in ("done", "skipped")
    )
    return handled, len(SECTION_ORDER)


def is_finished(session: InterviewSession) -> bool:
    handled, total = progress(session)
    return handled == total


# --- Tour de parole -----------------------------------------------------------


def run_turn(
    session: InterviewSession,
    base: ExperienceBase | None,
    user_text: str,
    client: object,
) -> InterviewSession:
    """Joue un tour : message de l'utilisateur -> réponse contrôlée de l'intervieweur.

    Renvoie une NOUVELLE session ; celle reçue n'est pas modifiée (en cas d'échec, l'appelant
    garde l'état d'avant). Lève `InterviewError` (message vide, plafond) ou
    `InterviewLLMError` (modèle).
    """
    text = user_text.strip()
    if not text:
        raise InterviewError("message vide")
    if len(text) > MAX_USER_CHARS:
        raise InterviewError(
            f"message trop long ({len(text)} caractères, maximum {MAX_USER_CHARS})"
        )
    if session.turns >= MAX_TURNS:
        raise TurnLimitReached(
            f"cette session a atteint {MAX_TURNS} échanges : recommence un nouvel "
            "entretien (ton profil et les éléments acceptés sont conservés)"
        )

    work = session.model_copy(deep=True)
    work.messages.append(
        Message(id=work.next_message_id(), role="user", text=text, section=work.current)
    )
    _maybe_summarize(work, client)

    gaps = find_gaps(base)
    next_index = work.proposal_seq + 1

    def check(turn: InterviewTurn) -> list[str]:
        return check_turn(turn, work, base, next_index=next_index).issues

    turn = _call(
        lambda: run_correction_loop(
            client,
            build_messages(work, base, gaps),
            InterviewTurn,
            check=check,
            build_correction=correction_message,
            max_retries=MAX_CORRECTIONS,
            step="entretien",
        )[0]
    )
    report = check_turn(turn, work, base, next_index=next_index)
    if report.issues:
        logger.info("constats restants après correction : %s", report.issues)

    assistant_id = work.next_message_id()
    work.messages.append(
        Message(
            id=assistant_id,
            role="assistant",
            text=turn.message.strip(),
            section=work.current,
        )
    )
    for proposal in report.proposals:
        proposal.message_id = assistant_id
        work.proposals.append(proposal)
    work.proposal_seq += len(turn.proposals)
    for note in turn.context_notes:
        note = " ".join(note.split())
        if note and note not in work.context_notes:
            work.context_notes.append(note)
    work.context_notes = work.context_notes[-MAX_CONTEXT_NOTES:]
    if turn.section_complete:
        advance(work)
    work.turns += 1
    return work


def _call(fn: Callable[[], InterviewTurn]) -> InterviewTurn:
    """Appel au modèle avec un nouvel essai sur échec ; erreur lisible au-delà."""
    last: Exception | None = None
    for _ in range(1 + MAX_CALL_RETRIES):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — traduit en erreur lisible ci-dessous
            logger.warning("appel au modèle échoué (entretien)", exc_info=True)
            last = exc
    raise InterviewLLMError(
        "l'assistant n'a pas pu répondre (réponse invalide ou service indisponible). "
        "Ton message n'a pas été perdu : renvoie-le dans un instant."
    ) from last


def _maybe_summarize(session: InterviewSession, client: object) -> None:
    """Condense les plus anciens messages si l'historique dépasse la fenêtre.

    Un résumé raté n'empêche pas le tour : on envoie simplement un historique plus long.
    """
    cut = summary_cut(session)
    if cut is None:
        return
    try:
        result = call_structured(
            client,  # type: ignore[arg-type]
            summary_messages(session, cut),
            ContextSummary,
            log_slug="entretien_resume",
        )
    except Exception:  # noqa: BLE001 — le résumé est une optimisation, pas un prérequis
        logger.warning("résumé de l'entretien impossible", exc_info=True)
        return
    session.summary = result.summary.strip()
    session.summary_upto = cut


# --- Décisions sur les propositions -------------------------------------------


def accept(
    session: InterviewSession,
    base: ExperienceBase | None,
    decisions: list[tuple[str, dict | None]],
) -> tuple[InterviewSession, ExperienceBase]:
    """Accepte des propositions (chacune éventuellement MODIFIÉE) et renvoie la nouvelle base.

    `decisions` : (id de proposition, payload modifié ou None). Tout ou rien : si l'une est
    inapplicable, aucune n'est acceptée (`InterviewError`). Renvoie une nouvelle session et
    la nouvelle base, que l'appelant écrit (une écriture de chaque).
    """
    work = session.model_copy(deep=True)
    chosen: list[Proposal] = []
    for proposal_id, edited in decisions:
        proposal = work.proposal(proposal_id)
        if proposal is None:
            raise InterviewError(f"proposition inconnue : {proposal_id}")
        if proposal.status != "pending":
            raise InterviewError(f"proposition déjà traitée : {proposal_id}")
        if edited is not None:
            clean, errors = recheck_edit(proposal, edited, work, base)
            if clean is None:
                raise InterviewError(f"{proposal_id} : " + " ; ".join(errors))
            if clean != proposal.payload:
                proposal.payload = clean
                proposal.edited = True
                proposal.warnings = []
                proposal.blocking = False
        if proposal.blocking:
            raise InterviewError(
                f"{proposal_id} contient une information que tu n'as pas donnée "
                f"({'; '.join(proposal.warnings)}) : modifie-la avant de l'accepter"
            )
        chosen.append(proposal)

    resolved = {
        p.id: p.result_id
        for p in work.proposals
        if p.status == "accepted" and p.result_id
    }
    try:
        new_base, created = apply_proposals(base, chosen, resolved)
    except ApplyError as exc:
        raise InterviewError(str(exc)) from exc
    except ValueError as exc:  # revalidation de la base (pydantic)
        raise InterviewError(f"profil invalide après ajout : {exc}") from exc

    now = now_iso()
    for proposal in chosen:
        proposal.status = "accepted"
        proposal.decided_at = now
        proposal.result_id = created[proposal.id]
        sources = work.provenance.setdefault(proposal.result_id, [])
        for source in proposal.sources:
            if source not in sources:
                sources.append(source)
    return work, new_base


def reject(session: InterviewSession, proposal_ids: list[str]) -> InterviewSession:
    """Refuse des propositions. Elles restent dans l'historique (le LLM ne les repropose pas)."""
    work = session.model_copy(deep=True)
    now = now_iso()
    for proposal_id in proposal_ids:
        proposal = work.proposal(proposal_id)
        if proposal is None:
            raise InterviewError(f"proposition inconnue : {proposal_id}")
        if proposal.status == "pending":
            proposal.status = "rejected"
            proposal.decided_at = now
    return work


__all__ = [
    "MAX_TURNS",
    "InterviewError",
    "InterviewLLMError",
    "TurnLimitReached",
    "accept",
    "advance",
    "goto_section",
    "is_finished",
    "progress",
    "reject",
    "run_turn",
    "skip_section",
    "start_session",
]
