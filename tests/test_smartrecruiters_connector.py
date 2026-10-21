"""Preuve SANS réseau du connecteur SmartRecruiters (FakeSession partagée + fixtures JSON).

Prouve : API 2 étages (liste -> détail, N+1) ; mapping détail->RawJob->Job ; minimisation
(`creator` exclu) ; pagination par offset qui s'arrête ; garde-fou page vide ; détail en
échec -> offre IGNORÉE ; isolation (aucune entreprise, HTTP non-200, réseau) ; filtres exposés.
"""

from __future__ import annotations

import copy

import pytest
import requests

from jobtool.aggregator._http import RateLimiter
from jobtool.aggregator.models import SearchCriteria
from jobtool.aggregator.normalize import raw_to_job
from jobtool.aggregator.smartrecruiters.connector import (
    SmartRecruitersConnector,
    map_posting_to_rawjob,
)

NOOP = lambda _delay: None  # noqa: E731 — sleep injecté : aucun back-off réel en test


def _conn(session, companies=("acme",), **kwargs):
    """Connecteur câblé pour les tests : rate limiter et sleep neutralisés (aucune attente)."""
    return SmartRecruitersConnector(
        list(companies),
        session=session,
        rate_limiter=RateLimiter(max_per_second=1000, sleep=NOOP),
        sleep=NOOP,
        **kwargs,
    )


@pytest.fixture
def sr_list(load_fixture):
    return load_fixture("smartrecruiters_list.json")


@pytest.fixture
def sr_detail(load_fixture):
    return load_fixture("smartrecruiters_detail.json")


# --- Mapping + N+1 ----------------------------------------------------------
def test_two_stage_mapping_to_job(fake_session, sr_list, sr_detail):
    # liste (2 offres) puis 1 détail par offre -> 1 + 2 = 3 GET.
    session = fake_session([(200, sr_list), (200, sr_detail), (200, sr_detail)])
    conn = _conn(session)
    raws = conn.fetch(SearchCriteria(mots_cles="data", max_results=50))

    assert len(raws) == 2, "2 offres collectées"
    assert session.get_calls == 3, "N+1 : 1 liste + 2 détails"

    job = raw_to_job(raws[0])
    assert job.source == "smartrecruiters"
    assert job.native_id == "743999900000001", "native_id <- detail.id"
    assert job.title == "Senior Data Engineer"
    assert job.company == "Acme Corp", "company <- company.name"
    assert job.location == "Austin, TX, US", "location <- fullLocation"
    assert job.remote == "oui", "remote=true -> oui"
    assert job.contract_type == "Full-time", "<- typeOfEmployment.label"
    assert job.salary is None, "Posting API : pas de salaire"
    assert job.posted_date == "2026-06-10T09:00:00.000Z", "<- releasedDate"
    assert job.url == "https://careers.acme.com/jobs/743999900000001/apply", (
        "url <- applyUrl (prioritaire sur postingUrl)"
    )


def test_description_is_html_cleaned_and_skips_empty_sections(
    fake_session, sr_list, sr_detail
):
    session = fake_session([(200, sr_list), (200, sr_detail)])
    raws = _conn(session).fetch(SearchCriteria(max_results=1))
    desc = raws[0].description
    assert "<p>" not in desc and "<ul>" not in desc, "HTML retiré"
    assert "Acme builds R&D tooling." in desc, "entités décodées (&amp; -> &)"
    assert "- Build pipelines" in desc and "- Own quality" in desc, "puces des <li>"
    assert "Python & SQL required." in desc
    # additionalInformation est vide -> aucune ligne fantôme en fin de description.
    assert not desc.endswith("\n")


def test_raw_excludes_personal_data(fake_session, sr_list, sr_detail):
    session = fake_session([(200, sr_list), (200, sr_detail)])
    raws = _conn(session).fetch(SearchCriteria(max_results=1))
    raw_payload = raws[0].raw
    assert "creator" not in raw_payload, "donnée personnelle (creator) JAMAIS conservée"
    assert raw_payload["department"]["label"] == "Engineering"
    assert raw_payload["postingUrl"].endswith("/743999900000001")


