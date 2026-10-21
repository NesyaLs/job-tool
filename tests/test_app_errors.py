"""Point 0 de l'audit : plus AUCUN SystemExit dans le domaine/core.

`load_base` et les loaders de config lèvent des exceptions APPLICATIVES héritant
d'Exception (`BaseLoadError`, `ConfigError`) : elles sont attrapables par les
`except Exception` de l'API (plus de 500 non géré) et traduites en sortie propre
côté CLI (typer.Exit code 1) et côté API (HTTPException).
"""

from __future__ import annotations

import pytest

from jobtool.aggregator.config import (
    ConfigError,
    load_criteria,
    load_smartrecruiters_config,
    load_workday_config,
)
from jobtool.core.experience_base.loader import BaseLoadError, load_base

MALFORMED_YAML = "criteres: [non fermé\n"


# --- Hiérarchie : des Exception ordinaires, jamais des BaseException-only ----


def test_errors_are_plain_exceptions():
    """Attrapables par `except Exception` (API) — ne traversent plus rien."""
    for exc_type in (BaseLoadError, ConfigError):
        assert issubclass(exc_type, Exception)
        assert not issubclass(exc_type, SystemExit)


# --- load_base (core/experience_base/loader.py) ------------------------------


def test_load_base_missing_file(tmp_path):
    with pytest.raises(BaseLoadError, match="introuvable"):
        load_base(tmp_path / "absent.yaml")


def test_load_base_malformed_yaml(tmp_path):
    p = tmp_path / "base.yaml"
    p.write_text(MALFORMED_YAML, encoding="utf-8")
    with pytest.raises(BaseLoadError, match="mal formé"):
        load_base(p)


def test_load_base_invalid_data_lists_problems(tmp_path):
    p = tmp_path / "base.yaml"
    p.write_text("profile: 42\n", encoding="utf-8")
    with pytest.raises(BaseLoadError, match="invalide"):
        load_base(p)


# --- Loaders de config (aggregator/config.py) --------------------------------


def test_load_criteria_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="introuvable"):
        load_criteria(tmp_path / "absent.yaml")


def test_load_criteria_malformed_yaml(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(MALFORMED_YAML, encoding="utf-8")
    with pytest.raises(ConfigError, match="mal formée"):
        load_criteria(p)


def test_load_criteria_invalid_values(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("criteres:\n  max_results: pas_un_nombre\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalides"):
        load_criteria(p)


def test_load_source_sections_malformed_yaml(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(MALFORMED_YAML, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_smartrecruiters_config(p)
    with pytest.raises(ConfigError):
        load_workday_config(p)


# --- Traduction côté API -----------------------------------------------------


def test_api_aggregate_config_error_is_clean_500(tmp_path, monkeypatch):
    """Config YAML cassée -> HTTPException propre (detail lisible), pas un 500 brut."""
    from fastapi.testclient import TestClient

    from jobtool.api.main import app

    p = tmp_path / "config.yaml"
    p.write_text(MALFORMED_YAML, encoding="utf-8")
    monkeypatch.setenv("JOBTOOL_AGG_CONFIG", str(p))

    client = TestClient(app)
    r = client.post("/aggregate", json={"sources": ["adzuna"]})
    assert r.status_code == 500
    assert "mal formée" in r.json()["detail"]


def test_api_generator_base_error_is_captured(tmp_path, monkeypatch):
    """Base introuvable -> GenerationOutcome.error (le `except Exception` la capture)."""
    from jobtool.aggregator.models import Job
    from jobtool.api.generation import Generator
    from jobtool.core.experience_base.store import YamlExperienceBase

    gen = Generator(
        YamlExperienceBase(tmp_path / "absente.yaml"), output_dir=tmp_path / "out"
    )
    offer = Job(
        id="h1",
        source="manual",
        native_id="",
        title="t",
        company="c",
        location="l",
        fetched_date="2026-01-01T00:00:00+00:00",
        dedup_hash="h1",
    )
    outcome = gen.generate_cv(offer)
    assert not outcome.ok
    assert outcome.error is not None and "introuvable" in outcome.error


# --- Traduction côté CLI -----------------------------------------------------


def test_cli_letter_missing_base_exits_1(tmp_path, monkeypatch):
    """Base introuvable -> exit code 1 + message lisible (typer.Exit, pas une stack)."""
    from typer.testing import CliRunner

    from jobtool.cli.main import app

    monkeypatch.setenv("MISTRAL_API_KEY", "clé-factice")
    offre = tmp_path / "offre.txt"
    offre.write_text("Une offre.", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["letter", "--offre", str(offre), "--base", str(tmp_path / "absente.yaml")],
    )
    assert result.exit_code == 1
    assert "introuvable" in result.output
