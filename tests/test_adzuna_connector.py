"""Preuve SANS réseau du connecteur Adzuna (FakeSession, aucune dépendance de mock).

Prouve : fetch -> mapping JSON->RawJob->Job ; isolation (clés absentes, erreur HTTP,
erreur réseau -> liste vide sans lever, sans secret) ; pagination 1-indexée qui s'arrête.
"""

from __future__ import annotations

import pytest
import requests

from jobtool.aggregator.adzuna.connector import AdzunaConnector
from jobtool.aggregator.models import SearchCriteria
from jobtool.aggregator.normalize import raw_to_job


@pytest.fixture
def search(load_fixture):
    return load_fixture("adzuna_search.json")


def test_mapping_json_to_job(fake_session, search):
    session = fake_session([(200, search)])
    conn = AdzunaConnector("FAKE_ID", "FAKE_KEY", session=session, page_size=50)
    raws = conn.fetch(SearchCriteria(mots_cles="data", max_results=50))

    assert len(raws) == 3, f"3 offres mappées (vu {len(raws)})"
    assert session.get_calls == 1, (
        "3 résultats < page_size=50 -> pagination s'arrête (1 GET)"
    )
    assert "app_id" in session.last_params and "what" in session.last_params, (
        "params: app_id + what (national)"
    )
    assert (
        "where" not in session.last_params and "distance" not in session.last_params
    ), "national : NI where NI distance"

    jobs = [raw_to_job(r) for r in raws]
    j0 = jobs[0]
    assert j0.source == "adzuna"
    assert j0.native_id == "4012345678", "native_id <- id Adzuna"
    assert j0.title == "Data Scientist (H/F)"
    assert j0.company == "Globex", "company <- company.display_name"
    assert j0.location == "Paris, Île-de-France", "location <- location.display_name"
    assert j0.url == "https://www.adzuna.fr/details/4012345678", "url <- redirect_url"
    assert j0.posted_date == "2026-06-12T08:30:00Z", "posted_date <- created"
    assert "tronqué" in j0.description, "description <- snippet (tel quel)"


def test_salary_real_predicted_absent(fake_session, search):
    session = fake_session([(200, search)])
    conn = AdzunaConnector("FAKE_ID", "FAKE_KEY", session=session, page_size=50)
    jobs = [
        raw_to_job(r)
        for r in conn.fetch(SearchCriteria(mots_cles="data", max_results=50))
    ]

    j0, j1, j2 = jobs
    # Offre 0 : salaire RÉEL.
    assert (
        j0.salary is not None and j0.salary.min == 45000 and j0.salary.max == 55000
    ), "salaire réel mappé (45000-55000)"
    assert j0.salary.currency == "EUR" and j0.salary.period == "year", (
        "devise EUR, période year"
    )
    assert j0.raw.get("salary_is_predicted") == "0", (
        "salary_is_predicted='0' tracé dans raw"
    )
    # Offre 1 : salaire PRÉDIT -> Salary valide quand même.
    assert j1.salary is not None and j1.salary.min == 48000, (
        "salaire PRÉDIT -> Salary valide produit"
    )
    assert j1.raw.get("salary_is_predicted") == "1", (
        "salary_is_predicted='1' (prédit) tracé dans raw"
    )
    # Offre 2 : SANS salaire.
    assert j2.salary is None, "offre sans salaire -> salary = None"
    assert "latitude" in j2.raw and "category" in j2.raw, (
        "raw conserve les champs utiles (lat/long, category)"
    )


def test_isolation_missing_keys(fake_session, search):
    conn = AdzunaConnector(None, None, session=fake_session([(200, search)]))
    raws = conn.fetch(SearchCriteria(mots_cles="data", max_results=50))
    assert raws == [], "clés absentes -> liste vide (pas d'exception)"
    assert len(conn.errors) == 1 and "absentes" in conn.errors[0], (
        "erreur 'clés absentes' notée"
    )


def test_isolation_http_error_without_secret(fake_session):
    body_400 = {"exception": "AUTH_FAIL", "doc": "https://api.adzuna.com"}
    conn = AdzunaConnector(
        "FAKE_ID", "SUPER_SECRET_KEY", session=fake_session([(400, body_400)])
    )
    raws = conn.fetch(SearchCriteria(mots_cles="data", max_results=50))
    assert raws == [], "HTTP 400 -> liste vide (partiel), pas d'exception"
    assert len(conn.errors) == 1 and "400" in conn.errors[0], (
        "erreur HTTP 400 notée avec le corps"
    )
    assert (
        "SUPER_SECRET_KEY" not in conn.errors[0] and "app_key" not in conn.errors[0]
    ), "AUCUN secret dans le message d'erreur"


def test_isolation_network_error(fake_session):
    session = fake_session([requests.ConnectionError("réseau coupé")])
    conn = AdzunaConnector("FAKE_ID", "FAKE_KEY", session=session)
    raws = conn.fetch(SearchCriteria(mots_cles="data", max_results=50))
    assert raws == [] and len(conn.errors) == 1, (
        "erreur réseau capturée -> liste vide, notée"
    )


def test_pagination_advances_then_stops(fake_session, search):
    page1 = {"count": 3, "results": search["results"][:2]}  # pleine (page_size=2)
    page2 = {"count": 3, "results": search["results"][2:]}  # 1 résultat (< 2) -> stop
    session = fake_session([(200, page1), (200, page2)])
    conn = AdzunaConnector("FAKE_ID", "FAKE_KEY", session=session, page_size=2)
    raws = conn.fetch(SearchCriteria(mots_cles="data", max_results=50))
    assert len(raws) == 3, "pagination : 2+1 = 3 offres collectées"
    assert session.get_calls == 2, "2 pages interrogées puis arrêt (page 2 non pleine)"
