"""Câblage de `jobtool api` : l'option `--local` neutralise Postgres et Supabase."""

from __future__ import annotations

from typer.testing import CliRunner

from jobtool.cli.api import _REMOTE_ENV_VARS
from jobtool.cli.main import app
from jobtool.settings import AggregatorSettings

runner = CliRunner()


def test_api_help_shows_local():
    result = runner.invoke(app, ["api", "--help"])
    assert result.exit_code == 0
    assert "--local" in result.output


def test_local_forces_single_user_mode(monkeypatch, tmp_path):
    # .env qui déclarerait la base et l'authentification distantes.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://x@h/db\nSUPABASE_JWKS_URL=https://h/jwks\n"
    )
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append(kw))
    for name in _REMOTE_ENV_VARS:
        # restaurée par monkeypatch en fin de test
        monkeypatch.setenv(name, "sentinelle")

    result = runner.invoke(app, ["api", "--local"])

    assert result.exit_code == 0, result.output
    assert calls
    settings = AggregatorSettings()
    assert not settings.database_url
    assert not settings.supabase_jwks_url


def test_without_local_env_untouched(monkeypatch):
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@h/db")
    result = runner.invoke(app, ["api"])
    assert result.exit_code == 0
    assert AggregatorSettings().database_url == "postgresql://x@h/db"
