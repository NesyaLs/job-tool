"""Normalisation du lieu et du contrat, par source, sur des réponses RÉELLES des API.

`tests/fixtures/normalization_samples.json` contient des offres telles que les API les
ont renvoyées (descriptions et contacts retirés). Les valeurs attendues ont été vérifiées
à la main. Sans réseau, déterministe, aucun LLM.
"""

from __future__ import annotations

import pytest

from jobtool.aggregator import storage
from jobtool.aggregator.adzuna.connector import map_offer_to_rawjob as adzuna
from jobtool.aggregator.contracts import normalize_contract, normalize_work_time
from jobtool.aggregator.francetravail.connector import map_offer_to_rawjob as ft
from jobtool.aggregator.geo import normalize_location, parse_free_text
from jobtool.aggregator.models import RawJob
from jobtool.aggregator.normalize import raw_to_job
from jobtool.aggregator.smartrecruiters.connector import map_posting_to_rawjob as sr
from jobtool.aggregator.workday.connector import map_posting_to_rawjob as wd
from jobtool.aggregator.workday.urls import WorkdaySite


@pytest.fixture(scope="module")
def samples():
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "normalization_samples.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _by(items, key, value):
    return next(i for i in items if str(i[key]) == value)


# --- France Travail : code INSEE de la commune, codes de contrat ---------------
@pytest.mark.parametrize(
    "native_id, dept, city, kind, work_time",
    [
        ("214CRWD", "92", "Courbevoie", "cdi", "temps_plein"),
        ("213ZDYN", "75", "Paris", "interim", "temps_plein"),  # « Paris 14e Arr. »
        ("213QWNF", "92", "Asnières-sur-Seine", "alternance", "temps_plein"),
        ("7030425", "75", "Paris", "freelance", None),  # LIB = profession libérale
        # Intitulé « Stage / Alternance », contrat de professionnalisation.
        ("6936562", "92", "Nanterre", "alternance", None),
        # « hors stage et alternance » : négation, c'est le CDI de la source.
        ("6825250", "75", "Paris", "cdi", None),
        # Intitulé « Stage - … » alors que la source dit CDI : l'intitulé l'emporte.
        ("6676569", "50", "Baudre", "stage", None),
        ("213ZBCQ", "973", "Cayenne", "alternance", "temps_plein"),  # outre-mer
    ],
)
def test_france_travail(samples, native_id, dept, city, kind, work_time):
    job = raw_to_job(ft(_by(samples["france_travail"], "id", native_id)))
    assert (job.department, job.city, job.contract_kind, job.work_time) == (
        dept,
        city,
        kind,
        work_time,
    )
    assert job.country == "FR"


def test_france_travail_region_is_derived_from_department(samples):
    job = raw_to_job(ft(_by(samples["france_travail"], "id", "214CRWD")))
    assert job.region == "Île-de-France"
    assert job.place_precision == "commune"


# --- Adzuna : hiérarchie `area` ------------------------------------------------
@pytest.mark.parametrize(
    "native_id, dept, city, precision",
    [
        ("5876631143", "63", None, "departement"),  # [France, AURA, Puy-de-Dôme]
        ("5874912466", "69", "Lyon", "commune"),
        ("5880885848", "75", "Paris", "commune"),  # « Paris » = département ET ville
        ("5878324514", "974", None, "departement"),  # [France, DOM-TOM, La Réunion]
        (
            "5813726905",
            "92",
            "Courbevoie",
            "commune",
        ),  # [.., Hauts-de-Seine, Nanterre, ..]
    ],
)
def test_adzuna(samples, native_id, dept, city, precision):
    job = raw_to_job(adzuna(_by(samples["adzuna"], "id", native_id)))
    assert (job.department, job.city, job.place_precision) == (dept, city, precision)
    # Adzuna ne dit pas le contrat sur ces offres : rien n'est inventé.
    assert job.contract_kind == "inconnu"


