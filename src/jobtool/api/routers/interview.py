"""Routes /interview : entretien guidé par LLM pour construire le profil.

Recevoir -> appeler le service -> renvoyer du JSON, comme le reste de l'API. Chaque route
renvoie la VUE complète de l'entretien (messages, propositions, plan, profil en direct) :
le front n'a jamais à recoller des morceaux.

Erreurs : une demande impossible (proposition inconnue ou bloquée, message vide) -> 400 ;
plafond de tours atteint -> 409 ; le modèle n'a pas pu répondre -> 502. Dans tous les cas la
session est intacte.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from jobtool.api.deps import get_interview_service
from jobtool.interview.engine import InterviewError, InterviewLLMError, TurnLimitReached
from jobtool.interview.models import Section
from jobtool.services.interview_service import InterviewService, InterviewView

router = APIRouter(prefix="/interview", tags=["interview"])


class MessageIn(BaseModel):
    text: str


class Decision(BaseModel):
    id: str
    #: Proposition MODIFIÉE par l'utilisateur (mêmes champs que `payload`), ou absente.
    payload: dict | None = None


class AcceptIn(BaseModel):
    decisions: list[Decision]


class RejectIn(BaseModel):
    ids: list[str]


def _guard(fn):
    try:
        return fn()
    except TurnLimitReached as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InterviewLLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except InterviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=InterviewView)
def get_interview(
    service: InterviewService = Depends(get_interview_service),
) -> InterviewView:
    """La session du compte ; créée (avec son message d'ouverture) si elle n'existe pas."""
    return _guard(service.view)


@router.post("/messages", response_model=InterviewView)
def post_message(
    body: MessageIn, service: InterviewService = Depends(get_interview_service)
) -> InterviewView:
    """Envoie un message ; renvoie la session avec la réponse et les nouvelles propositions."""
    return _guard(lambda: service.send(body.text))


@router.post("/restart", response_model=InterviewView)
def restart(
    service: InterviewService = Depends(get_interview_service),
) -> InterviewView:
    """Recommence la conversation. Le profil n'est pas touché."""
    return _guard(service.restart)


@router.post("/sections/{section}/goto", response_model=InterviewView)
def goto_section(
    section: Section, service: InterviewService = Depends(get_interview_service)
) -> InterviewView:
    return _guard(lambda: service.goto(section))


@router.post("/sections/{section}/skip", response_model=InterviewView)
def skip_section(
    section: Section, service: InterviewService = Depends(get_interview_service)
) -> InterviewView:
    return _guard(lambda: service.skip(section))


@router.post("/proposals/accept", response_model=InterviewView)
def accept_proposals(
    body: AcceptIn, service: InterviewService = Depends(get_interview_service)
) -> InterviewView:
    """Accepte une ou plusieurs propositions (récapitulatif) : tout ou rien, une écriture."""
    return _guard(lambda: service.accept([(d.id, d.payload) for d in body.decisions]))


@router.post("/proposals/reject", response_model=InterviewView)
def reject_proposals(
    body: RejectIn, service: InterviewService = Depends(get_interview_service)
) -> InterviewView:
    return _guard(lambda: service.reject(body.ids))
