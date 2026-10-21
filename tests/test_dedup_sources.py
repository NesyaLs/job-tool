"""Dédoublonnage ENTRE SOURCES : chaque niveau, les refus de prudence, le report d'état.

Sans réseau. Les offres sont construites comme le pipeline les construit
(`RawJob` -> `raw_to_job`), et écrites par le VRAI stockage : c'est ce qui est écrit en
base qu'on vérifie, pas ce qu'une doublure aurait reçu (project_ctx.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from jobtool.aggregator import catalogue, storage
from jobtool.aggregator.dedup import (
    company_key,
    offer_url_key,
    title_key,
    title_similarity,
)
from jobtool.aggregator.models import JobStatus, RawJob
from jobtool.aggregator.normalize import raw_to_job
from jobtool.core.db import documents, offer_occurrences, offers

UID = storage.LOCAL_USER_ID
TODAY = datetime.now(UTC)


def _day(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _job(source, native_id, title, company, location, *, url="", raw=None, days=1):
    return raw_to_job(
        RawJob(
            source=source,
            native_id=native_id,
            title=title,
            company=company,
            location=location,
            url=url,
            posted_date=_day(days),
            description=f"{title} — texte de {source}.",
            raw=raw or {},
        )
    )


@pytest.fixture
def conn(tmp_db):
    c = storage.connect(tmp_db)
    yield c
    c.close()


def _outcomes(conn, jobs):
    result = catalogue.upsert_catalogue(conn, jobs)
    conn.commit()
    return result


def _count(conn, table):
    return len(conn.execute(select(table)).all())


# --- Clés de comparaison ---------------------------------------------------------
@pytest.mark.parametrize(
    "a, b",
    [
        ("Data Scientist (H/F)", "DATA SCIENTIST F/H"),
        ("Data Analyst - CDI", "Data Analyst (H/F/N)"),
        ("Alternant(e) Data Engineer - 12 mois", "Data Engineer en alternance"),
        ("Ingénieur.e Data - PARIS", "Ingénieur Data"),
    ],
)
def test_title_keys_ignore_gender_contract_duration_and_city(a, b):
    assert title_key(a, "Paris") == title_key(b, "Paris")


def test_company_key_drops_legal_forms_but_not_meaningful_words():
    assert company_key("ALTEN SA") == company_key("Alten") == "alten"
    assert company_key("Bosch Group") == company_key("BOSCH") == "bosch"
    assert company_key("Air France") == "air france", "« france » n'est pas retiré"
    assert company_key("") == ""


@pytest.mark.parametrize(
    "a, b",
    [
        (
            "https://jobs.smartrecruiters.com/BoschGroup/744000150141246-lead?oga=true",
            "https://jobs.smartrecruiters.com/BoschGroup/744000150141246-lead",
        ),
        (
            "https://thales.wd3.myworkdayjobs.com/en-US/Careers/job/Toulouse/E2_R0339415",
            "https://thales.wd3.myworkdayjobs.com/Careers/job/Toulouse/E2-Manager_R0339415",
        ),
        (
            "https://www.adzuna.fr/land/ad/5876631143?se=1&utm_medium=api&v=1B",
            "https://www.adzuna.fr/details/5876631143",
        ),
        (
            "https://www.example.com/jobs/42?utm_source=li&ref=x",
            "https://example.com/jobs/42/",
        ),
    ],
)
def test_url_keys_drop_tracking_and_resolve_known_links(a, b):
    assert offer_url_key(a) == offer_url_key(b) != ""


def test_similarity_is_order_insensitive_and_strict_on_extra_words():
    assert title_similarity("data scientist senior", "senior data scientist") == 1.0
    assert title_similarity("data scientist", "data scientist senior") < 0.9


# --- Niveau 1 : même source, même identifiant ----------------------------------------
def test_level1_same_native_id_updates_the_occurrence(conn):
    first = _job("france_travail", "FT1", "Data scientist", "Acme", "75 - Paris")
    again = _job(
        "france_travail", "FT1", "Data scientist confirmé", "Acme", "75 - Paris"
    )
    (a,) = _outcomes(conn, [first])
    (b,) = _outcomes(conn, [again])
    assert (a.level, b.level) == ("nouvelle", "identifiant")
    assert a.offer_id == b.offer_id
    assert _count(conn, offers) == 1 and _count(conn, offer_occurrences) == 1
    title = conn.execute(select(offers.c.title)).scalar()
    assert title == "Data scientist confirmé", "le contenu suit la source"


# --- Niveau 2 : même lien ---------------------------------------------------------------
SR_URL = "https://jobs.smartrecruiters.com/BoschGroup/744000150141246-lead-h-f"


def test_level2_france_travail_partner_link_to_the_ats(conn):
    """France Travail relaie l'annonce d'un partenaire dont le lien est celui de l'ATS :
    même annonce, même si FT n'a pas le nom de l'entreprise."""
    ats = _job(
        "smartrecruiters",
        "744000150141246",
        "Marketing Lead (H/F)",
        "Bosch Group",
        "Saint-Ouen-sur-Seine, IDF, France",
        url=SR_URL + "?oga=true",
        raw={"postingUrl": SR_URL},
    )
    ft = _job(
        "france_travail",
        "7000001",
        "Marketing Lead",
        "",  # entreprise absente, fréquent chez France Travail
        "93 - Saint-Ouen-sur-Seine",
        url="https://candidat.francetravail.fr/offres/recherche/detail/7000001",
        raw={"partenaires": [{"nom": "SMARTRECRUITERS", "url": SR_URL}]},
    )
    first, second = _outcomes(conn, [ft, ats])
    assert second.level == "url" and second.offer_id == first.offer_id
    row = conn.execute(select(offers.c.company, offers.c.url, offers.c.source)).one()
    assert row.company == "Bosch Group", "l'entreprise vient de l'ATS"
    assert row.source == "smartrecruiters", "l'ATS est la source prioritaire"
    assert row.url.startswith(SR_URL), "lien de candidature direct"
    links = catalogue.occurrences_for(conn, [first.offer_id])[first.offer_id]
    assert {o["source"] for o in links} == {"smartrecruiters", "france_travail"}
    assert any("francetravail" in o["url"] for o in links), "tous les liens conservés"


def test_url_fragment_routes_identify_the_offer():
    """Cas réel (BEETWEEN via France Travail) : l'annonce est dans le fragment."""
    a = "https://app.beetween.com/WeaselWeb/p/#/apply/job/u7vl5jk0zr8/data-analyst-power-bi"
    b = "https://app.beetween.com/WeaselWeb/p/#/apply/job/g8klv8p1xs8/data-analyst-banque"
    assert offer_url_key(a) != offer_url_key(b)
    assert offer_url_key("https://x.fr/offre/1#description") == "x.fr/offre/1"


