"""Preuve SANS réseau du connecteur Workday (FakeSession partagée GET/POST + fixtures JSON).

Prouve : API 2 étages (listing POST -> détail GET, N+1) ; mapping détail->RawJob->Job ;
`limit=20` + `searchText` dans le corps ; pagination par offset qui s'arrête ; `total` fiable
page 0 seulement (total=0 offset>0 ignoré) ; GARDE ANTI-CLAMP (page 100 % doublons -> stop, pas de
boucle infinie) ; détail en échec -> offre IGNORÉE ; isolation (aucun site, URL invalide, HTTP
422/500, réseau) ; mapping remote/fallback id.
"""

from __future__ import annotations

import requests

from jobtool.aggregator._http import RateLimiter
from jobtool.aggregator.models import SearchCriteria
from jobtool.aggregator.normalize import raw_to_job
from jobtool.aggregator.workday.connector import WorkdayConnector, map_posting_to_rawjob
from jobtool.aggregator.workday.urls import WorkdaySite, parse_careers_url

NOOP = lambda _delay: None  # noqa: E731 — sleep injecté : aucun back-off réel en test
KAINOS_URL = "https://kainos.wd3.myworkdayjobs.com/en-US/kainos"
SITE = parse_careers_url(KAINOS_URL)


def _conn(session, sites=(KAINOS_URL,), **kwargs):
    """Connecteur câblé pour les tests : rate limiter et sleep neutralisés (aucune attente)."""
    return WorkdayConnector(
        list(sites),
        session=session,
        rate_limiter=RateLimiter(max_per_second=1000, sleep=NOOP),
        sleep=NOOP,
        **kwargs,
    )


# --- Mapping + N+1 ----------------------------------------------------------
def test_two_stage_mapping_to_job(fake_session, load_fixture):
    wd_list = load_fixture("workday_list.json")
    detail = load_fixture("workday_detail.json")
    # 1 listing POST + 1 détail GET par offre (2) = 1 POST + 2 GET.
    session = fake_session([(200, wd_list), (200, detail), (200, detail)])
    raws = _conn(session).fetch(SearchCriteria(mots_cles="workday", max_results=50))

    assert len(raws) == 2, "2 offres collectées"
    assert session.post_calls == 1 and session.get_calls == 2, (
        "N+1 : 1 listing POST + 2 détails GET"
    )

    job = raw_to_job(raws[0])
    assert job.source == "workday"
    assert job.native_id == "JR_17954", "native_id <- jobPostingInfo.jobReqId"
    assert job.title == "Workday HCM Consultant (C)"
    assert job.company == "Kainos", "company <- hiringOrganization.name"
    assert job.location == "Melbourne, Sydney, Homeworker - Australia", (
        "location <- location + additionalLocations"
    )
    assert job.remote == "non", "remoteType=On-site -> non"
    # `timeType` est un temps de travail : il reste dans `raw`, jamais dans le contrat.
    assert job.contract_type == ""
    assert job.raw["timeType"] == "Full time"
    assert job.salary is None, "API CXS : pas de salaire"
    assert job.posted_date == "2026-06-23", "<- startDate ISO (pas postedOn relatif)"
    assert job.url == (
        "https://kainos.wd3.myworkdayjobs.com/en-US/kainos"
        "/job/Melbourne/Workday-HCM-Consultant--C-_JR_17954"
    ), "url <- externalUrl (vrai lien candidature, pas l'URL CXS)"


def test_list_body_has_limit20_and_searchtext(fake_session):
    session = fake_session([(200, {"total": 0, "jobPostings": []})])
    _conn(session).fetch(SearchCriteria(mots_cles="engineer", max_results=50))
    assert session.last_json == {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": "engineer",
    }, "corps POST : limit plafonné à 20, searchText <- mots_cles"


def test_description_is_html_cleaned(fake_session, load_fixture):
    session = fake_session(
        [
            (200, load_fixture("workday_list.json")),
            (200, load_fixture("workday_detail.json")),
        ]
    )
    raws = _conn(session).fetch(SearchCriteria(max_results=1))
    desc = raws[0].description
    assert "<p>" not in desc and "<ul>" not in desc, "HTML retiré"
    assert "Join Kainos & build Workday solutions." in desc, (
        "entités décodées (&amp; -> &)"
    )
    assert "- Configure HCM" in desc and "- Own delivery quality" in desc, (
        "puces des <li>"
    )


