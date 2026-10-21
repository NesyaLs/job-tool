"""Preuve du câblage des sources : loader config SmartRecruiters, registry, résolution CLI.

Aucun réseau : on vérifie seulement quelles sources sont construites et comment `--source`
(both/all) est déplié. Les identifiants sont forcés explicitement (override de .env).
"""

from __future__ import annotations

import textwrap

from jobtool.aggregator.config import (
    load_smartrecruiters_config,
    load_workday_config,
)
from jobtool.aggregator.models import SmartRecruitersConfig, WorkdayConfig
from jobtool.aggregator.registry import ALL_SOURCES, BuildContext, build_connectors
from jobtool.cli.aggregate import Source, _resolve_sources
from jobtool.settings import AggregatorSettings


# --- Loader de la section smartrecruiters: du config.yaml -------------------
def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_load_sr_config_list_and_filters(tmp_path):
    p = _write(
        tmp_path,
        """
        smartrecruiters:
          companies: [visa, airbus]
          country: us
          department: 1146555
        """,
    )
    cfg = load_smartrecruiters_config(p)
    assert cfg.companies == ["visa", "airbus"]
    assert cfg.country == "us"
    assert cfg.department == "1146555", "department ramené en str (ID numérique)"


def test_load_sr_config_companies_as_csv_string(tmp_path):
    p = _write(tmp_path, "smartrecruiters:\n  companies: 'visa, airbus'\n")
    assert load_smartrecruiters_config(p).companies == ["visa", "airbus"]


def test_load_sr_config_absent_section_is_empty(tmp_path):
    p = _write(tmp_path, "criteres:\n  motsCles: data\n")
    cfg = load_smartrecruiters_config(p)
    assert cfg.companies == [], "section absente -> vide (la source s'auto-isolera)"


def test_load_sr_config_missing_file_is_empty(tmp_path):
    cfg = load_smartrecruiters_config(tmp_path / "absent.yaml")
    assert cfg == SmartRecruitersConfig(), "fichier absent -> config vide, pas d'erreur"


# --- Loader de la section workday: du config.yaml --------------------------
def test_load_workday_config_list_and_csv(tmp_path):
    p = _write(
        tmp_path,
        """
        workday:
          sites:
            - https://kainos.wd3.myworkdayjobs.com/en-US/kainos
            - https://cdpq.wd10.myworkdayjobs.com/fr-CA/CDPQ
        """,
    )
    assert load_workday_config(p).sites == [
        "https://kainos.wd3.myworkdayjobs.com/en-US/kainos",
        "https://cdpq.wd10.myworkdayjobs.com/fr-CA/CDPQ",
    ]
    p2 = _write(
        tmp_path,
        "workday:\n  sites: 'https://a.wd1.myworkdayjobs.com/A, https://b.wd2.myworkdayjobs.com/B'\n",
    )
    assert load_workday_config(p2).sites == [
        "https://a.wd1.myworkdayjobs.com/A",
        "https://b.wd2.myworkdayjobs.com/B",
    ]


def test_load_workday_config_absent_is_empty(tmp_path):
    assert (
        load_workday_config(_write(tmp_path, "criteres:\n  motsCles: data\n")).sites
        == []
    )
    assert load_workday_config(tmp_path / "absent.yaml") == WorkdayConfig()


# --- Registry : quelles sources sont construites ----------------------------
def _ctx(*, ft=False, companies=(), workday_sites=()):
    settings = AggregatorSettings(
        france_travail_client_id="ID" if ft else None,
        france_travail_client_secret="SECRET" if ft else None,
        adzuna_app_id=None,
        adzuna_app_key=None,
    )
    return BuildContext(
        settings=settings,
        smartrecruiters=SmartRecruitersConfig(companies=list(companies)),
        workday=WorkdayConfig(sites=list(workday_sites)),
        debug=False,
    )


def test_registry_france_travail_needs_credentials():
    connectors, errors = build_connectors(["france_travail"], _ctx(ft=False))
    assert connectors == [] and any("France Travail" in e for e in errors)

    connectors, errors = build_connectors(["france_travail"], _ctx(ft=True))
    assert len(connectors) == 1 and errors == []
    assert connectors[0].source == "france_travail"


def test_registry_adzuna_and_sr_always_built_then_self_isolate():
    # Adzuna sans clés et SmartRecruiters sans entreprise sont quand même CONSTRUITS :
    # ils s'auto-isolent à l'exécution (notent l'erreur, renvoient []).
    connectors, errors = build_connectors(["adzuna", "smartrecruiters"], _ctx())
    assert [c.source for c in connectors] == ["adzuna", "smartrecruiters"]
    assert errors == []


def test_registry_workday_always_built_then_self_isolates():
    # Workday sans site configuré est quand même CONSTRUIT : il s'auto-isole à l'exécution.
    connectors, errors = build_connectors(["workday"], _ctx())
    assert [c.source for c in connectors] == ["workday"] and errors == []

    connectors, _ = build_connectors(
        ["workday"],
        _ctx(workday_sites=("https://kainos.wd3.myworkdayjobs.com/en-US/kainos",)),
    )
    assert connectors[0].source == "workday"


# --- Résolution CLI de --source (both / all / dédup) ------------------------
def test_resolve_both_is_ft_plus_adzuna():
    assert _resolve_sources([Source.both]) == ["france_travail", "adzuna"]


def test_resolve_all_is_every_source():
    assert _resolve_sources([Source.all]) == list(ALL_SOURCES)
    assert "smartrecruiters" in _resolve_sources([Source.all])


def test_resolve_dedups_preserving_order():
    resolved = _resolve_sources(
        [Source.france_travail, Source.both, Source.smartrecruiters]
    )
    assert resolved == ["france_travail", "adzuna", "smartrecruiters"]