def test_level2_generic_link_needs_a_near_identical_title(conn):
    """Cas réel : deux annonces d'un même diffuseur, intitulés voisins mais postes
    différents (similarité 0,69). Un lien générique ne suffit pas à les fusionner."""
    shared = "https://www.diffuseur.fr/carrieres"
    a = _job(
        "france_travail",
        "A",
        "Data Analyst - Power BI H/F",
        "",
        "75 - Paris",
        url=shared,
    )
    b = _job(
        "france_travail",
        "B",
        "Data Analyst Banque expérimenté (H/F)",
        "",
        "75 - Paris",
        url=shared,
    )
    same = _job(
        "france_travail",
        "C",
        "Data Analyst - Power BI (F/H)",
        "",
        "75 - Paris",
        url=shared,
    )
    first, second, third = _outcomes(conn, [a, b, same])
    assert second.offer_id != first.offer_id
    assert third.offer_id == first.offer_id and third.level == "url"


def test_level2_generic_link_with_different_titles_is_not_enough(conn):
    careers = "https://www.acme.fr/carrieres"
    a = _job("france_travail", "A", "Comptable", "Acme", "Lyon", url=careers)
    b = _job("adzuna", "B", "Data scientist", "Acme", "Lyon", url=careers)
    first, second = _outcomes(conn, [a, b])
    assert second.level == "nouvelle" and second.offer_id != first.offer_id