def test_raw_is_minimized_with_provenance(fake_session, load_fixture):
    session = fake_session(
        [
            (200, load_fixture("workday_list.json")),
            (200, load_fixture("workday_detail.json")),
        ]
    )
    raws = _conn(session).fetch(SearchCriteria(max_results=1))
    raw = raws[0].raw
    assert (
        raw["tenant"] == "kainos"
        and raw["site"] == "kainos"
        and raw["wd_server"] == "wd3"
    )
    assert raw["jobReqId"] == "JR_17954"
    assert raw["company"]["name"] == "Kainos"
    assert raw["externalUrl"].endswith("_JR_17954")


# --- Pagination -------------------------------------------------------------
def test_pagination_advances_via_offset_then_stops(fake_session, load_fixture):
    detail = load_fixture("workday_detail.json")
    post1 = {"externalPath": "/job/A/JR_1", "title": "A"}
    post2 = {"externalPath": "/job/B/JR_2", "title": "B"}
    page0 = {"total": 2, "jobPostings": [post1]}  # total fiable ici = 2
    page1 = {"total": 0, "jobPostings": [post2]}  # total=0 sur offset>0 : IGNORÉ
    session = fake_session([(200, page0), (200, detail), (200, page1), (200, detail)])
    raws = _conn(session).fetch(SearchCriteria(max_results=50))

    assert len(raws) == 2, (
        "2 pages de 1 offre -> 2 offres (total=0 offset>0 n'arrête pas)"
    )
    assert session.post_calls == 2 and session.get_calls == 2, "2 listings + 2 détails"
    assert session.last_json["offset"] == 1, (
        "2e listing demandé à offset=1 (avance par page)"
    )


def test_empty_page_stops_immediately(fake_session):
    session = fake_session([(200, {"total": 99, "jobPostings": []})])
    raws = _conn(session).fetch(SearchCriteria(max_results=50))
    assert raws == [] and session.post_calls == 1 and session.get_calls == 0, (
        "page vide -> stop, aucun détail"
    )


def test_clamp_guard_stops_on_all_duplicate_page(fake_session, load_fixture):
    detail = load_fixture("workday_detail.json")
    post1 = {"externalPath": "/job/A/JR_1", "title": "A"}
    post2 = {"externalPath": "/job/B/JR_2", "title": "B"}
    page = {"total": _total_capped(), "jobPostings": [post1, post2]}
    # offset hors fenêtre REBOUCLE sur la même page -> 2e listing = doublons -> garde anti-clamp.
    session = fake_session([(200, page), (200, detail), (200, detail), (200, page)])
    raws = _conn(session).fetch(SearchCriteria(max_results=50))

    assert len(raws) == 2, "2 offres distinctes ; la page rebouclée n'ajoute rien"
    assert session.post_calls == 2, "2e listing tenté puis arrêt (page 100 % doublons)"
    assert session.get_calls == 2, "aucun détail re-récupéré sur la page rebouclée"


def _total_capped() -> int:
    # `total` plafonné à 2000 : la fin de pagination ne peut PAS s'appuyer sur offset>=total ici,
    # seule la garde anti-clamp arrête la boucle.
    return 2000


# --- Détail en échec -> offre ignorée --------------------------------------
def test_detail_failure_skips_offer(fake_session, load_fixture):
    wd_list = load_fixture("workday_list.json")
    detail = load_fixture("workday_detail.json")
    # 1er détail OK ; 2e détail en 500 (réessayé puis abandonné) -> 1 seule offre conservée.
    session = fake_session([(200, wd_list), (200, detail), (500, {"err": "boom"})])
    conn = _conn(session)
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert len(raws) == 1, "l'offre dont le détail échoue est IGNORÉE"
    assert raws[0].native_id == "JR_17954"
    assert any("500" in e for e in conn.errors), "échec du détail noté (HTTP 500)"


# --- Isolation --------------------------------------------------------------
def test_isolation_no_sites(fake_session):
    conn = _conn(fake_session([(200, {})]), sites=())
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert raws == [] and conn._session.get_calls == 0 and conn._session.post_calls == 0
    assert len(conn.errors) == 1 and "aucun site" in conn.errors[0]


