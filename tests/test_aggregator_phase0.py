"""Preuve SANS réseau de la PHASE 0 de l'agrégateur (fondations).

À partir de RawJobs FABRIQUÉS (multi-sources, avec doublons volontaires) : normalisation,
déduplication (2 sources -> 1 Job, 2 sources tracées), insertion SQLite IDEMPOTENTE,
transitions de statut, export .txt.
"""

from __future__ import annotations

import pytest

from jobtool.aggregator import storage
from jobtool.aggregator.export import export_jobs, job_to_offer_text
from jobtool.aggregator.models import JobStatus, RawJob
from jobtool.aggregator.normalize import raw_to_job

FIXED_DATE = "2026-06-15T00:00:00+00:00"

# Deux RawJobs DÉLIBÉRÉMENT identiques (à la casse/accents/espaces près), de 2 sources.
RAW_FT = RawJob(
    source="france_travail",
    native_id="FT-1",
    title="Ingénieur Calcul Haute Performance",
    company="Helix Compute",
    location="Grenoble",
    contract_type="Stage",
    description="Optimisation de noyaux de calcul sur GPU et CPU multi-nœuds.",
    url="https://example.fr/ft/FT-1",
)
RAW_OTHER = RawJob(
    source="jobboard_x",
    native_id="X-99",
    title="ingénieur  calcul   haute performance",
    company="HELIX COMPUTE ",
    location="grenoble",
    contract_type="Internship",
    description="(même offre, autre source)",
    url="https://example.fr/x/X-99",
)
RAW_OTHER_JOB = RawJob(
    source="france_travail",
    native_id="FT-2",
    title="Data Engineer",
    company="Globex",
    location="Paris",
    description="Pipelines de données et orchestration.",
    url="https://example.fr/ft/FT-2",
)


@pytest.fixture
def jobs():
    job_ft = raw_to_job(RAW_FT, fetched_date=FIXED_DATE)
    job_other = raw_to_job(RAW_OTHER, fetched_date=FIXED_DATE)
    job_distinct = raw_to_job(RAW_OTHER_JOB, fetched_date=FIXED_DATE)
    return job_ft, job_other, job_distinct


def test_normalization_and_dedup_hash(jobs):
    job_ft, job_other, job_distinct = jobs
    assert job_ft.dedup_hash == job_other.dedup_hash, (
        "2 sources de la MÊME offre -> dedup_hash identique"
    )
    assert job_ft.dedup_hash != job_distinct.dedup_hash, (
        "une offre différente -> dedup_hash différent"
    )
    assert job_ft.id == job_ft.dedup_hash, (
        "id interne == dedup_hash (identité canonique)"
    )


def test_insert_dedup_idempotence_status(jobs, tmp_path):
    job_ft, job_other, job_distinct = jobs
    db_path = tmp_path / "jobs.db"
    conn = storage.connect(db_path)

    # Insertion + dédup multi-sources.
    new1 = storage.upsert_job(conn, storage.LOCAL_USER_ID, job_ft)
    new2 = storage.upsert_job(
        conn, storage.LOCAL_USER_ID, job_other
    )  # même dedup_hash, autre source
    new3 = storage.upsert_job(conn, storage.LOCAL_USER_ID, job_distinct)
    assert new1 and not new2 and new3, (
        "upsert : 1ʳᵉ nouvelle, 2ᵉ (doublon) connue, 3ᵉ nouvelle"
    )
    assert storage.count_jobs(conn, storage.LOCAL_USER_ID) == 2, (
        "2 offres distinctes en base (le doublon n'a pas créé de ligne)"
    )
    assert storage.sources_for(conn, job_ft.dedup_hash) == [
        "france_travail",
        "jobboard_x",
    ], "2 sources tracées"

    # Idempotence : relancer ne duplique rien.
    again = [
        storage.upsert_job(conn, storage.LOCAL_USER_ID, j)
        for j in (job_ft, job_other, job_distinct)
    ]
    assert not any(again), "relancer les mêmes upserts -> aucune nouvelle ligne"
    assert storage.count_jobs(conn, storage.LOCAL_USER_ID) == 2, (
        "toujours 2 offres après relance"
    )
    assert storage.sources_for(conn, job_ft.dedup_hash) == [
        "france_travail",
        "jobboard_x",
    ], "provenance inchangée"

    # Transitions de statut.
    assert (
        len(
            storage.list_jobs(
                conn, storage.LOCAL_USER_ID, storage.JobQuery(status=JobStatus.new)
            )
        )
        == 2
    ), "2 offres au statut 'new'"
    storage.set_status(
        conn, storage.LOCAL_USER_ID, job_distinct.dedup_hash, JobStatus.applied
    )
    assert (
        len(
            storage.list_jobs(
                conn, storage.LOCAL_USER_ID, storage.JobQuery(status=JobStatus.applied)
            )
        )
        == 1
        and len(
            storage.list_jobs(
                conn, storage.LOCAL_USER_ID, storage.JobQuery(status=JobStatus.new)
            )
        )
        == 1
    ), "1 'applied', 1 'new'"
    conn.close()


def test_export_txt(jobs, tmp_path):
    job_ft, job_other, job_distinct = jobs
    out_dir = tmp_path / "offres"
    written = export_jobs([job_ft, job_other, job_distinct], out_dir)
    assert len(written) == 2, (
        "export : 1 .txt par offre DISTINCTE (doublon non réécrit)"
    )

    text = job_to_offer_text(job_ft)
    assert "Ingénieur Calcul Haute Performance" in text, "le .txt contient le titre"
    assert "Helix Compute" in text, "le .txt contient l'entreprise"
    assert "Optimisation de noyaux de calcul" in text, "le .txt contient la description"
    assert all(p.exists() and p.read_text(encoding="utf-8").strip() for p in written), (
        "fichiers .txt non vides"
    )