# --- SmartRecruiters : code postal, `typeOfEmployment` peu fiable --------------
@pytest.mark.parametrize(
    "native_id, dept, city, kind",
    [
        ("744000150141246", "93", "Saint-Ouen-sur-Seine", "cdi"),  # id « permanent »
        # id « permanent », mais l'intitulé dit « Apprenti » : c'est une alternance.
        ("744000150108490", "93", "Drancy", "alternance"),
        # id « intern », intitulé « Apprenti(e) » : l'intitulé l'emporte aussi.
        ("744000149873136", "93", "Drancy", "alternance"),
        ("744000150424089", "92", "Levallois-Perret", "stage"),  # « Stage – … »
    ],
)
def test_smartrecruiters(samples, native_id, dept, city, kind):
    job = raw_to_job(sr(_by(samples["smartrecruiters"], "id", native_id), "x"))
    assert (job.department, job.city, job.contract_kind) == (dept, city, kind)
    assert job.region == "Île-de-France"


def test_smartrecruiters_full_time_is_work_time_not_contract(samples):
    job = raw_to_job(sr(_by(samples["smartrecruiters"], "id", "744000150141246"), "x"))
    assert job.work_time == "temps_plein"


# --- Workday : ville en toutes lettres, contrat dans les puces -----------------
def _wd_jobs(samples):
    for item in samples["workday"]:
        site = WorkdaySite(item["tenant"], item["wd_server"], item["site"])
        yield raw_to_job(wd(item["detail"], site, item["listing"]))


@pytest.mark.parametrize(
    "native_id, dept, city, kind, work_time",
    [
        ("JR101169", "75", "Paris", "cdi", None),  # « PARIS », puce « CDI »
        ("JR101215", "75", "Paris", "stage", None),  # puce « Stage »
        ("R0316316", "78", "Élancourt", "cdi", "temps_plein"),  # « Regular Employee »
        ("R0339415", "31", "Toulouse", "cdi", "temps_plein"),
    ],
)
def test_workday(samples, native_id, dept, city, kind, work_time):
    job = next(j for j in _wd_jobs(samples) if j.native_id == native_id)
    assert (job.department, job.city, job.contract_kind, job.work_time) == (
        dept,
        city,
        kind,
        work_time,
    )
    assert job.contract_type == "", "timeType n'est pas un contrat"


def test_workday_foreign_country_is_kept_without_department():
    place = normalize_location(
        "workday", "Belfast", {"location": "Belfast", "country": {"descriptor": "UK"}}
    )
    assert (place.country, place.department, place.precision) == ("GB", None, "pays")


# --- Texte libre (saisie manuelle, replis) --------------------------------------
@pytest.mark.parametrize(
    "text, country, dept, city, precision",
    [
        ("Lyon (69)", "FR", "69", "Lyon", "commune"),
        ("69003 Lyon", "FR", "69", "Lyon", "commune"),
        ("20000 Ajaccio", "FR", "2A", "Ajaccio", "commune"),  # la Corse, par le CP
        ("78 - Mureaux", "FR", "78", "Les Mureaux", "commune"),  # article omis par FT
        ("95 - Val-d'Oise", "FR", "95", None, "departement"),
        ("Île-de-France", "FR", None, None, "region"),
        ("99 - Belgique", "BE", None, None, "pays"),
        ("Télétravail", None, None, None, "inconnu"),
        ("", None, None, None, "inconnu"),
    ],
)
def test_free_text(text, country, dept, city, precision):
    place = parse_free_text(text)
    assert (place.country, place.department, place.city, place.precision) == (
        country,
        dept,
        city,
        precision,
    )


def test_homonym_takes_most_populous_and_says_so():
    # Saint-Denis : Aude, Gard, Seine-Saint-Denis, La Réunion.
    place = parse_free_text("Saint-Denis")
    assert place.department == "974"
    assert place.precision == "commune_homonyme"
    # Un indice lève l'ambiguïté.
    assert parse_free_text("93 - Saint-Denis").department == "93"
    assert parse_free_text("93200 Saint-Denis").precision == "commune"