# --- Niveau 3 : empreinte approchée -------------------------------------------------------
def _ats_and_board(ats_kwargs=None, board_kwargs=None):
    ats = dict(
        source="workday",
        native_id="R1",
        title="Data Scientist (H/F)",
        company="Thales SA",
        location="Élancourt",
        raw={"bulletFields": ["CDI"]},  # puce Workday : contrat connu
        days=2,
    )
    board = dict(
        source="adzuna",
        native_id="Z1",
        title="DATA SCIENTIST F/H - CDI",
        company="THALES",
        location="Élancourt, Yvelines",
        days=1,
    )
    ats.update(ats_kwargs or {})
    board.update(board_kwargs or {})
    return _job(**ats), _job(**board)


def test_level3_same_company_place_and_title_merges_with_trace(conn):
    ats, board = _ats_and_board()
    first, second = _outcomes(conn, [ats, board])
    assert second.level == "approchee" and second.offer_id == first.offer_id
    assert second.score == 1.0
    occ = conn.execute(
        select(offer_occurrences.c.match_level, offer_occurrences.c.match_score).where(
            offer_occurrences.c.source == "adzuna"
        )
    ).one()
    assert tuple(occ) == ("approchee", 1.0), "décision tracée : niveau et score"


@pytest.mark.parametrize(
    "board_change, why",
    [
        ({"company": ""}, "entreprise vide : jamais de fusion approchée"),
        ({"location": "Lyon"}, "même intitulé, autre ville"),
        ({"title": "Stage - Data Scientist"}, "stage contre CDI (contrats connus)"),
        ({"days": 60}, "publiées à deux mois d'écart"),
        ({"title": "Data Scientist Senior"}, "intitulé sous le seuil"),
        ({"source": "workday", "native_id": "R2"}, "deux annonces d'une même source"),
    ],
)
def test_level3_prudence_refusals(conn, board_change, why):
    ats, board = _ats_and_board(board_kwargs=board_change)
    first, second = _outcomes(conn, [ats, board])
    assert second.offer_id != first.offer_id, why
    assert second.level == "nouvelle", why


def test_empty_company_same_title_and_city_on_two_boards_stays_apart(conn):
    """Le cas France Travail : entreprise vide, intitulé et lieu seuls ne suffisent pas."""
    a = _job("france_travail", "A", "Data scientist", "", "75 - Paris")
    b = _job("adzuna", "B", "Data scientist", "", "Paris, Ile-de-France")
    first, second = _outcomes(conn, [a, b])
    assert first.offer_id != second.offer_id


def test_decisions_hold_between_offers_of_the_same_run_and_across_runs(conn):
    ats, board = _ats_and_board()
    together = _outcomes(conn, [ats, board])
    again = _outcomes(conn, [board, ats])
    assert {o.offer_id for o in together + again} == {together[0].offer_id}
    assert _count(conn, offers) == 1 and _count(conn, offer_occurrences) == 2


# --- Offre canonique ------------------------------------------------------------------------
def test_canonical_offer_takes_best_fields_by_source_priority(conn):
    ft = _job(
        "france_travail",
        "F",
        "Data Scientist",
        "Thales",
        "78 - Élancourt",
        days=5,
        url="https://candidat.francetravail.fr/offres/recherche/detail/F",
    )
    ats, _ = _ats_and_board()
    _outcomes(conn, [ft, ats])
    row = conn.execute(select(offers)).one()
    assert row.source == "workday", "l'ATS l'emporte"
    assert row.title == "Data Scientist (H/F)"
    assert row.posted_date == ft.posted_date, "première parution conservée"
    assert row.department == "78"


# --- Fusion d'offres existantes : état utilisateur reporté ------------------------------------
def _two_offers_then_link(conn):
    """Deux offres distinctes au catalogue, puis la preuve qu'elles n'en font qu'une."""
    ft = _job(
        "france_travail",
        "F9",
        "Analyste FX",
        "",
        "75 - Paris",
        url="https://candidat.francetravail.fr/offres/recherche/detail/F9",
    )
    wd = _job(
        "workday",
        "JR9",
        "Analyste FX (H/F)",
        "Banque de France",
        "PARIS",
        url="https://bdf.wd103.myworkdayjobs.com/x/job/PARIS/Analyste-FX_JR9",
    )
    storage.upsert_jobs(conn, UID, [ft, wd])
    ids = conn.execute(select(offers.c.dedup_hash, offers.c.source)).all()
    by_source = {r.source: r.dedup_hash for r in ids}
    return by_source["france_travail"], by_source["workday"]


