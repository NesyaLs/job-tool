"""Suivi des candidatures : `applied_at` renseigné/vidé selon le statut, tri du listing.

`applied_at` vit dans `user_offers` : c'est une donnée PRIVÉE (la date à laquelle CE
compte a postulé), pas un fait sur l'annonce. Les tests passent donc par le service, qui
porte l'identité, plutôt que par le SQL.

Note : les tests de « migration douce » d'une ancienne base jobs.db ont disparu avec le
code qu'ils couvraient. Le schéma appartient désormais à Alembic, et la reprise d'une base
locale est le rôle de scripts/import_local_to_postgres.py.
"""

from __future__ import annotations

import pytest

from jobtool.aggregator import storage
from jobtool.aggregator.models import Job, JobStatus
from jobtool.services.offers_service import OffersFilter, OffersService

# (dedup_hash, titre, entreprise, url, statut, favori)
_ROWS = [
    ("h1", "Data Scientist", "LCL", "https://x.fr/1", JobStatus.new, False),
    ("h2", "Data Engineer", "Globex", "https://x.fr/2", JobStatus.seen, True),
    # Postulée mais SANS date connue : le cas des offres reprises d'une base antérieure
    # à la colonne. On n'invente pas une date qu'on ne connaît pas.
    ("h3", "Dev Python", "Acme", "https://x.fr/3", JobStatus.applied, False),
]


def _job(dedup_hash: str, title: str, company: str, url: str, status, favorite) -> Job:
    return Job(
        id=dedup_hash,
        source="france_travail",
        native_id=f"FT-{dedup_hash}",
        title=title,
        company=company,
        location="Paris",
        remote="inconnu",
        contract_type="CDI",
        salary=None,
        description=f"Description de {title}",
        url=url,
        posted_date="2026-07-01T08:00:00+00:00",
        fetched_date="2026-07-02T08:00:00+00:00",
        dedup_hash=dedup_hash,
        status=status,
        favorite=favorite,
        applied_at=None,
        raw={"k": "v"},
    )


@pytest.fixture
def seeded_db(tmp_db):
    """Base peuplée par le CHEMIN RÉEL (upsert_job), avec statuts et favoris variés."""
    conn = storage.connect(tmp_db)
    try:
        for row in _ROWS:
            storage.upsert_job(conn, storage.LOCAL_USER_ID, _job(*row))
    finally:
        conn.close()
    return tmp_db


def test_seeded_state_is_readable(seeded_db):
    """Point de départ : statuts, favoris et payload brut sont bien relus."""
    service = OffersService(seeded_db)
    jobs = {j.dedup_hash: j for j in service.list_offers()}
    assert len(jobs) == 3
    for h, title, company, url, status, favorite in _ROWS:
        job = jobs[h]
        assert job.title == title and job.company == company and job.url == url
        assert job.status is status and job.favorite is favorite
        assert job.raw == {"k": "v"}, "le payload brut est intact"
        assert job.applied_at is None


def test_writes_are_persisted(seeded_db):
    """Statut et favori s'écrivent puis se relisent (colonnes réellement utilisables)."""
    service = OffersService(seeded_db)
    assert service.update_status("h1", JobStatus.applied)
    assert service.set_favorite("h1", True)
    job = service.get_offer("h1")
    assert job is not None and job.status is JobStatus.applied and job.favorite


# --- applied_at : renseigné / vidé selon le statut ---------------------------


def test_applying_stamps_applied_at(seeded_db):
    service = OffersService(seeded_db)
    assert service.get_offer("h1").applied_at is None
    service.update_status("h1", JobStatus.applied)
    stamped = service.get_offer("h1").applied_at
    assert stamped, "passer à 'applied' horodate la candidature"


def test_leaving_applied_clears_applied_at(seeded_db):
    """Statut quitté -> la date de candidature est effacée (pas de fantôme en historique)."""
    service = OffersService(seeded_db)
    service.update_status("h2", JobStatus.applied)
    assert service.get_offer("h2").applied_at
    service.update_status("h2", JobStatus.seen)
    assert service.get_offer("h2").applied_at is None


def test_applied_at_is_stable_across_reapply(seeded_db):
    """Re-marquer 'applied' ne réécrit pas la date : c'est la date de la 1re candidature."""
    service = OffersService(seeded_db)
    service.update_status("h1", JobStatus.applied)
    first = service.get_offer("h1").applied_at
    service.update_status("h1", JobStatus.applied)
    assert service.get_offer("h1").applied_at == first


# --- Listing « Candidatures » -----------------------------------------------


def test_applications_listing_is_sorted_by_applied_at_desc(seeded_db):
    """L'écran Candidatures : status=applied + order=applied_at, plus récentes d'abord."""
    service = OffersService(seeded_db)
    for h in ("h1", "h2"):
        service.update_status(h, JobStatus.applied)
        # Horodatages distincts garantis par l'écriture successive (résolution µs).
    jobs = service.list_offers(
        OffersFilter(status=JobStatus.applied, order="applied_at")
    )
    stamped = [j for j in jobs if j.applied_at]
    assert [j.dedup_hash for j in stamped] == ["h2", "h1"], (
        "dernière candidature en tête"
    )
    # h3 est 'applied' sans date connue : il ferme la marche.
    assert jobs[-1].dedup_hash == "h3" and jobs[-1].applied_at is None
