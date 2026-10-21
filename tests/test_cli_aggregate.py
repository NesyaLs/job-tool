"""Squelette CLI : la commande `jobtool aggregate` est bien câblée et son --help répond.

Le --help reste sans réseau ni clé. Les tests d'override (--keyword / --location) interceptent
le pipeline (run_aggregation_multi patché) pour CAPTURER le `criteria` réellement passé aux
connecteurs, sans aucun appel réseau : on prouve que la surcharge CLI atteint bien `criteria`.
"""

from __future__ import annotations

import textwrap

from typer.testing import CliRunner

from jobtool.cli.main import app

runner = CliRunner()


def _write_config(tmp_path, mots_cles: str = "data scientist", departement: str = "75"):
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent(f"""\
            criteres:
              motsCles: "{mots_cles}"
              departement: "{departement}"
              max_results: 10
            """),
        encoding="utf-8",
    )
    return p


def _capture_criteria(monkeypatch) -> dict:
    """Patche le pipeline pour capturer le `criteria` passé aux connecteurs (sans réseau)."""
    from jobtool.aggregator import pipeline

    captured: dict = {}

    def fake_run(connectors, criteria, db_path, export_dir, *args, **kwargs):

        # Une LISTE de recherches depuis la config multi-recherches : ici, une seule.

        criteria = criteria[0] if isinstance(criteria, list) else criteria
        captured["mots_cles"] = criteria.mots_cles
        captured["departement"] = criteria.departement
        return pipeline.RunSummary()  # errors=[] -> la commande sort en 0

    monkeypatch.setattr(pipeline, "run_collection", fake_run)
    return captured


def test_root_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "aggregate" in result.output
    assert "cv" in result.output
    assert "letter" in result.output


def test_aggregate_help_shows_all_options():
    result = runner.invoke(app, ["aggregate", "--help"])
    assert result.exit_code == 0
    for opt in (
        "--config",
        "--db",
        "--out",
        "--max-results",
        "--keyword",
        "--location",
        "--verbose",
        "--source",
    ):
        assert opt in result.output, f"option manquante dans --help : {opt}"


def test_keyword_overrides_yaml(monkeypatch, tmp_path):
    """--keyword surcharge le motsCles du YAML pour la run (atteint bien les connecteurs)."""
    cfg = _write_config(tmp_path, "data scientist")
    captured = _capture_criteria(monkeypatch)
    # --source adzuna : connecteur CONSTRUIT même sans clés (auto-isolé), donc non vide.
    result = runner.invoke(
        app,
        [
            "aggregate",
            "--source",
            "adzuna",
            "--config",
            str(cfg),
            "--keyword",
            "ingénieur réseau",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["mots_cles"] == "ingénieur réseau"


def test_without_keyword_keeps_yaml_value(monkeypatch, tmp_path):
    """Sans --keyword : le motsCles du YAML est conservé (comportement inchangé)."""
    cfg = _write_config(tmp_path, "data scientist")
    captured = _capture_criteria(monkeypatch)
    result = runner.invoke(
        app, ["aggregate", "--source", "adzuna", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert captured["mots_cles"] == "data scientist"


def test_location_overrides_yaml(monkeypatch, tmp_path):
    """--location surcharge le departement INSEE du YAML (criteria.departement)."""
    cfg = _write_config(tmp_path, departement="75,92,93,94")
    captured = _capture_criteria(monkeypatch)
    result = runner.invoke(
        app,
        [
            "aggregate",
            "--source",
            "adzuna",
            "--config",
            str(cfg),
            "--location",
            "33063",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["departement"] == "33063"


def test_without_location_keeps_yaml_value(monkeypatch, tmp_path):
    """Sans --location : le departement du YAML est conservé (comportement inchangé)."""
    cfg = _write_config(tmp_path, departement="75,92,93,94")
    captured = _capture_criteria(monkeypatch)
    result = runner.invoke(
        app, ["aggregate", "--source", "adzuna", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert captured["departement"] == "75,92,93,94"
