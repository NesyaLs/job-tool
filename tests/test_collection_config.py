"""Collecte : config multi-recherches, sources activées, parallélisme, même base que l'API.

Sans réseau : les connecteurs sont des doublures qui enregistrent ce qu'on leur demande.
"""

from __future__ import annotations

import threading
import time

import pytest

from jobtool.aggregator.config import ConfigError, load_collection_config
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.models import CollectionConfig, RawJob, SearchCriteria
from jobtool.aggregator.pipeline import fetch_all, plan_criteria
from jobtool.aggregator.registry import ALL_SOURCES, enabled_sources
from jobtool.core.db import database_for


def _write(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- Chargement --------------------------------------------------------------
def test_searches_inherit_criteres_defaults(tmp_path):
    cfg = load_collection_config(
        _write(
            tmp_path,
            """
criteres: {departement: "75,92", max_results: 40}
recherches:
  - motsCles: data scientist
  - mots_cles: data analyst
    departement: "33"
""",
        ),
        ALL_SOURCES,
    )
    assert [(s.mots_cles, s.departement, s.max_results) for s in cfg.searches] == [
        ("data scientist", "75,92", 40),
        ("data analyst", "33", 40),
    ]


def test_legacy_criteres_is_a_single_search(tmp_path):
    cfg = load_collection_config(
        _write(tmp_path, "criteres: {motsCles: python}\n"), ALL_SOURCES
    )
    assert [s.mots_cles for s in cfg.searches] == ["python"]


def test_sources_section_enables_and_caps(tmp_path):
    cfg = load_collection_config(
        _write(
            tmp_path,
            """
sources:
  adzuna: {enabled: false}
  workday: {max_results: 12}
""",
        ),
        ALL_SOURCES,
    )
    assert enabled_sources(cfg) == ["france_travail", "smartrecruiters", "workday"]
    assert cfg.source("workday").max_results == 12
    assert cfg.source("france_travail").enabled, "absente de la section -> activée"


def test_unknown_source_name_is_an_error(tmp_path):
    # Une faute de frappe ne doit pas laisser la source activée en silence.
    with pytest.raises(ConfigError, match="smartrecruiter"):
        load_collection_config(
            _write(tmp_path, "sources: {smartrecruiter: {enabled: false}}\n"),
            ALL_SOURCES,
        )


def test_ats_sections_are_read(tmp_path):
    cfg = load_collection_config(
        _write(
            tmp_path,
            """
smartrecruiters: {companies: [BoschGroup], country: fr, max_per_company: 5}
workday: {sites: ["https://bdf.wd103.myworkdayjobs.com/fr-FR/x"], country: France}
""",
        ),
        ALL_SOURCES,
    )
    assert cfg.smartrecruiters.companies == ["BoschGroup"]
    assert cfg.smartrecruiters.max_per_company == 5
    assert cfg.workday.country == "France"


def test_project_config_enables_every_source_with_verified_ats():
    """Le config.yaml du dépôt : toutes les sources actives, ATS non vides."""
    cfg = load_collection_config("data/aggregator/config.yaml", ALL_SOURCES)
    assert enabled_sources(cfg) == list(ALL_SOURCES)
    assert "BoschGroup" in cfg.smartrecruiters.companies
    assert any("bdf.wd103" in s for s in cfg.workday.sites)


# --- Plan d'appels -----------------------------------------------------------
class _Recorder(Connector):
    def __init__(self, source: str, uses_search: bool = True, delay: float = 0.0):
        super().__init__()
        self.source = source
        self.uses_search = uses_search
        self.calls: list[SearchCriteria] = []
        self.threads: set[str] = set()
        self._delay = delay

    def fetch(self, criteria: SearchCriteria) -> list[RawJob]:
        self.errors = []
        self.calls.append(criteria)
        self.threads.add(threading.current_thread().name)
        time.sleep(self._delay)
        return [
            RawJob(source=self.source, native_id=f"{self.source}-1", title="T"),
        ]


SEARCHES = [
    SearchCriteria(mots_cles="data scientist", departement="75", max_results=50),
    SearchCriteria(mots_cles="data analyst", departement="75", max_results=50),
]


def test_search_source_runs_every_search_with_its_cap():
    ft = _Recorder("france_travail")
    plan = plan_criteria(ft, SEARCHES, {"france_travail": 7})
    assert [(c.mots_cles, c.max_results) for c in plan] == [
        ("data scientist", 7),
        ("data analyst", 7),
    ]


def test_ats_runs_once_without_keyword_nor_insee_geo():
    ats = _Recorder("smartrecruiters", uses_search=False)
    plan = plan_criteria(ats, SEARCHES, {"smartrecruiters": 300})
    assert len(plan) == 1
    assert plan[0].mots_cles == "" and plan[0].departement is None
    assert plan[0].max_results == 300


def test_sources_run_in_parallel_and_duplicates_within_a_source_collapse():
    slow = [_Recorder(n, delay=0.2) for n in ("a", "b", "c")]
    started = time.monotonic()
    raws, reports = fetch_all(slow, SEARCHES)
    elapsed = time.monotonic() - started
    # 3 sources × 2 recherches × 0,2 s : ~1,2 s en série, ~0,4 s en parallèle.
    assert elapsed < 0.9, f"sources interrogées en série ({elapsed:.2f} s)"
    assert len({t for c in slow for t in c.threads}) == 3, "un fil par source"
    # Chaque source renvoie deux fois la même offre (une par recherche) : une seule gardée.
    assert [r.source for r in raws] == ["a", "b", "c"], "ordre des connecteurs, stable"
    assert all(rep.status == "done" and rep.fetched == 1 for rep in reports.values())
    assert reports["a"].searches_done == 2


def test_failing_source_is_isolated_and_reported():
    class Boom(_Recorder):
        def fetch(self, criteria):
            raise RuntimeError("panne")

    raws, reports = fetch_all([Boom("x"), _Recorder("y")], SEARCHES)
    assert [r.source for r in raws] == ["y"]
    assert reports["x"].status == "failed"
    assert "panne" in reports["x"].errors[0]


def test_progress_callback_sees_each_source_finish():
    seen: list[tuple[str, str]] = []
    lock = threading.Lock()

    def on_progress(report):
        with lock:
            seen.append((report.source, report.status))

    fetch_all([_Recorder("a"), _Recorder("b")], SEARCHES, on_progress=on_progress)
    assert ("a", "done") in seen and ("b", "done") in seen
    assert ("a", "running") in seen


# --- Même base que l'API -----------------------------------------------------
def test_cli_database_follows_database_url(tmp_path):
    assert database_for(None, tmp_path / "jobs.db") == str(tmp_path / "jobs.db")
    engine = database_for(f"sqlite:///{tmp_path / 'shared.db'}", tmp_path / "jobs.db")
    assert not isinstance(engine, str), "DATABASE_URL définie -> moteur, pas le fichier"
    assert engine.url.database.endswith("shared.db")


def test_default_config_is_empty_but_valid():
    cfg = CollectionConfig()
    assert enabled_sources(cfg) == list(ALL_SOURCES)


def test_service_defaults_to_enabled_sources_and_shared_database(tmp_path, monkeypatch):
    """Sans `sources` ni `db` : sources activées de la config, base de DATABASE_URL."""
    from jobtool.aggregator import pipeline
    from jobtool.services.aggregation_service import run_aggregation
    from jobtool.settings import AggregatorSettings

    cfg = _write(
        tmp_path,
        """
criteres: {motsCles: data}
sources: {france_travail: {enabled: false}}
smartrecruiters: {companies: [BoschGroup]}
""",
    )
    captured: dict = {}

    def fake_run(connectors, criteria, db, export_dir, *args, **kwargs):
        captured["sources"] = [c.source for c in connectors]
        captured["db"] = db
        return pipeline.RunSummary()

    monkeypatch.setattr(pipeline, "run_collection", fake_run)
    shared = f"sqlite:///{tmp_path / 'shared.db'}"
    # Une base désignée par DATABASE_URL a son schéma (c'est Alembic qui le pose) :
    # on le crée ici comme le ferait une migration.
    from jobtool.aggregator import storage

    storage.connect(tmp_path / "shared.db").close()
    report = run_aggregation(
        config_path=cfg,
        export=False,
        settings=AggregatorSettings(database_url=shared),
    )
    assert captured["sources"] == ["adzuna", "smartrecruiters", "workday"]
    assert not isinstance(captured["db"], str), "moteur partagé, pas le fichier local"
    assert "shared.db" in report.db_path