def test_merge_carries_user_state_without_loss(conn):
    ft_id, wd_id = _two_offers_then_link(conn)
    storage.set_favorite(conn, UID, ft_id, True)
    storage.set_description(conn, UID, ft_id, "Ma description corrigée")
    storage.set_status(conn, UID, wd_id, JobStatus.applied)
    conn.execute(
        documents.insert().values(
            doc_id="d1",
            user_id=UID,
            offer_id=ft_id,
            kind="cv",
            template="t",
            render_input={},
            original_texts={},
            overrides={},
            created_at="x",
            updated_at="x",
        )
    )
    conn.commit()

    merged = catalogue.merge_offers(
        conn, [catalogue.Merge(drop=ft_id, keep=wd_id, level="url")]
    )
    conn.commit()
    assert merged == 1
    job = storage.get_job(conn, UID, wd_id)
    assert job.favorite is True, "le favori de l'offre absorbée est reporté"
    assert job.status is JobStatus.applied, "le statut le plus engagé est gardé"
    assert job.applied_at is not None
    assert job.description == "Ma description corrigée"
    # L'ancien identifiant reste valide : il désigne l'offre qui l'a absorbée.
    assert storage.get_job(conn, UID, ft_id).dedup_hash == wd_id
    assert catalogue.resolve_offer_id(conn, ft_id) == wd_id
    doc_offer = conn.execute(select(documents.c.offer_id)).scalar()
    assert doc_offer == wd_id, "les documents suivent l'offre"
    assert {o["source"] for o in catalogue.occurrences_for(conn, [wd_id])[wd_id]} == {
        "france_travail",
        "workday",
    }


def test_merge_keeps_hidden_state_and_combines_both_sides(conn):
    ft_id, wd_id = _two_offers_then_link(conn)
    storage.set_status(conn, UID, ft_id, JobStatus.seen)  # masquée d'un côté
    storage.set_favorite(conn, UID, wd_id, True)  # favorite de l'autre
    catalogue.merge_offers(conn, [catalogue.Merge(ft_id, wd_id, "url")])
    conn.commit()
    job = storage.get_job(conn, UID, wd_id)
    assert job.favorite is True and job.status is JobStatus.seen


def test_deduplicate_catalogue_merges_existing_duplicates(conn):
    ats, board = _ats_and_board()
    # Écrites SANS la règle approchée (seuil inatteignable), comme avant le dédoublonnage.
    import jobtool.aggregator.dedup as dedup

    saved = dedup.FUZZY_THRESHOLD
    dedup.FUZZY_THRESHOLD = 2.0
    try:
        storage.upsert_jobs(conn, UID, [ats, board])
    finally:
        dedup.FUZZY_THRESHOLD = saved
    assert _count(conn, offers) == 2

    decided = catalogue.deduplicate_catalogue(conn)
    conn.commit()
    assert [(m.level, m.score) for m in decided] == [("approchee", 1.0)]
    assert _count(conn, offers) == 1
    assert storage.count_jobs(conn, UID) == 1, "une seule offre dans la vue du compte"


def test_probable_duplicates_lists_pairs_under_the_threshold(conn):
    ats, board = _ats_and_board(board_kwargs={"title": "Data Scientist Senior"})
    _outcomes(conn, [ats, board])
    pairs = catalogue.probable_duplicates(conn)
    assert len(pairs) == 1
    pair, a, b = pairs[0]
    assert 0.75 <= pair.score < 0.90
    assert {a["title"], b["title"]} == {"Data Scientist (H/F)", "Data Scientist Senior"}


def test_private_manual_offer_is_never_a_candidate_for_the_catalogue(conn):
    manual = _job("manual", "u1:x", "Data Scientist (H/F)", "Thales", "Élancourt")
    storage.upsert_jobs(conn, UID, [manual], private=True)
    ats, _ = _ats_and_board()
    (outcome,) = _outcomes(conn, [ats])
    assert outcome.level == "nouvelle", "une collecte ne rejoint pas une offre privée"
