"""Routes /aggregate : lancer une COLLECTE en arrière-plan, et en suivre l'avancement.

Une collecte alimente le catalogue COMMUN (toutes les sources activées dans le
config.yaml) ; elle ne dépend pas de qui la lance et ne change l'état de personne. La
RECHERCHE, elle, est un simple filtre du catalogue (GET /offers) et répond tout de suite.

  POST /aggregate           -> 202 + la collecte inscrite ; elle tourne en arrière-plan.
                               409 si une collecte tourne déjà (son identifiant est joint,
                               pour la suivre) ; 429 + Retry-After si la précédente date de
                               moins d'un quart d'heure.
  GET  /aggregate/latest    -> la dernière collecte (en cours ou terminée), ou null.
  GET  /aggregate/{run_id}  -> une collecte : avancement par source, bilan, erreurs.

Contrat habituel : recevoir -> appeler le service -> renvoyer du JSON. L'identité et la
base arrivent par `Depends` (project_ctx.md) : la collecte écrit dans la base que lit GET /offers.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import Engine

from jobtool.api.deps import get_account_settings, get_current_user_id, get_offers_db
from jobtool.api.schemas import CollectionStatus
from jobtool.settings import AggregatorSettings

router = APIRouter(prefix="/aggregate", tags=["aggregate"])


@router.post("", response_model=CollectionStatus, status_code=202)
def trigger_collection(
    background: BackgroundTasks,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
    settings: AggregatorSettings = Depends(get_account_settings),
) -> CollectionStatus:
    """Lance une collecte de TOUTES les sources activées et rend la main aussitôt.

    Les sources à identifiants (France Travail, Adzuna) utilisent les clés du compte qui
    lance la collecte, sinon celles du serveur.
    """
    # Imports différés : tirent les connecteurs (requests) seulement à l'appel.
    from jobtool.aggregator.config import ConfigError
    from jobtool.services.aggregation_service import (
        CollectionBusy,
        CollectionTooSoon,
        execute_collection,
        get_collection,
        start_collection,
    )

    try:
        run_id = start_collection(db, trigger="api", triggered_by=user_id)
    except CollectionBusy as exc:
        raise HTTPException(
            status_code=409, detail={"message": str(exc), "run_id": exc.run_id}
        ) from exc
    except CollectionTooSoon as exc:
        raise HTTPException(
            status_code=429,
            detail={"message": str(exc), "run_id": exc.run_id},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except ConfigError as exc:  # config.yaml illisible : erreur SERVEUR, mais propre
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    background.add_task(execute_collection, run_id, db, settings=settings)
    view = get_collection(db, run_id)
    assert view is not None  # vient d'être inscrite
    return CollectionStatus.from_view(view)


@router.get("/latest", response_model=CollectionStatus | None)
def latest(db: Engine | str = Depends(get_offers_db)) -> CollectionStatus | None:
    """La dernière collecte lancée, ou null s'il n'y en a jamais eu.

    Déclarée AVANT `/{run_id}` : sinon « latest » serait lu comme un identifiant.
    """
    from jobtool.services.aggregation_service import latest_collection

    view = latest_collection(db)
    return CollectionStatus.from_view(view) if view is not None else None


@router.get("/{run_id}", response_model=CollectionStatus)
def status(run_id: str, db: Engine | str = Depends(get_offers_db)) -> CollectionStatus:
    """Avancement d'une collecte (l'interface l'interroge pendant qu'elle tourne)."""
    from jobtool.services.aggregation_service import get_collection

    view = get_collection(db, run_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"collecte introuvable : {run_id}")
    return CollectionStatus.from_view(view)
