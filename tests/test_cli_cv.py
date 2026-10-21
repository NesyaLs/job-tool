"""Squelette CLI : la commande `jobtool cv` est câblée et son --help répond.

La logique de génération (run_single, rendu, checks) est couverte par les tests
`test_split_generation` / `test_template_options` ; ici on prouve seulement le câblage Typer.
(Le fait que `cv --help` ne tire pas mistralai se vérifie en sous-processus, pas ici : la
suite partagée a déjà chargé le SDK via d'autres tests.)
"""

from __future__ import annotations

from typer.testing import CliRunner

from jobtool.cli.main import app

runner = CliRunner()


def test_cv_help_shows_options():
    result = runner.invoke(app, ["cv", "--help"])
    assert result.exit_code == 0
    assert "--template" in result.output
    assert "--name" in result.output
