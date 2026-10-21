"""Service de COLLECTE des offres (printless) — partagé CLI + API.

Une collecte interroge toutes les sources ACTIVÉES dans le config.yaml et alimente le
CATALOGUE commun. Elle ne dépend d'aucun compte et ne change l'état de personne : la
RECHERCHE, elle, filtre ensuite ce catalogue (voir offers_service).

Déroulé : `start_collection` inscrit la collecte (verrou : une seule à la fois, et pas
plus d'une par quart d'heure depuis l'interface), puis `execute_collection` interroge
les sources en parallèle, enregistre l'avancement de chacune (lu par l'interface pendant
que la collecte tourne en arrière-plan), range les offres au catalogue (dédoublonnage
entre sources), purge les offres expirées et enregistre le bilan. `run_aggregation`
enchaîne les deux pour la CLI.

AUCUN print : la présentation appartient aux couches d'interface. Les erreurs par source
sont JOURNALISÉES (logging) et ENREGISTRÉES dans le suivi de la collecte.

`pipeline.run_collection` est appelé via l'ATTRIBUT du module (pas un import direct du
nom) pour rester remplaçable dans les tests sans réseau.

Sens des dépendances : services -> domaines (aggregator) -> core. N'importe ni cli/ ni api/.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from jobtool.aggregator import catalogue, pipeline, runs, storage
from jobtool.aggregator.config import load_collection_config
from jobtool.aggregator.models import CollectionConfig, SearchCriteria
from jobtool.aggregator.pipeline import ProgressCallback, RunSummary, SourceReport
from jobtool.aggregator.registry import (
    ALL_SOURCES,
    BuildContext,
    build_connectors,
    enabled_sources,
)
from jobtool.aggregator.runs import CollectionBusy, CollectionTooSoon, RunView
from jobtool.core.db import database_for, engine_for_sqlite_file
from jobtool.settings import AggregatorSettings

logger = logging.getLogger(__name__)

__all__ = [
    "AggregationReport",
    "CollectionBusy",
    "CollectionTooSoon",
    "execute_collection",
    "get_collection",
    "latest_collection",
    "run_aggregation",
    "start_collection",
]


@dataclass
class AggregationReport:
    """Issue d'une collecte : le bilan du pipeline + le contexte effectif (affichage).

    `summary.errors` inclut DÉJÀ les erreurs préalables de construction (pre_errors).
    `usable_sources` = nombre de connecteurs réellement construits (0 = aucune source
    utilisable : identifiants manquants / entreprises non configurées).
    """

    summary: RunSummary
    sources: list[str]  # noms de sources résolus/interrogés
    criteria: (
        SearchCriteria  # 1re recherche EFFECTIVE (après overrides) — pour l'affichage
    )
    searches: list[SearchCriteria]  # toutes les recherches effectives
    usable_sources: int
    config_path: str
    db_path: str
    export_dir: str
    run_id: str | None = None
    pre_errors: list[str] | None = None  # sources écartées avant la collecte (clés…)
    purged: int = 0  # offres expirées retirées du catalogue
    kept_expired: int = 0  # offres expirées conservées (un compte y tient)


def _db_label(db: str | Path | Engine) -> str:
    """Description AFFICHABLE de la base visée, sans jamais divulguer de secret."""
    if isinstance(db, Engine):
        return db.url.render_as_string(hide_password=True)
    return str(db)


def _target_db(
    db: str | Path | Engine | None, settings: AggregatorSettings
) -> str | Path | Engine:
    """La base de l'application (même bascule que l'API), sauf base donnée."""
    if db is not None:
        return db
    return database_for(settings.database_url, settings.db_path)


def _config(
    config_path: str | Path | None, settings: AggregatorSettings
) -> tuple[str, CollectionConfig]:
    path = str(config_path or settings.config_path)
    return path, load_collection_config(path, ALL_SOURCES)


def _source_names(sources: list[str] | None, config: CollectionConfig) -> list[str]:
    names = list(sources) if sources else enabled_sources(config)
    unknown = [s for s in names if s not in ALL_SOURCES]
    if unknown:
        raise ValueError(
            f"source(s) inconnue(s) : {', '.join(unknown)} "
            f"(connues : {', '.join(ALL_SOURCES)})"
        )
    return names


# --- Verrou -------------------------------------------------------------------


def start_collection(
    db: str | Path | Engine | None = None,
    *,
    trigger: str,
    triggered_by: uuid.UUID | None = None,
    enforce_cooldown: bool = True,
    sources: list[str] | None = None,
    config_path: str | Path | None = None,
    settings: AggregatorSettings | None = None,
) -> str:
    """Inscrit une collecte et renvoie son identifiant.

    Lève `CollectionBusy` (une collecte tourne déjà — son identifiant est joint, pour la
    suivre) ou `CollectionTooSoon` (la précédente date de moins de
    `collection_cooldown_minutes`, si `enforce_cooldown`). La CLI, outil
    d'administration, ne se soumet pas au délai ; l'interface, si.
    """
    settings = settings or AggregatorSettings()
    _, config = _config(config_path, settings)
    names = _source_names(sources, config)
    cooldown = (
        timedelta(minutes=settings.collection_cooldown_minutes)
        if enforce_cooldown and settings.collection_cooldown_minutes > 0
        else None
    )
    conn = storage.open_connection(_target_db(db, settings))
    try:
        return runs.start_run(
            conn,
            trigger=trigger,
            sources=names,
            triggered_by=triggered_by,
            cooldown=cooldown,
        )
    finally:
        conn.close()


# --- Exécution ----------------------------------------------------------------


def _report_dict(report: SourceReport) -> dict[str, Any]:
    data = asdict(report)
    data.pop("source", None)
    return data


def execute_collection(
    run_id: str,
    db: str | Path | Engine | None = None,
    *,
    sources: list[str] | None = None,
    keyword: str | None = None,
    location: str | None = None,
    max_results: int | None = None,
    config_path: str | Path | None = None,
    export_dir: str | Path | None = None,
    export: bool = False,
    debug: bool = False,
    settings: AggregatorSettings | None = None,
    on_progress: ProgressCallback | None = None,
) -> AggregationReport:
    """Exécute une collecte inscrite par `start_collection`.

    Les erreurs d'une SOURCE ne lèvent pas (isolation, bilan par source). Une erreur
    inattendue de la collecte elle-même est enregistrée dans le suivi (statut `failed`,
    que l'interface affiche) et journalisée, puis relevée.

    `sources` : absent -> les sources ACTIVÉES dans la config. `keyword` + `location`
    remplacent les recherches de la config par UNE recherche (usage CLI) ; `max_results`
    s'applique à toutes les sources. `export` : écrire un .txt par offre (CLI).
    """
    settings = settings or AggregatorSettings()
    target = _target_db(db, settings)
    # Un moteur pour toute la collecte : l'avancement est écrit depuis les fils des
    # sources, chacun avec sa connexion. Le schéma local est garanti par une ouverture.
    storage.open_connection(target).close()
    engine = target if isinstance(target, Engine) else engine_for_sqlite_file(target)
    write_lock = threading.Lock()

    def progress(report: SourceReport) -> None:
        with write_lock, engine.connect() as conn:
            runs.update_source(conn, run_id, report.source, _report_dict(report))
        if report.status in ("done", "failed"):
            logger.info(
                "collecte %s : %s %s, %d offre(s), %d erreur(s)",
                run_id[:8],
                report.source,
                report.status,
                report.fetched,
                len(report.errors),
            )
            for error in report.errors:
                logger.warning(
                    "collecte %s : %s : %s", run_id[:8], report.source, error
                )
        if on_progress is not None:
            on_progress(report)

    report: AggregationReport | None = None
    try:
        report = _collect(
            engine,
            settings=settings,
            sources=sources,
            keyword=keyword,
            location=location,
            max_results=max_results,
            config_path=config_path,
            export_dir=export_dir,
            export=export,
            debug=debug,
            on_progress=progress,
        )
        report.run_id = run_id
        report.db_path = _db_label(target)
        summary = report.summary
        with engine.connect() as conn:
            runs.finish_run(
                conn,
                run_id,
                status="done",
                summary={
                    "fetched": summary.fetched,
                    "distinct": summary.distinct,
                    "new_offers": summary.new,
                    "already_known": summary.already_known,
                    "by_level": summary.by_level,
                    "purged": report.purged,
                    "kept_expired": report.kept_expired,
                },
                errors=list(report.pre_errors or []),
                sources={
                    # Une source écartée avant la collecte (identifiants absents) n'a pas
                    # de bilan : elle est marquée `skipped` plutôt que laissée en attente.
                    name: (
                        _report_dict(summary.per_source[name])
                        if name in summary.per_source
                        else {"status": "skipped", "fetched": 0, "errors": []}
                    )
                    for name in report.sources
                },
            )
        logger.info(
            "collecte %s terminée : %d récupérées, %d nouvelles, niveaux %s",
            run_id[:8],
            summary.fetched,
            summary.new,
            summary.by_level,
        )
    except Exception as exc:  # noqa: BLE001 — la collecte tourne en arrière-plan
        logger.exception("collecte %s en échec", run_id[:8])
        with engine.connect() as conn:
            runs.finish_run(
                conn,
                run_id,
                status="failed",
                errors=[f"{type(exc).__name__}: {exc}"],
            )
        if report is None:
            raise
    finally:
        if engine is not target:
            engine.dispose()
    return report


def _collect(
    engine: Engine,
    *,
    settings: AggregatorSettings,
    sources: list[str] | None,
    keyword: str | None,
    location: str | None,
    max_results: int | None,
    config_path: str | Path | None,
    export_dir: str | Path | None,
    export: bool,
    debug: bool,
    on_progress: ProgressCallback,
) -> AggregationReport:
    config_path, config = _config(config_path, settings)
    names = _source_names(sources, config)
    export_path = str(export_dir or settings.export_dir)

    searches = list(config.searches)
    if keyword is not None or location is not None:
        base = searches[0] if searches else SearchCriteria()
        update: dict = {}
        if keyword is not None:
            update["mots_cles"] = keyword
        if location is not None:
            update["departement"] = location
        searches = [base.model_copy(update=update)]
    if max_results is not None:
        searches = [s.model_copy(update={"max_results": max_results}) for s in searches]
    limits = {
        name: limit
        for name in names
        if max_results is None and (limit := config.source(name).max_results)
    }

    ctx = BuildContext(
        settings=settings,
        smartrecruiters=config.smartrecruiters,
        workday=config.workday,
        debug=debug or settings.agg_debug,
    )
    connectors, pre_errors = build_connectors(names, ctx)
    report = AggregationReport(
        summary=RunSummary(errors=list(pre_errors)),
        sources=names,
        criteria=searches[0] if searches else SearchCriteria(),
        searches=searches,
        usable_sources=len(connectors),
        config_path=config_path,
        db_path=_db_label(engine),
        export_dir=export_path,
        pre_errors=list(pre_errors),
    )
    if not connectors:
        # Aucune source utilisable : on ne touche pas au catalogue.
        return report

    summary = pipeline.run_collection(
        connectors,
        searches,
        engine,
        export_path if export else None,
        limits=limits,
        on_progress=on_progress,
    )
    summary.errors = pre_errors + summary.errors
    report.summary = summary
    report.purged, report.kept_expired = _purge(engine, settings)
    return report


def _purge(engine: Engine, settings: AggregatorSettings) -> tuple[int, int]:
    """Retire les offres expirées (voir catalogue.purge_expired). 0 jour = jamais."""
    days = settings.catalogue_retention_days
    if days <= 0:
        return 0, 0
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with engine.connect() as conn:
        healthy = runs.healthy_sources_since(conn, cutoff)
        result = catalogue.purge_expired(conn, cutoff=cutoff, healthy_sources=healthy)
        conn.commit()
    if result.deleted or result.kept_expired:
        logger.info(
            "purge du catalogue : %d offre(s) expirée(s) retirée(s), %d conservée(s)",
            result.deleted,
            result.kept_expired,
        )
    return result.deleted, result.kept_expired


def run_aggregation(
    *,
    sources: list[str] | None = None,
    keyword: str | None = None,
    location: str | None = None,
    max_results: int | None = None,
    config_path: str | Path | None = None,
    db: str | Path | Engine | None = None,
    export_dir: str | Path | None = None,
    export: bool = True,
    debug: bool = False,
    settings: AggregatorSettings | None = None,
    on_progress: ProgressCallback | None = None,
) -> AggregationReport:
    """Collecte complète, au premier plan (CLI) : inscription puis exécution.

    Lève `CollectionBusy` si une collecte tourne déjà (lancée depuis l'interface, par
    exemple) ; pas de délai minimal pour la CLI. `sources` inconnue(s) -> ValueError.
    """
    settings = settings or AggregatorSettings()
    target = _target_db(db, settings)
    run_id = start_collection(
        target,
        trigger="cli",
        enforce_cooldown=False,
        sources=sources,
        config_path=config_path,
        settings=settings,
    )
    return execute_collection(
        run_id,
        target,
        sources=sources,
        keyword=keyword,
        location=location,
        max_results=max_results,
        config_path=config_path,
        export_dir=export_dir,
        export=export,
        debug=debug,
        settings=settings,
        on_progress=on_progress,
    )


# --- Suivi ------------------------------------------------------------------------


def get_collection(db: str | Path | Engine, run_id: str) -> RunView | None:
    """Une collecte par son identifiant (avancement par source, bilan), ou None."""
    conn = storage.open_connection(db)
    try:
        return runs.get_run(conn, run_id)
    finally:
        conn.close()


def latest_collection(db: str | Path | Engine) -> RunView | None:
    """La dernière collecte lancée (en cours ou terminée), ou None."""
    conn = storage.open_connection(db)
    try:
        return runs.latest_run(conn)
    finally:
        conn.close()
