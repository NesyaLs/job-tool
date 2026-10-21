"""Preuve SANS réseau du branchement multi-sources (pipeline.run_collection).

Connecteurs FACTICES (sous-classes de Connector, aucun réseau) : prouve qu'une run sur
plusieurs sources fusionne par dédup (1 Job, 2 sources), isole un connecteur en panne (la run
continue), propage les erreurs non fatales, et exporte un .txt par offre distincte.
"""

from __future__ import annotations

import pytest

from jobtool.aggregator import storage
from jobtool.aggregator.adzuna.connector import map_offer_to_rawjob as adzuna_map
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.francetravail.connector import map_offer_to_rawjob as ft_map
from jobtool.aggregator.models import SearchCriteria
from jobtool.aggregator.normalize import raw_to_job
from jobtool.aggregator.pipeline import run_collection


class StubConnector(Connector):
    """Connecteur factice : renvoie des RawJob présets, ou simule une panne (boom)."""

    def __init__(self, source, raws, errors=None, boom=False):
        super().__init__()
        self.source = source
        self._raws = raws
        self._preset_errors = errors or []
        self._boom = boom

    def fetch(self, criteria):
        if self._boom:
            raise RuntimeError("connecteur en panne")
        self.errors = list(self._preset_errors)
        return list(self._raws)


@pytest.fixture
def multi_run(load_fixture, tmp_path):
    ft_collision = ft_map(load_fixture("francetravail_collision.json")["resultats"][0])
    adz_collision = adzuna_map(load_fixture("adzuna_collision.json")["results"][0])
    adz_other = adzuna_map(
        load_fixture("adzuna_search.json")["results"][0]
    )  # offre distincte

    ft = StubConnector("france_travail", [ft_collision])
    adz = StubConnector(
        "adzuna", [adz_collision, adz_other], errors=["adzuna : page 2 ignorée"]
    )
    boom = StubConnector("brokensource", [], boom=True)

    db = tmp_path / "multi.db"
    out = tmp_path / "offres"
    # Une collecte ne dépend d'aucun compte : elle alimente le catalogue commun.
    summary = run_collection([ft, adz, boom], SearchCriteria(max_results=50), db, out)
    collision_hash = raw_to_job(ft_collision).dedup_hash
    return summary, db, collision_hash


def test_summary_counts(multi_run):
    summary, _, _ = multi_run
    assert summary.fetched == 3, "3 RawJob récupérés (1 FT + 2 Adzuna)"
    assert summary.distinct == 2, (
        "2 offres distinctes (la collision FT⨉Adzuna fusionne)"
    )
    assert summary.new == 2, "2 nouvelles offres insérées"
    assert summary.exported == 2, "2 fichiers .txt exportés (1 par offre distincte)"


def test_error_isolation_and_propagation(multi_run):
    summary, _, _ = multi_run
    assert any("panne" in e for e in summary.errors), (
        "connecteur en PANNE isolé (run poursuivie, erreur notée)"
    )
    assert any("page 2 ignorée" in e for e in summary.errors), (
        "erreurs non fatales propagées au résumé"
    )


def test_merge_verified_in_db(multi_run):
    _, db, collision_hash = multi_run
    conn = storage.connect(db)
    try:
        total = storage.count_jobs(conn, storage.LOCAL_USER_ID)
        sources = storage.sources_for(conn, collision_hash)
    finally:
        conn.close()
    assert total == 2, f"2 Jobs en base (vu {total})"
    assert sources == ["adzuna", "france_travail"], (
        f"l'offre collisionnante trace ses 2 sources : {sources}"
    )