# --- Filtres exposés --------------------------------------------------------
def test_list_params_expose_filters(fake_session):
    empty = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}
    session = fake_session([(200, empty)])
    conn = _conn(session, country="us", department="1146555")
    conn.fetch(
        SearchCriteria(
            mots_cles="engineer",
            min_creation_date="2026-06-01T00:00:00Z",
            max_results=50,
        )
    )
    p = session.params_history[
        0
    ]  # la liste filtrée (la suivante vérifie l'identifiant)
    assert p["q"] == "engineer", "q <- mots_cles"
    assert p["releasedAfter"] == "2026-06-01T00:00:00Z", (
        "releasedAfter <- min_creation_date"
    )
    assert p["country"] == "us" and p["department"] == "1146555", (
        "filtres SR de la config"
    )
    assert p["limit"] == 100 and p["offset"] == 0, "pagination par limit/offset"


def test_page_size_capped_at_100(fake_session):
    empty = {"content": [], "totalFound": 0}
    session = fake_session([(200, empty)])
    conn = _conn(session, page_size=500)
    conn.fetch(SearchCriteria(max_results=50))
    assert session.last_params["limit"] == 100, "limit plafonné à 100 (plafond API)"


# --- Pagination -------------------------------------------------------------
def test_pagination_advances_via_offset_then_stops(fake_session, sr_list, sr_detail):
    item1, item2 = sr_list["content"]
    detail1 = sr_detail
    detail2 = copy.deepcopy(sr_detail)
    detail2["id"] = "743999900000002"
    detail2["name"] = "Product Designer"

    page1 = {"offset": 0, "limit": 1, "totalFound": 2, "content": [item1]}
    page2 = {"offset": 1, "limit": 1, "totalFound": 2, "content": [item2]}
    session = fake_session([(200, page1), (200, detail1), (200, page2), (200, detail2)])
    conn = _conn(session, page_size=1)
    raws = conn.fetch(SearchCriteria(max_results=50))

    assert len(raws) == 2, "2 pages de 1 offre -> 2 offres"
    assert session.get_calls == 4, "2 listes + 2 détails ; arrêt à offset>=totalFound"
    assert {r.native_id for r in raws} == {"743999900000001", "743999900000002"}


def test_empty_content_stops_immediately(fake_session):
    # totalFound>0 mais content vide -> garde-fou anti-boucle : on s'arrête.
    session = fake_session([(200, {"content": [], "totalFound": 99})])
    raws = _conn(session).fetch(SearchCriteria(max_results=50))
    assert raws == [] and session.get_calls == 1, (
        "page vide -> stop, pas de boucle infinie"
    )


# --- Détail en échec -> offre ignorée --------------------------------------
def test_detail_failure_skips_offer(fake_session, sr_list, sr_detail):
    # 1er détail OK, 2e détail en 500 (réessayé puis abandonné) -> 1 seule offre conservée.
    session = fake_session([(200, sr_list), (200, sr_detail), (500, {"err": "boom"})])
    conn = _conn(session)
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert len(raws) == 1, "l'offre dont le détail échoue est IGNORÉE"
    assert raws[0].native_id == "743999900000001"
    assert any("500" in e for e in conn.errors), "échec du détail noté (HTTP 500)"


# --- Isolation --------------------------------------------------------------
def test_isolation_no_companies(fake_session):
    conn = _conn(fake_session([(200, {})]), companies=())
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert raws == [] and session_unused(conn), (
        "aucune entreprise -> liste vide, aucun GET"
    )
    assert len(conn.errors) == 1 and "aucune entreprise" in conn.errors[0]


def session_unused(conn) -> bool:
    return getattr(conn._session, "get_calls", 0) == 0


