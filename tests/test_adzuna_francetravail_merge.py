"""Preuve de FUSION inter-sources par dédup EXACTE (Adzuna ⨉ France Travail), sans réseau.

Une offre vue chez Adzuna ET chez France Travail (même company|title|location, à la casse/
accents près) doit produire UN SEUL Job avec DEUX sources tracées — via la dédup existante
(`dedup_hash`) et `storage.upsert_job`, SANS aucune modification de la dédup/normalisation.
"""

from __future__ import annotations

import pytest

from jobtool.aggregator import storage
from jobtool.aggregator.adzuna.connector import map_offer_to_rawjob as adzuna_map
from jobtool.aggregator.francetravail.connector import map_offer_to_rawjob as ft_map
from jobtool.aggregator.normalize import raw_to_job


@pytest.fixture
def collision_jobs(load_fixture):
    ft_offer = load_fixture("francetravail_collision.json")["resultats"][0]
    adz_offer = load_fixture("adzuna_collision.json")["results"][0]
    ft_job = raw_to_job(ft_map(ft_offer))
    adz_job = raw_to_job(adzuna_map(adz_offer))
    return ft_job, adz_job


def test_same_dedup_hash_distinct_sources(collision_jobs):
    ft_job, adz_job = collision_jobs
    assert ft_job.dedup_hash == adz_job.dedup_hash, (
        "même dedup_hash malgré casse/accents (dédup par NORMALISATION)"
    )
    assert ft_job.source == "france_travail" and adz_job.source == "adzuna", (
        "sources distinctes"
    )
    # Minimisation FT préservée : le contact n'est pas dans le raw.
    assert "contact" not in ft_job.raw, "minimisation FT : contact absent du raw"


def test_storage_one_job_two_sources(collision_jobs, tmp_db):
    ft_job, adz_job = collision_jobs
    conn = storage.connect(tmp_db)
    try:
        new_ft = storage.upsert_job(conn, storage.LOCAL_USER_ID, ft_job)
        new_adz = storage.upsert_job(conn, storage.LOCAL_USER_ID, adz_job)
        total = storage.count_jobs(conn, storage.LOCAL_USER_ID)
        sources = storage.sources_for(conn, ft_job.dedup_hash)
    finally:
        conn.close()

    assert new_ft is True, "1ʳᵉ source (FT) : nouvelle offre insérée"
    assert new_adz is False, "2ᵉ source (Adzuna) : pas de nouvelle ligne (fusion)"
    assert total == 1, f"UN SEUL Job en base après les deux sources (vu {total})"
    assert sources == ["adzuna", "france_travail"], f"DEUX sources tracées : {sources}"
