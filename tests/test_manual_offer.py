"""Ajout MANUEL d'une offre : service, route POST /offers, et parité avec les offres collectées.

Une offre saisie à la main doit se comporter comme les autres partout : statuts, favori,
description éditable, tri, filtres, génération. Ces tests le prouvent bout en bout.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jobtool.aggregator.models import JobStatus
from jobtool.api.deps import get_offers_service
from jobtool.api.main import app
from jobtool.services.offers_service import OffersFilter, OffersService

URL = "https://exemple.fr/offres/manuelle-1"


@pytest.fixture
def service(tmp_db):
    return OffersService(tmp_db)


@pytest.fixture
def client(tmp_db):
    app.dependency_overrides[get_offers_service] = lambda: OffersService(tmp_db)
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- Service ----------------------------------------------------------------


def test_create_minimal_offer(service):
    """Titre + lien suffisent : entreprise, lieu et description restent vides."""
    result = service.create_manual_offer(titre="Ingénieur ML", url=URL)
    assert result.created
    job = result.job
    assert job.title == "Ingénieur ML" and job.url == URL
    assert job.source == "manual"
    assert job.company == "" and job.location == "" and job.description == ""
    assert job.status is JobStatus.new
    assert job.posted_date, (
        "posted_date = date du jour (l'offre est datée d'aujourd'hui)"
    )


def test_created_offer_is_readable_like_any_other(service):
    """L'offre manuelle est relue par les mêmes chemins que les offres collectées."""
    created = service.create_manual_offer(
        titre="Data Analyst",
        url=URL,
        description="Desc",
        entreprise="Acme",
        lieu="Lyon",
    ).job
    reread = service.get_offer(created.dedup_hash)
    assert reread is not None
    assert (reread.company, reread.location) == ("Acme", "Lyon")
    assert reread in service.list_offers()
    assert "manual" in service.available_sources()


def test_duplicate_url_is_reported_not_inserted(service):
    """Même lien (à query près) -> pas de 2e ligne, et on récupère l'offre existante."""
    first = service.create_manual_offer(titre="Poste", url=URL).job
    again = service.create_manual_offer(titre="Autre titre", url=URL + "?src=mail")
    assert again.created is False
    assert again.job.dedup_hash == first.dedup_hash, "on renvoie l'offre déjà connue"
    assert len(service.list_offers()) == 1


def test_manual_offer_supports_status_favorite_description(service):
    """Parité complète : statut, favori et description éditable fonctionnent."""
    h = service.create_manual_offer(titre="Poste", url=URL).job.dedup_hash
    assert service.update_status(h, JobStatus.applied)
    assert service.set_favorite(h, True)
    assert service.update_description(h, "Description collée après coup")
    job = service.get_offer(h)
    assert job.status is JobStatus.applied and job.favorite
    assert job.description == "Description collée après coup"
    assert job.applied_at, "une offre manuelle entre aussi dans l'historique"


def test_manual_offer_participates_in_sort(service):
    """Datée d'aujourd'hui, l'offre manuelle passe en tête du tri par publication."""
    service.create_manual_offer(titre="Ajoutée à la main", url=URL)
    jobs = service.list_offers(OffersFilter(order="posted_date"))
    assert jobs[0].title == "Ajoutée à la main"


# --- Route POST /offers -----------------------------------------------------


def test_post_creates_offer_201(client):
    r = client.post("/offers", json={"titre": "Ingénieur ML", "url": URL})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["titre"] == "Ingénieur ML" and body["source"] == "manual"
    assert body["url"] == URL and body["id"]
    # L'offre est immédiatement listée comme les autres.
    assert any(o["id"] == body["id"] for o in client.get("/offers").json())


def test_post_accepts_optional_fields(client):
    r = client.post(
        "/offers",
        json={
            "titre": "Data Analyst",
            "url": URL,
            "description": "Une description",
            "entreprise": "Acme",
            "lieu": "Lyon",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert (body["entreprise"], body["lieu"]) == ("Acme", "Lyon")
    assert body["description"] == "Une description"


def test_post_duplicate_url_is_409(client):
    assert client.post("/offers", json={"titre": "A", "url": URL}).status_code == 201
    r = client.post("/offers", json={"titre": "B", "url": URL + "?utm=x"})
    assert r.status_code == 409
    assert "déjà" in r.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"url": URL},  # titre manquant
        {"titre": "Sans lien"},  # url manquante
        {"titre": "", "url": URL},  # titre vide
        {"titre": "Titre", "url": ""},  # url vide
        {"titre": "Titre", "url": "pas-une-url"},  # url non exploitable
    ],
)
def test_post_rejects_invalid_payload_422(client, payload):
    """Titre et lien sont OBLIGATOIRES : sans lien on perdrait l'offre."""
    assert client.post("/offers", json=payload).status_code == 422


def test_post_then_patch_description(client):
    """La description reste éditable ensuite (chemin de la page « postuler »)."""
    offer_id = client.post("/offers", json={"titre": "Poste", "url": URL}).json()["id"]
    r = client.patch(
        f"/offers/{offer_id}/description",
        json={"description": "Collée depuis l'annonce"},
    )
    assert r.status_code == 200 and r.json()["description"] == "Collée depuis l'annonce"
