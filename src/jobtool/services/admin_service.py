"""Service ADMIN : vue d'ensemble des comptes et de ce qu'ils consomment.

Couche service (printless, sans SQL d'interface) : l'API appelle, obtient des DTO, et se
contente de les sérialiser.

Ce qu'on mesure ici est ce qu'on ne peut PAS reconstituer après coup — nombre de comptes,
volume par compte, appels au modèle. C'est la base d'un quota ou d'une facturation.

La VUE D'ENSEMBLE n'expose aucune donnée de contenu : ni texte de CV, ni description
d'offre — elle sert à compter, pas à lire par-dessus l'épaule des clients.

Le JOURNAL des échanges avec le modèle, lui, est une exception ASSUMÉE et demandée : il
rend lisibles les prompts et les réponses des dernières générations, de tous les comptes,
pour vérifier ce que le modèle met en avant. Il contient donc du contenu client. Ce qui le
borne : la garde `require_admin` (403 sinon) et la purge de
`services/llm_journal_service.py`, qui n'en conserve que les dernières générations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.sql import ColumnElement

from jobtool.core.db import (
    documents,
    llm_exchanges,
    llm_runs,
    llm_usage,
    offers,
    user_offers,
    users,
)


@dataclass(frozen=True)
class AccountRow:
    """Un compte et ses volumes. Des compteurs, jamais du contenu."""

    id: str
    email: str
    display_name: str
    is_admin: bool
    created_at: str
    offers: int = 0
    favorites: int = 0
    applied: int = 0
    documents: int = 0
    llm_calls: int = 0


@dataclass(frozen=True)
class AdminOverview:
    """Ce que montre l'écran d'administration."""

    accounts: list[AccountRow] = field(default_factory=list)
    catalogue_offers: int = 0  # lignes du catalogue mutualisé (toutes annonces connues)
    total_documents: int = 0
    total_llm_calls: int = 0
    database: str = ""  # « Postgres (partagée) » ou « SQLite (poste local) »


@dataclass(frozen=True)
class JournalRunRow:
    """Une génération, telle que la LISTE du journal l'affiche. Aucun prompt ici."""

    run_id: str
    kind: str
    template: str
    model: str
    status: str
    error: str | None
    calls: int
    started_at: str
    ended_at: str | None
    account: str  # e-mail du compte
    offer_id: str | None
    offer_title: str  # titre au catalogue, "" si l'offre n'y figure pas


@dataclass(frozen=True)
class JournalExchangeRow:
    """UN appel au modèle : ce qui est parti, ce qui est revenu, intégralement."""

    seq: int
    step: str
    attempt: int
    retries_429: int
    messages: list[Any]
    response: Any | None
    created_at: str


@dataclass(frozen=True)
class JournalRunDetail:
    """Une génération et ses appels, dans l'ordre."""

    run: JournalRunRow
    exchanges: list[JournalExchangeRow] = field(default_factory=list)


