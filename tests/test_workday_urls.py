"""Preuve du parsing d'URL carrière Workday (cas réels de la cartographie : wd103, site≠tenant…)."""

from __future__ import annotations

import pytest

from jobtool.aggregator.workday.urls import (
    WorkdaySite,
    WorkdayUrlError,
    parse_careers_url,
)


def test_basic_url_tenant_wd_site_locale():
    site = parse_careers_url("https://kainos.wd3.myworkdayjobs.com/en-US/kainos")
    assert site == WorkdaySite("kainos", "wd3", "kainos", "en-US")
    assert site.base == "https://kainos.wd3.myworkdayjobs.com"
    assert site.cxs == "https://kainos.wd3.myworkdayjobs.com/wday/cxs/kainos/kainos"
    assert site.list_url() == site.cxs + "/jobs"


def test_locale_is_optional():
    # workday.wd5.../Workday : aucune locale dans l'URL.
    site = parse_careers_url("https://workday.wd5.myworkdayjobs.com/Workday")
    assert site.locale is None
    assert site.site == "Workday"


def test_site_differs_from_tenant_and_keeps_case():
    site = parse_careers_url(
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
    )
    assert site.tenant == "nvidia"
    assert site.site == "NVIDIAExternalCareerSite", "site sensible à la casse, ≠ tenant"


def test_multi_digit_data_center():
    # bdf = Banque de France sur wd103 ; cdpq sur wd10 -> regex wd\d+, jamais devinée.
    assert (
        parse_careers_url(
            "https://bdf.wd103.myworkdayjobs.com/fr-FR/recrutement-banque-de-france"
        ).wd_server
        == "wd103"
    )
    assert (
        parse_careers_url("https://cdpq.wd10.myworkdayjobs.com/fr-CA/CDPQ").wd_server
        == "wd10"
    )


def test_fallback_site_equals_tenant_when_path_empty():
    site = parse_careers_url("https://kainos.wd3.myworkdayjobs.com/")
    assert site.site == "kainos", "path vide -> site = tenant (fallback)"


def test_tenant_not_company_name():
    # airliquidehr ≠ « Air Liquide » : le tenant est le label du host, tel quel.
    site = parse_careers_url(
        "https://airliquidehr.wd3.myworkdayjobs.com/fr-FR/AirLiquideExternalCareer"
    )
    assert site.tenant == "airliquidehr"
    assert site.site == "AirLiquideExternalCareer"


def test_pasted_cxs_url_is_parsed():
    site = parse_careers_url(
        "https://kainos.wd3.myworkdayjobs.com/wday/cxs/kainos/kainos/jobs"
    )
    assert site.tenant == "kainos" and site.site == "kainos"


def test_detail_url_concatenates_external_path():
    site = parse_careers_url("https://kainos.wd3.myworkdayjobs.com/en-US/kainos")
    url = site.detail_url("/job/Melbourne/Workday-HCM-Consultant--C-_JR_17954")
    assert url == (
        "https://kainos.wd3.myworkdayjobs.com/wday/cxs/kainos/kainos"
        "/job/Melbourne/Workday-HCM-Consultant--C-_JR_17954"
    )


def test_non_workday_host_raises():
    with pytest.raises(WorkdayUrlError):
        parse_careers_url("https://careers.example.com/jobs")
    with pytest.raises(WorkdayUrlError):
        parse_careers_url(
            "https://kainos.myworkdayjobs.com/en-US/kainos"
        )  # wd<N> manquant
