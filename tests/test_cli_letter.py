"""Commande `jobtool letter` : câblage Typer, et la table échec -> code de sortie.

L'enchaînement lui-même (passe 1/2 + rendu + checks) est couvert ailleurs : par
`test_cover_letter_*` pour le domaine, et par `test_run_batch_e2e` qui traverse le vrai
`process_letter` avec Typst réel. Ce que la commande possède EN PROPRE depuis qu'elle ne
réécrit plus l'enchaînement, c'est la présentation et le code de retour — c'est donc cela
qu'on vérifie ici, en remplaçant `process_letter` par un résultat fabriqué.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from jobtool.cli.main import app
from jobtool.core.traceability import Violation
from jobtool.services.batch_service import LetterResult

runner = CliRunner()


def test_letter_help_shows_options():
    result = runner.invoke(app, ["letter", "--help"])
    assert result.exit_code == 0
    for opt in ("--offre", "--base", "--out", "--template"):
        assert opt in result.output, f"option manquante dans --help : {opt}"


@pytest.fixture
def offre(tmp_path):
    """Un fichier d'offre non vide : la commande refuse de tourner sans."""
    path = tmp_path / "offre.txt"
    path.write_text("Développeur Python chez Acme.", encoding="utf-8")
    return path


@pytest.fixture
def invoke(monkeypatch, tmp_path, offre, example_base):
    """Lance `jobtool letter` avec un `process_letter` remplacé par un résultat fabriqué.

    La clé d'API est simulée : la commande la vérifie avant tout appel, et aucun appel réel
    n'a lieu puisque le service est remplacé.
    """
    base_yaml = tmp_path / "base.yaml"

    def run(result: LetterResult):
        monkeypatch.setenv("MISTRAL_API_KEY", "clé-de-test")
        monkeypatch.setattr(
            "jobtool.core.experience_base.loader.load_base", lambda _p: example_base
        )
        monkeypatch.setattr(
            "jobtool.services.batch_service.process_letter",
            lambda *a, **k: result,
        )
        return runner.invoke(
            app,
            [
                "letter",
                "--offre",
                str(offre),
                "--base",
                str(base_yaml),
                "--out",
                str(tmp_path / "lettre.pdf"),
            ],
        )

    return run


def _ok_result(**overrides) -> LetterResult:
    """Une lettre produite et conforme, que chaque test dégrade sur un seul point."""
    fields = {
        "mode": "cible",
        "pdf_path": "output/lettre.pdf",
        "pages": 1,
        "rendered": True,
    }
    fields.update(overrides)
    return LetterResult(**fields)


def test_lettre_conforme_sort_en_zero(invoke):
    result = invoke(_ok_result())
    assert result.exit_code == 0, result.output
    assert "OK : lettre générée, 1 page, texte extractible." in result.output


def test_passe1_non_convergee_sort_en_un_et_liste_les_violations(invoke):
    """Les violations s'affichent UNE PAR LIGNE : c'est ce que la liste structurée permet,
    et que le résumé d'une ligne de `error` ne permettait pas."""
    issues = [
        Violation(kind="invented_hard_skill", message="compétence inventée : Rust"),
        Violation(kind="unknown_company", message="entreprise absente de l'offre"),
    ]
    result = invoke(_ok_result(routing_issues=issues, error="passe 1 non convergée"))
    assert result.exit_code == 1
    assert "la passe 1 (routage) n'a pas convergé — 2 violation(s)" in result.output
    assert "compétence inventée : Rust" in result.output
    assert "entreprise absente de l'offre" in result.output


def test_passe2_non_convergee_sort_en_un(invoke):
    issues = [Violation(kind="too_long", message="paragraphe Vous trop long")]
    result = invoke(_ok_result(draft_issues=issues, error="passe 2 non convergée"))
    assert result.exit_code == 1
    assert "la passe 2 (rédaction) n'a pas convergé" in result.output
    assert "paragraphe Vous trop long" in result.output


def test_echec_de_generation_sort_en_erreur(invoke):
    """Génération ou rendu en échec : le service ne lève pas, la commande, si."""
    result = invoke(LetterResult(error="rendu : typst a échoué"))
    assert result.exit_code != 0
    assert "typst a échoué" in result.output


def test_debordement_de_page_sort_en_un_avec_le_conseil_de_reglage(invoke):
    result = invoke(_ok_result(pages=2, page_violations=["2 pages, attendu 1"]))
    assert result.exit_code == 1
    assert "2 pages, attendu 1" in result.output
    assert "plafonds de longueur de la passe 2" in result.output


def test_ats_incomplet_sort_en_un(invoke):
    result = invoke(_ok_result(missing=["Acme"]))
    assert result.exit_code == 1
    assert "Alerte ATS" in result.output
    assert "'Acme'" in result.output


def test_template_is_passed_to_the_service(monkeypatch, offre, example_base):
    """`--template moderne` arrive jusqu'au rendu ; un nom inconnu est refusé par Typer."""
    received = {}

    def fake(*_a, **kwargs):
        received.update(kwargs)
        return LetterResult(rendered=True, pdf_path="x.pdf", pages=1)

    monkeypatch.setenv("MISTRAL_API_KEY", "clé-de-test")
    monkeypatch.setattr(
        "jobtool.core.experience_base.loader.load_base", lambda _p: example_base
    )
    monkeypatch.setattr("jobtool.services.batch_service.process_letter", fake)

    ok = runner.invoke(app, ["letter", "--offre", str(offre), "--template", "moderne"])
    assert ok.exit_code == 0, ok.output
    assert received["template"] == "moderne"

    default = runner.invoke(app, ["letter", "--offre", str(offre)])
    assert default.exit_code == 0 and received["template"] == "sobre"

    bad = runner.invoke(app, ["letter", "--offre", str(offre), "--template", "inconnu"])
    assert bad.exit_code == 2
