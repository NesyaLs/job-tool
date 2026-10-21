"""Service d'entretien du CATALOGUE d'offres (printless) — outils de développement.

Dédoublonnage de l'existant, doublons probables pour calibrer le seuil, volumes,
recalcul des champs normalisés. La CLI (`jobtool catalogue …`) en fait la présentation.

La base est celle de l'application (même bascule que l'API : Postgres si `DATABASE_URL`
est définie, sinon le fichier SQLite local), sauf chemin SQLite explicite.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.engine import Connection

from jobtool.aggregator import catalogue, storage
from jobtool.aggregator.dedup import FUZZY_THRESHOLD, PROBABLE_FLOOR
from jobtool.core.db import database_for
from jobtool.settings import AggregatorSettings


def resolve_db(db: str | Path | Engine | None = None) -> str | Path | Engine:
    """Base visée : celle donnée, sinon celle de l'application."""
    if db is not None:
        return db
    settings = AggregatorSettings()
    return database_for(settings.database_url, settings.db_path)


@contextmanager
def _conn(db: str | Path | Engine | None) -> Iterator[Connection]:
    conn = storage.open_connection(resolve_db(db))
    try:
        yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class ProbablePair:
    """Deux offres que le niveau 3 compare, sous le seuil de fusion."""

    score: float
    a: dict[str, Any]
    b: dict[str, Any]


def probable_duplicates(
    db: str | Path | Engine | None = None,
    *,
    low: float = PROBABLE_FLOOR,
    high: float = FUZZY_THRESHOLD,
) -> list[ProbablePair]:
    """Paires comparables (même entreprise, même lieu, contrats et dates compatibles) dont
    la similarité d'intitulé est dans [low ; high[ — à examiner pour régler le seuil."""
    with _conn(db) as conn:
        catalogue.renormalize_catalogue(conn)
        return [
            ProbablePair(score=pair.score or 0.0, a=a, b=b)
            for pair, a, b in catalogue.probable_duplicates(conn, low=low, high=high)
        ]


def deduplicate(
    db: str | Path | Engine | None = None, *, apply: bool = False
) -> list[catalogue.Merge]:
    """Fusions que les règles décident sur le catalogue EXISTANT ; appliquées si `apply`."""
    with _conn(db) as conn:
        catalogue.renormalize_catalogue(conn)
        merges = catalogue.deduplicate_catalogue(conn, apply=apply)
        if apply:
            conn.commit()
        return merges


def stats(db: str | Path | Engine | None = None) -> dict[str, Any]:
    """Volumes : occurrences et offres, par source et par niveau de rattachement."""
    with _conn(db) as conn:
        return catalogue.catalogue_stats(conn)


def renormalize(db: str | Path | Engine | None = None) -> int:
    """Recalcule les champs normalisés produits par une règle antérieure."""
    with _conn(db) as conn:
        return catalogue.renormalize_catalogue(conn)
