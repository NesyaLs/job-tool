"""Couche SERVICE de la page « Découvrir » : la pile d'offres à swiper.

Orchestration seulement : lire les candidates (UNE requête), les faire noter par
`aggregator.matching` (calcul pur), renvoyer la tranche demandée. Aucune interface, aucun
print, aucun SQL écrit ici.

Candidates = offres du compte au statut `new` et NON favorites. C'est ce qui garantit
qu'une offre « aimée » (favori) ou « masquée » (statut `seen`) ne revient jamais dans la
pile : les deux gestes réutilisent l'état par compte existant (`user_offers`), et la page
« Offres » voit donc exactement le même état. Le filtre est fait par la base, pas ici.

Le score, lui, ne peut pas être calculé en SQL sans dupliquer la formule : on rapatrie les
candidates en une fois et on trie en mémoire. Le périmètre `new` d'un compte est borné par
une recherche (quelques centaines d'offres), et un seul aller-retour vaut mieux que
plusieurs sur une base distante.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine

from jobtool.aggregator import storage
from jobtool.aggregator.matching import (
    DEFAULT_MIN_PERCENT,
    MatchCriteria,
    MatchResult,
    rank_jobs,
)
from jobtool.aggregator.models import JobStatus


@dataclass(frozen=True)
class StackPage:
    """Les prochaines cartes, et le nombre total d'offres qui restent à proposer."""

    items: list[MatchResult]
    remaining: int


class DiscoveryService:
    """Pile d'offres triées par compatibilité, cloisonnée sur un compte."""

    def __init__(self, db: str | Path | Engine, user_id: uuid.UUID):
        """`user_id` SANS défaut : la pile ne se construit jamais pour un compte deviné."""
        self._db = db
        self._user_id = user_id

    def stack(
        self,
        criteria: MatchCriteria,
        *,
        limit: int,
        exclude: list[str] | None = None,
        max_age_days: int | None = None,
        min_percent: int = DEFAULT_MIN_PERCENT,
        now: datetime | None = None,
    ) -> StackPage:
        """Les `limit` offres les plus compatibles, hors `exclude` (cartes déjà chargées).

        `max_age_days` est un filtre DUR (même sens que sur la page « Offres » : publiée
        dans les N derniers jours) ; les autres critères sont souples et ne font que noter.
        """
        now = now or datetime.now(UTC)
        published_after = None
        if max_age_days is not None:
            published_after = (now - timedelta(days=max_age_days)).date().isoformat()
        query = storage.JobQuery(
            status=JobStatus.new, favorite=False, published_after=published_after
        )
        conn = storage.open_connection(self._db)
        try:
            candidates = storage.list_jobs(conn, self._user_id, query=query)
        finally:
            conn.close()

        ranked = rank_jobs(
            candidates,
            criteria,
            now=now,
            min_percent=min_percent,
            exclude=exclude or (),
        )
        return StackPage(items=ranked[: max(0, limit)], remaining=len(ranked))
