"""Routes /admin : tableau de bord des comptes.

Réservé aux comptes portant `is_admin`. La garde est une DÉPENDANCE, pas un test dispersé
dans chaque route : un oubli fait échouer l'ajout de la route, pas la sécurité.

La vue d'ensemble n'expose aucun contenu client (ni CV, ni description d'offre) :
uniquement des compteurs.

Le JOURNAL des échanges avec le modèle fait exception, à la demande : il rend lisibles les
prompts et les réponses des dernières générations, de TOUS les comptes. C'est un outil de
vérification de ce que le modèle met en avant, réservé à l'administration et borné par la
purge (10 générations par type). Voir services/llm_journal_service.py.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query

from jobtool.api.deps import get_admin_service, get_current_user_id
from jobtool.api.schemas import (
    AdminAccountOut,
    AdminOverviewOut,
    JournalExchangeOut,
    JournalRunDetailOut,
    JournalRunOut,
)
from jobtool.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(
    user_id: uuid.UUID = Depends(get_current_user_id),
    service: AdminService = Depends(get_admin_service),
) -> AdminService:
    """Laisse passer les seuls comptes administrateurs. 403 sinon.

    403 et non 404 : la route existe, c'est l'autorisation qui manque — et l'administration
    n'est pas un secret à dissimuler, c'est un accès à refuser.

    L'identité arrive par `Depends`, jamais par un appel direct : c'est ce qui permet de la
    remplacer (test, puis authentification réelle) sans toucher à cette route.
    """
    if not service.is_admin(user_id):
        raise HTTPException(status_code=403, detail="accès réservé à l'administration")
    return service


@router.get("/overview", response_model=AdminOverviewOut)
def overview(service: AdminService = Depends(require_admin)) -> AdminOverviewOut:
    """Comptes, volumes par compte, et totaux de l'instance."""
    data = service.overview()
    # Les champs portent les mêmes noms de part et d'autre : les recopier un à un
    # n'ajoutait rien, sinon le risque qu'un compteur ajouté au service reste absent de la
    # réponse sans que rien ne le signale.
    return AdminOverviewOut(
        accounts=[AdminAccountOut(**asdict(a)) for a in data.accounts],
        catalogue_offers=data.catalogue_offers,
        total_documents=data.total_documents,
        total_llm_calls=data.total_llm_calls,
        database=data.database,
    )


# --- Journal des échanges avec le modèle ------------------------------------


@router.get("/llm-runs", response_model=list[JournalRunOut])
def llm_runs(
    limit: int = Query(20, ge=1, le=100),
    service: AdminService = Depends(require_admin),
) -> list[JournalRunOut]:
    """Dernières générations, plus récentes d'abord. La liste ne porte AUCUN prompt.

    Le détail (les messages) demande une seconde requête, sur une seule génération : une
    liste qui transporterait les prompts pèserait des centaines de kilo-octets pour rien.
    """
    return [JournalRunOut(**asdict(row)) for row in service.llm_runs(limit)]


@router.get("/llm-runs/{run_id}", response_model=JournalRunDetailOut)
def llm_run(
    run_id: str,
    service: AdminService = Depends(require_admin),
) -> JournalRunDetailOut:
    """Une génération et tous ses appels au modèle, dans l'ordre. 404 si inconnue ou purgée."""
    detail = service.llm_run(run_id)
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"génération introuvable : {run_id}"
        )
    return JournalRunDetailOut(
        run=JournalRunOut(**asdict(detail.run)),
        exchanges=[JournalExchangeOut(**asdict(x)) for x in detail.exchanges],
    )