def test_isolation_list_http_error_without_exception(fake_session):
    session = fake_session(
        [(400, {"error": "bad request", "details": "limit invalide"})]
    )
    conn = _conn(session)
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert raws == [], "HTTP 400 -> liste vide (partiel), pas d'exception"
    assert len(conn.errors) == 1 and "400" in conn.errors[0], "erreur HTTP 400 notée"


def test_isolation_network_error(fake_session):
    session = fake_session([requests.ConnectionError("réseau coupé")])
    conn = _conn(session)
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert raws == [] and len(conn.errors) == 1, (
        "erreur réseau capturée -> partiel + notée"
    )


# --- Mapper unitaire (variantes non couvertes par les fixtures) -------------
def test_map_remote_variants():
    def remote(loc):
        return map_posting_to_rawjob({"location": loc}, "acme").remote

    assert remote({"remote": True}) == "oui"
    assert remote({"remote": False, "hybrid": True}) == "hybride"
    assert remote({"remote": False}) == "non"
    assert remote({}) == "inconnu", "champs absents -> inconnu (défensif)"


def test_location_fallback_without_fulllocation():
    raw = map_posting_to_rawjob({"location": {"city": "Lyon", "country": "fr"}}, "acme")
    assert raw.location == "Lyon, fr", (
        "compo city/region/country quand fullLocation absent"
    )


def test_company_fallback_to_identifier():
    raw = map_posting_to_rawjob({"company": {}}, "acme")
    assert raw.company == "acme", "company.name absent -> identifiant interrogé"


# --- Identifiant d'entreprise invalide --------------------------------------
# L'API répond 200 + liste vide pour un identifiant inexistant (constaté sur « Visa » et
# « Bosch », alors que « BoschGroup » renvoie des milliers d'offres) : sans vérification,
# une faute d'identifiant ressemble à une entreprise qui ne recrute pas.
EMPTY = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}


def test_empty_unfiltered_first_page_flags_invalid_identifier(fake_session):
    session = fake_session([(200, EMPTY)])
    conn = _conn(session, companies=("Bosch",))
    assert conn.fetch(SearchCriteria(max_results=50)) == []
    assert session.get_calls == 1, "sans filtre, la page vide suffit à conclure"
    assert len(conn.errors) == 1
    assert "Bosch" in conn.errors[0]
    assert "probablement invalide" in conn.errors[0]


def test_empty_filtered_page_is_checked_without_filter(fake_session):
    # Filtrée (pays) ET vide sans filtre -> identifiant suspect.
    session = fake_session([(200, EMPTY), (200, EMPTY)])
    conn = _conn(session, companies=("Visa",), country="fr")
    conn.fetch(SearchCriteria(max_results=50))
    assert session.get_calls == 2, "une vérification sans filtre"
    assert "country" not in session.params_history[1]
    assert any("probablement invalide" in e for e in conn.errors)


def test_empty_filtered_page_of_real_company_is_not_an_error(fake_session, sr_list):
    # Filtrée et vide, mais l'entreprise a des offres ailleurs : aucune erreur.
    session = fake_session([(200, EMPTY), (200, sr_list)])
    conn = _conn(session, companies=("acme",), country="fr")
    assert conn.fetch(SearchCriteria(max_results=50)) == []
    assert conn.errors == []


def test_max_per_company_caps_each_company(fake_session, sr_list, sr_detail):
    # 2 offres listées, plafond 1 par entreprise : 1 détail seulement par entreprise.
    other = copy.deepcopy(sr_detail)
    other["id"] = "743999900000009"
    session = fake_session(
        [(200, sr_list), (200, sr_detail), (200, sr_list), (200, other)]
    )
    conn = _conn(session, companies=("a", "b"), max_per_company=1)
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert [r.native_id for r in raws] == ["743999900000001", "743999900000009"]
    assert session.get_calls == 4
