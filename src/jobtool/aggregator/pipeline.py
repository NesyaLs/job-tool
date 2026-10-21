"""Orchestration synchrone d'une run d'agrégation : fetch -> normalize -> dédup/store -> export.

Ne connaît PAS le générateur (CV/lettre) : il écrit seulement des .txt que le pipeline
existant lira séparément.

Point d'entrée UNIQUE : `run_collection`. Il a longtemps coexisté avec une variante
mono-connecteur, qui réappliquait la même dédup et le même export sur une liste d'un
élément ; la variante n'avait plus d'appelant et a été supprimée.

PARALLÉLISME : les sources sont interrogées EN MÊME TEMPS (un fil par source), les
recherches d'une même source l'une après l'autre. Chaque connecteur garde son propre
limiteur de débit : le parallélisme n'accélère que ce qui ne se gêne pas. C'est ce qui
ramène la durée d'une collecte à celle de sa source la plus lente, au lieu de leur somme.
Tout ce qui touche la base reste dans le fil appelant, après la collecte.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from jobtool.aggregator import catalogue, storage
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.models import Job, RawJob, SearchCriteria
from jobtool.aggregator.normalize import raw_to_job


@dataclass
class SourceReport:
    """Avancement et bilan d'UNE source pendant une collecte.

    `status` : `pending` (pas commencée), `running`, `done` (terminée, éventuellement avec
    des erreurs non fatales) ou `failed` (exception inattendue : rien de cette source).
    """

    source: str
    status: str = "pending"
    searches_done: int = 0
    searches_total: int = 0
    fetched: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None


#: Appelé à chaque changement d'état d'une source, DEPUIS LE FIL de cette source.
ProgressCallback = Callable[[SourceReport], None]


@dataclass
class RunSummary:
    """Bilan d'une collecte (affiché par la CLI, enregistré pour l'interface)."""

    fetched: int = 0  # nb de RawJob renvoyés par le(s) connecteur(s)
    distinct: int = 0  # offres CANONIQUES distinctes touchées (après dédoublonnage)
    new: int = 0  # offres canoniques CRÉÉES au catalogue par cette collecte
    already_known: int = 0  # offres distinctes qui existaient déjà au catalogue
    #: Rattachements par niveau de dédoublonnage (identifiant, url, approchee, nouvelle).
    by_level: dict[str, int] = field(default_factory=dict)
    exported: int = 0  # nb de fichiers .txt écrits
    errors: list[str] = field(default_factory=list)
    per_source: dict[str, SourceReport] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def plan_criteria(
    connector: Connector,
    searches: list[SearchCriteria],
    limits: dict[str, int] | None = None,
) -> list[SearchCriteria]:
    """Critères effectivement passés à `connector.fetch`, un par appel.

    Source par recherche : une fois par recherche, plafonnée par la limite de la source.
    ATS : une seule fois, SANS mot-clé ni géo INSEE (ils ne s'y appliquent pas : l'ATS
    ramène les offres des entreprises listées, que la recherche filtrera ensuite).
    """
    limit = (limits or {}).get(connector.source)
    if connector.uses_search:
        return [
            s.model_copy(update={"max_results": limit}) if limit else s
            for s in searches
        ]
    base_max = limit or (searches[0].max_results if searches else 150)
    return [SearchCriteria(max_results=base_max)]


def _fetch_source(
    connector: Connector,
    criteria: list[SearchCriteria],
    on_progress: ProgressCallback | None,
) -> tuple[list[RawJob], SourceReport]:
    """Toutes les recherches d'UNE source, dans son fil. Ne lève pas (isolation).

    Une même offre revient souvent dans deux recherches voisines (« data scientist » et
    « data analyst ») : les doublons de (source, native_id) sont retirés ici.
    """
    report = SourceReport(
        source=getattr(connector, "source", "?"),
        status="running",
        searches_total=len(criteria),
        started_at=_now(),
    )
    notify = on_progress or (lambda _r: None)
    notify(report)
    raws: list[RawJob] = []
    seen: set[str] = set()
    try:
        for crit in criteria:
            part = connector.fetch(crit)
            # Une même panne se répète d'une recherche à l'autre (clés absentes,
            # entreprise invalide) : on ne la compte qu'une fois.
            for error in getattr(connector, "errors", []):
                if error not in report.errors:
                    report.errors.append(error)
            for raw in part:
                key = raw.native_id or f"#{len(raws)}"
                if key in seen:
                    continue
                seen.add(key)
                raws.append(raw)
            report.searches_done += 1
            report.fetched = len(raws)
            notify(report)
        report.status = "done"
    except Exception as exc:  # noqa: BLE001 — filet ultime : isolation des connecteurs
        report.status = "failed"
        report.errors.append(f"exception inattendue {type(exc).__name__}: {exc}")
    report.finished_at = _now()
    notify(report)
    return raws, report


def fetch_all(
    connectors: list[Connector],
    searches: list[SearchCriteria],
    *,
    limits: dict[str, int] | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[RawJob], dict[str, SourceReport]]:
    """Interroge toutes les sources EN PARALLÈLE ; renvoie les offres et le bilan par source.

    L'ordre du résultat suit celui des connecteurs (pas celui d'arrivée) : deux collectes
    identiques produisent la même liste, ce qui garde la dédup déterministe.
    """
    if not connectors:
        return [], {}
    with ThreadPoolExecutor(max_workers=len(connectors)) as pool:
        futures = [
            pool.submit(
                _fetch_source, c, plan_criteria(c, searches, limits), on_progress
            )
            for c in connectors
        ]
        results = [f.result() for f in futures]
    raws: list[RawJob] = []
    reports: dict[str, SourceReport] = {}
    for part, report in results:
        raws.extend(part)
        reports[report.source] = report
    return raws, reports


def run_collection(
    connectors: list[Connector],
    criteria: SearchCriteria | list[SearchCriteria],
    db: str | Path | Engine,
    export_dir: str | Path | None = None,
    fetched_date: str | None = None,
    *,
    limits: dict[str, int] | None = None,
    on_progress: ProgressCallback | None = None,
) -> RunSummary:
    """Collecte : interroge les sources, puis range les offres au CATALOGUE commun.

    `criteria` : une recherche ou une liste de recherches (voir `plan_criteria`). `limits` :
    plafond par source (`max_results` de la section `sources:` du config.yaml).

    Chaque connecteur est isolé : son `fetch` ne lève pas (et un filet ultime capture une
    exception inattendue) — un échec d'une source N'INTERROMPT NI la collecte NI les
    autres. Les offres sont dédoublonnées ENTRE SOURCES par le catalogue (niveaux
    identifiant, lien, empreinte approchée : aggregator/dedup.py).

    AUCUN compte ici : une collecte alimente le catalogue commun et ne change l'état de
    personne. Ce qu'elle ramène devient visible de tous, sans masquer quoi que ce soit.

    `export_dir` : None -> aucun .txt écrit (la CLI en écrit pour le traitement par lots).
    """
    summary = RunSummary()
    searches = criteria if isinstance(criteria, list) else [criteria]

    raws, reports = fetch_all(
        connectors, searches, limits=limits, on_progress=on_progress
    )
    summary.per_source = reports
    for report in reports.values():
        summary.errors.extend(f"{report.source} : {e}" for e in report.errors)
    summary.fetched = len(raws)

    jobs = [raw_to_job(raw, fetched_date=fetched_date) for raw in raws]

    conn = storage.open_connection(db)
    try:
        # Occurrences collectées sous une règle de normalisation antérieure : mises à
        # niveau avant d'écrire les nouvelles, pour que la recherche filtre tout.
        storage.renormalize_catalogue(conn)
        # Toute la collecte en une poignée de requêtes : sur une base distante, c'est le
        # NOMBRE d'allers-retours qui décide de la durée, pas le volume de données.
        outcomes = catalogue.upsert_catalogue(conn, jobs)
        conn.commit()
    finally:
        conn.close()

    distinct: dict[str, Job] = {}
    for job, outcome in zip(jobs, outcomes, strict=True):
        summary.by_level[outcome.level] = summary.by_level.get(outcome.level, 0) + 1
        if outcome.offer_id not in distinct:
            distinct[outcome.offer_id] = job.model_copy(
                update={"dedup_hash": outcome.offer_id, "id": outcome.offer_id}
            )
    summary.new = sum(1 for o in outcomes if o.created)
    summary.distinct = len(distinct)
    summary.already_known = summary.distinct - summary.new

    if export_dir is not None:
        from jobtool.aggregator.export import export_jobs

        written = export_jobs(list(distinct.values()), export_dir)
        summary.exported = len(written)
    return summary