def test_isolation_invalid_url(fake_session):
    conn = _conn(fake_session([(200, {})]), sites=("https://careers.example.com/jobs",))
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert raws == [] and conn._session.post_calls == 0, (
        "URL non Workday -> aucun appel"
    )
    assert len(conn.errors) == 1 and "URL Workday ignorée" in conn.errors[0]


def test_isolation_listing_http_422(fake_session):
    # 422 = tenant inconnu / mauvais data center : TERMINAL (non réessayé) -> noté, partiel vide.
    session = fake_session([(422, {"errorCode": "HTTP_422"})])
    conn = _conn(session)
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert raws == [] and session.post_calls == 1, "422 non réessayé (terminal)"
    assert len(conn.errors) == 1 and "422" in conn.errors[0]


def test_isolation_network_error(fake_session):
    session = fake_session([requests.ConnectionError("réseau coupé")])
    conn = _conn(session)
    raws = conn.fetch(SearchCriteria(max_results=50))
    assert raws == [] and len(conn.errors) == 1, (
        "erreur réseau capturée -> partiel + notée"
    )


# --- Mappers unitaires (variantes non couvertes par les fixtures) -----------
def test_map_remote_variants():
    def remote(remote_type):
        return map_posting_to_rawjob(
            {"jobPostingInfo": {"remoteType": remote_type}}, SITE
        ).remote

    assert remote("Remote") == "oui"
    assert remote("Hybrid") == "hybride"
    assert remote("Flexible") == "hybride"
    assert remote("On-site") == "non"
    assert remote(None) == "inconnu", "remoteType absent -> inconnu (défensif)"


def test_native_id_fallback_to_external_path():
    # Détail sans jobReqId -> on retombe sur l'externalPath de la liste.
    raw = map_posting_to_rawjob(
        {"jobPostingInfo": {"title": "X"}},
        SITE,
        listing={"externalPath": "/job/Z/REQ999"},
    )
    assert raw.native_id == "/job/Z/REQ999"


def test_company_fallback_to_tenant():
    raw = map_posting_to_rawjob({"jobPostingInfo": {}}, SITE)
    assert raw.company == "kainos", "hiringOrganization absent -> repli sur le tenant"


def test_site_is_a_workdaysite():
    assert isinstance(SITE, WorkdaySite) and SITE.tenant == "kainos"


# --- Filtre pays (facet locationCountry, échantillon réel Thales) -----------
THALES_URL = "https://thales.wd3.myworkdayjobs.com/en-US/Careers"
FRANCE_ID = "54c5b6971ffb4bf0b116fe7651ec789a"


def test_country_filter_reads_facet_then_relists(fake_session, load_fixture):
    first = load_fixture("workday_list_thales_facets.json")
    filtered = {"total": 0, "jobPostings": [], "facets": []}
    session = fake_session([(200, first), (200, filtered)])
    conn = _conn(session, sites=(THALES_URL,), country="France")
    conn.fetch(SearchCriteria(max_results=5))
    assert session.post_calls == 2, "page 0 non filtrée, puis relance filtrée"
    assert session.last_json["appliedFacets"] == {"locationCountry": [FRANCE_ID]}
    assert conn.errors == []


def test_country_filter_absent_facet_lists_unfiltered(fake_session, load_fixture):
    # Site mono-pays (Banque de France) : pas de facet pays -> on garde la page 0.
    wd_list = load_fixture("workday_list.json")
    detail = load_fixture("workday_detail.json")
    session = fake_session([(200, wd_list), (200, detail), (200, detail)])
    raws = _conn(session, country="France").fetch(SearchCriteria(max_results=50))
    assert session.post_calls == 1
    assert len(raws) == 2


def test_bullet_fields_kept_for_contract_normalization(fake_session, load_fixture):
    wd_list = load_fixture("workday_list.json")
    detail = load_fixture("workday_detail.json")
    session = fake_session([(200, wd_list), (200, detail), (200, detail)])
    raws = _conn(session).fetch(SearchCriteria(max_results=1))
    assert raws[0].raw["bulletFields"] == ["JR_17954"]


def test_max_per_site(fake_session, load_fixture):
    wd_list = load_fixture("workday_list.json")
    detail = load_fixture("workday_detail.json")
    session = fake_session([(200, wd_list), (200, detail)])
    raws = _conn(session, max_per_site=1).fetch(SearchCriteria(max_results=50))
    assert len(raws) == 1 and session.get_calls == 1