# --- Contrat : cas de règles -----------------------------------------------------
@pytest.mark.parametrize(
    "source, contract_type, title, raw, expected",
    [
        (
            "adzuna",
            "permanent / full_time",
            "Dev",
            {"contract_type": "permanent"},
            "cdi",
        ),
        ("adzuna", "contract", "Dev", {"contract_type": "contract"}, "cdd"),
        ("manual", "", "Contract manager", {}, "inconnu"),  # pas un CDD
        ("manual", "", "Data scientist - CDI", {}, "cdi"),
        ("manual", "", "Stage de fin d'études / Alternance", {}, "alternance"),
        ("manual", "", "Robot Learning Engineering Intern", {}, "stage"),
        ("manual", "", "Internal Communication Officer", {}, "inconnu"),
        ("workday", "", "QA", {"bulletFields": ["Fixed Term Employee"]}, "cdd"),
    ],
)
def test_contract_rules(source, contract_type, title, raw, expected):
    assert normalize_contract(source, contract_type, title, raw) == expected


def test_work_time_never_implies_a_contract():
    raw = {"timeType": "Part time"}
    assert normalize_work_time("workday", "", raw) == "temps_partiel"
    assert normalize_contract("workday", "", "Analyste", raw) == "inconnu"


def test_description_is_never_read_for_the_contract():
    job = raw_to_job(
        RawJob(
            source="manual",
            native_id="x",
            title="Analyste",
            description="Après un stage, vous rejoindrez l'équipe en CDI.",
        )
    )
    assert job.contract_kind == "inconnu"


# --- Offres déjà en base -----------------------------------------------------------
def test_renormalize_catalogue_backfills_old_rows(tmp_db):
    from sqlalchemy import select, update

    from jobtool.core.db import offer_occurrences, offers

    conn = storage.connect(tmp_db)
    try:
        job = raw_to_job(
            RawJob(source="manual", native_id="x", title="Stage data", location="Lyon")
        )
        storage.upsert_job(conn, storage.LOCAL_USER_ID, job)
        # Simule une ligne écrite avant les champs normalisés.
        for table in (offers, offer_occurrences):
            conn.execute(
                update(table).values(
                    department=None, contract_kind="inconnu", norm_version=0
                )
            )
        conn.commit()
        assert storage.renormalize_catalogue(conn) == 1
        row = conn.execute(select(offers.c.department, offers.c.contract_kind)).one()
        assert tuple(row) == ("69", "stage")
        assert storage.renormalize_catalogue(conn) == 0, "idempotent"
    finally:
        conn.close()


def test_search_filters_on_normalized_fields(tmp_db):
    conn = storage.connect(tmp_db)
    try:
        for i, (title, loc) in enumerate(
            [
                ("Stage data", "Lyon (69)"),
                ("Data scientist - CDI", "75 - Paris"),
                ("Data analyst CDI", ""),  # lieu inexploitable
            ]
        ):
            job = raw_to_job(
                RawJob(source="manual", native_id=str(i), title=title, location=loc)
            )
            storage.upsert_job(conn, storage.LOCAL_USER_ID, job)
        uid = storage.LOCAL_USER_ID

        def titles(**kw):
            query = storage.JobQuery(**kw)
            return sorted(j.title for j in storage.list_jobs(conn, uid, query))

        assert titles(departments=("69",)) == ["Stage data"]
        assert titles(departments=("69",), include_unknown_location=True) == [
            "Data analyst CDI",
            "Stage data",
        ]
        assert titles(contracts=("cdi",)) == [
            "Data analyst CDI",
            "Data scientist - CDI",
        ]
        assert titles(country="FR") == ["Data scientist - CDI", "Stage data"]
    finally:
        conn.close()
