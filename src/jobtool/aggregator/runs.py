"""Suivi des COLLECTES : verrou, progression par source, historique (table collection_runs).

Une collecte alimente le catalogue COMMUN à tous les comptes : il n'en faut qu'une à la
fois, et l'interface ne doit pas pouvoir en relancer une toutes les minutes. La table sert
donc de verrou, et l'écran la relit pour afficher l'avancement d'une collecte qui tourne
en arrière-plan.

VERROU sans service extérieur : la collecte s'INSCRIT d'abord, puis vérifie qu'elle est
bien la première inscrite parmi celles en cours. Deux demandes simultanées s'inscrivent
toutes les deux ; la seconde (par date, puis identifiant) se retire. La base tranche, pas
l'horloge d'un processus.

Une collecte interrompue (processus tué) resterait « en cours » pour toujours : au-delà
de `STALE_AFTER`, elle est déclarée échouée et ne bloque plus rien.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from jobtool.core.db import collection_runs

#: Au-delà, une collecte « en cours » est considérée comme interrompue.
STALE_AFTER = timedelta(hours=2)


class CollectionBusy(RuntimeError):
    """Une collecte tourne déjà : `run_id` est la sienne (à suivre plutôt qu'à relancer)."""

    def __init__(self, run_id: str):
        super().__init__(f"une collecte est déjà en cours ({run_id})")
        self.run_id = run_id


class CollectionTooSoon(RuntimeError):
    """La précédente collecte est trop récente : réessayer dans `retry_after` secondes."""

    def __init__(self, run_id: str, retry_after: int):
        minutes = max(1, round(retry_after / 60))
        super().__init__(
            f"les offres ont été actualisées il y a peu : réessaie dans {minutes} min"
        )
        self.run_id = run_id
        self.retry_after = retry_after


@dataclass(frozen=True)
class RunView:
    """Une collecte telle que l'interface l'affiche."""

    id: str
    status: str
    trigger: str
    started_at: str
    finished_at: str | None
    sources: dict[str, Any]
    summary: dict[str, Any] | None
    errors: list[str]


def _now() -> datetime:
    return datetime.now(UTC)


def _view(row: Any) -> RunView:
    return RunView(
        id=row.id,
        status=row.status,
        trigger=row.trigger,
        started_at=row.started_at,
        finished_at=row.finished_at,
        sources=dict(row.sources or {}),
        summary=dict(row.summary) if row.summary else None,
        errors=list(row.errors or []),
    )


def _expire_stale(conn: Connection, now: datetime) -> None:
    conn.execute(
        update(collection_runs)
        .where(
            collection_runs.c.status == "running",
            collection_runs.c.started_at < (now - STALE_AFTER).isoformat(),
        )
        .values(
            status="failed",
            finished_at=now.isoformat(),
            errors=["collecte interrompue (aucune fin enregistrée)"],
        )
    )


def start_run(
    conn: Connection,
    *,
    trigger: str,
    sources: list[str],
    triggered_by: uuid.UUID | None = None,
    cooldown: timedelta | None = None,
) -> str:
    """Inscrit une collecte et renvoie son identifiant — ou lève si le verrou l'interdit.

    `cooldown` : délai minimal depuis le DÉBUT de la précédente collecte (réservé à
    l'interface ; la CLI, outil d'administration, ne s'y soumet pas). Une collecte en
    cours bloque dans tous les cas : deux collectes simultanées se disputeraient les
    mêmes écritures sans rien ramener de plus.
    """
    now = _now()
    _expire_stale(conn, now)
    running = conn.execute(
        select(collection_runs.c.id)
        .where(collection_runs.c.status == "running")
        .order_by(collection_runs.c.started_at, collection_runs.c.id)
    ).first()
    if running is not None:
        conn.commit()
        raise CollectionBusy(running.id)
    if cooldown is not None:
        recent = conn.execute(
            select(collection_runs.c.id, collection_runs.c.started_at)
            .where(
                collection_runs.c.status.in_(("done", "failed")),
                collection_runs.c.started_at > (now - cooldown).isoformat(),
            )
            .order_by(collection_runs.c.started_at.desc())
        ).first()
        if recent is not None:
            started = datetime.fromisoformat(recent.started_at)
            wait = int((started + cooldown - now).total_seconds()) + 1
            conn.commit()
            raise CollectionTooSoon(recent.id, wait)

    run_id = uuid.uuid4().hex
    conn.execute(
        collection_runs.insert().values(
            id=run_id,
            status="running",
            trigger=trigger,
            triggered_by=triggered_by,
            started_at=now.isoformat(),
            sources={name: {"status": "pending"} for name in sources},
            errors=[],
        )
    )
    conn.commit()

    # Deux inscriptions simultanées : la première (date, puis identifiant) l'emporte.
    first = conn.execute(
        select(collection_runs.c.id)
        .where(collection_runs.c.status == "running")
        .order_by(collection_runs.c.started_at, collection_runs.c.id)
    ).first()
    if first is not None and first.id != run_id:
        conn.execute(
            update(collection_runs)
            .where(collection_runs.c.id == run_id)
            .values(status="rejected", finished_at=_now().isoformat())
        )
        conn.commit()
        raise CollectionBusy(first.id)
    return run_id


def update_source(conn: Connection, run_id: str, source: str, report: dict) -> None:
    """Enregistre l'avancement d'UNE source (lu, modifié, réécrit : un seul fil écrit à
    la fois, voir le verrou du service)."""
    row = conn.execute(
        select(collection_runs.c.sources).where(collection_runs.c.id == run_id)
    ).first()
    if row is None:
        return
    sources = dict(row.sources or {})
    sources[source] = report
    conn.execute(
        update(collection_runs)
        .where(collection_runs.c.id == run_id)
        .values(sources=sources)
    )
    conn.commit()


def finish_run(
    conn: Connection,
    run_id: str,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    sources: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {
        "status": status,
        "finished_at": _now().isoformat(),
        "summary": summary,
        "errors": list(errors or []),
    }
    if sources is not None:
        values["sources"] = sources
    conn.execute(
        update(collection_runs).where(collection_runs.c.id == run_id).values(values)
    )
    conn.commit()


def get_run(conn: Connection, run_id: str) -> RunView | None:
    row = conn.execute(
        select(collection_runs).where(collection_runs.c.id == run_id)
    ).first()
    return _view(row) if row is not None else None


def latest_run(conn: Connection) -> RunView | None:
    """La dernière collecte LANCÉE (les demandes refusées par le verrou ne comptent pas)."""
    row = conn.execute(
        select(collection_runs)
        .where(collection_runs.c.status != "rejected")
        .order_by(collection_runs.c.started_at.desc(), collection_runs.c.id.desc())
    ).first()
    return _view(row) if row is not None else None


def healthy_sources_since(conn: Connection, since: str) -> set[str]:
    """Sources collectées AVEC SUCCÈS (terminées, au moins une offre) depuis `since`.

    C'est la condition pour déclarer une offre expirée : ne plus la voir ne prouve rien
    si la source n'a pas été interrogée, ou si elle était en panne.
    """
    healthy: set[str] = set()
    rows = conn.execute(
        select(collection_runs.c.sources).where(
            collection_runs.c.started_at >= since,
            collection_runs.c.status == "done",
        )
    )
    for row in rows:
        for name, report in (row.sources or {}).items():
            if report.get("status") == "done" and int(report.get("fetched") or 0) > 0:
                healthy.add(name)
    return healthy