class AdminService:
    """Lectures d'administration. N'écrit rien : c'est un tableau de bord, pas une console."""

    def __init__(self, engine: Engine, *, single_user: bool = False) -> None:
        # Mono-poste : le seul utilisateur est le propriétaire de la machine, donc
        # administrateur d'office (même règle que les profils multiples, voir
        # core/experience_base/profiles.resolve_active_profile). Défaut FERMÉ : une base
        # partagée ne l'active jamais par oubli.
        self._engine = engine
        self._single_user = single_user

    @staticmethod
    def _grouped_count(
        conn: Connection, column: ColumnElement[uuid.UUID], *filters: Any
    ) -> dict[uuid.UUID, int]:
        """{compte -> nombre de lignes}, agrégé EN SQL (un aller-retour, pas un par compte)."""
        stmt = select(column, func.count()).group_by(column)
        for condition in filters:
            stmt = stmt.where(condition)
        return {row[0]: row[1] for row in conn.execute(stmt)}

    def overview(self) -> AdminOverview:
        """Un compte par ligne, avec ses volumes, plus les totaux de l'instance.

        Les compteurs sont agrégés en SQL par des sous-requêtes groupées, et non en
        parcourant les comptes un par un : le coût ne dépend pas du nombre de clients.
        """
        with self._engine.connect() as conn:
            offers_by_user = self._grouped_count(conn, user_offers.c.user_id)
            favorites_by_user = self._grouped_count(
                conn, user_offers.c.user_id, user_offers.c.favorite
            )
            applied_by_user = self._grouped_count(
                conn, user_offers.c.user_id, user_offers.c.status == "applied"
            )
            documents_by_user = self._grouped_count(conn, documents.c.user_id)
            calls_by_user = self._grouped_count(conn, llm_usage.c.user_id)

            accounts = [
                AccountRow(
                    id=str(row.id),
                    email=row.email,
                    display_name=row.display_name,
                    is_admin=bool(row.is_admin),
                    created_at=row.created_at,
                    offers=offers_by_user.get(row.id, 0),
                    favorites=favorites_by_user.get(row.id, 0),
                    applied=applied_by_user.get(row.id, 0),
                    documents=documents_by_user.get(row.id, 0),
                    llm_calls=calls_by_user.get(row.id, 0),
                )
                for row in conn.execute(
                    select(users).order_by(users.c.is_admin.desc(), users.c.created_at)
                )
            ]

            # Le CATALOGUE, pas la somme des vues : on compte les lignes de `offers`.
            # Compter les dedup_hash distincts de `user_offers` répondait à une autre
            # question — les annonces réclamées par au moins un compte. Les deux
            # coïncident tant que rien n'est purgé, et divergent dès la première purge :
            # `purge_hidden_offers` retire la ligne du compte et laisse l'annonce au
            # catalogue, où une prochaine recherche la retrouvera.
            catalogue = int(
                conn.execute(select(func.count()).select_from(offers)).scalar() or 0
            )

        return AdminOverview(
            accounts=accounts,
            catalogue_offers=catalogue,
            total_documents=sum(a.documents for a in accounts),
            total_llm_calls=sum(a.llm_calls for a in accounts),
            database=(
                "Postgres (partagée)"
                if self._engine.dialect.name != "sqlite"
                else "SQLite (poste local)"
            ),
        )

    # --- Journal des échanges avec le modèle --------------------------------

    @staticmethod
    def _run_row(row: Any) -> JournalRunRow:
        return JournalRunRow(
            run_id=row.run_id,
            kind=row.kind,
            template=row.template,
            model=row.model,
            status=row.status,
            error=row.error,
            calls=row.calls,
            started_at=row.started_at,
            ended_at=row.ended_at,
            account=row.email or "",
            offer_id=row.offer_id,
            offer_title=row.title or "",
        )

    @staticmethod
    def _runs_query():
        """Générations + e-mail du compte + titre de l'offre, en UNE requête.

        Jointures EXTERNES sur le catalogue : une offre collée à la main n'y figure pas, et
        sa génération doit rester visible (la liste affichera son identifiant).
        """
        return select(
            llm_runs,
            users.c.email.label("email"),
            offers.c.title.label("title"),
        ).select_from(
            llm_runs.join(users, users.c.id == llm_runs.c.user_id).outerjoin(
                offers, offers.c.dedup_hash == llm_runs.c.offer_id
            )
        )

    def llm_runs(self, limit: int = 20) -> list[JournalRunRow]:
        """Dernières générations, plus récentes d'abord. Sans aucun prompt."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self._runs_query().order_by(llm_runs.c.started_at.desc()).limit(limit)
            )
            return [self._run_row(row) for row in rows]

    def llm_run(self, run_id: str) -> JournalRunDetail | None:
        """Une génération et TOUS ses échanges, ou None si elle est inconnue (ou purgée)."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self._runs_query().where(llm_runs.c.run_id == run_id)
            ).first()
            if row is None:
                return None
            exchanges = [
                JournalExchangeRow(
                    seq=item.seq,
                    step=item.step,
                    attempt=item.attempt,
                    retries_429=item.retries_429,
                    messages=list(item.messages or []),
                    response=item.response,
                    created_at=item.created_at,
                )
                for item in conn.execute(
                    select(llm_exchanges)
                    .where(llm_exchanges.c.run_id == run_id)
                    .order_by(llm_exchanges.c.seq)
                )
            ]
        return JournalRunDetail(run=self._run_row(row), exchanges=exchanges)

    def is_admin(self, user_id: uuid.UUID) -> bool:
        """Vrai si le compte porte le drapeau administrateur (toujours en mono-poste)."""
        if self._single_user:
            return True
        with self._engine.connect() as conn:
            row = conn.execute(
                select(users.c.is_admin).where(users.c.id == user_id)
            ).first()
        return bool(row.is_admin) if row is not None else False
