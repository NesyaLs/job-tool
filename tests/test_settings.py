"""Preuve de AggregatorSettings : valeurs par défaut + lecture de l'environnement.

On instancie avec `_env_file=None` pour IGNORER le .env réel du dépôt (isolation), et on
contrôle les variables via monkeypatch. Aucun secret réel n'est lu.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobtool.settings import AggregatorSettings

# Variables lues par les settings — nettoyées avant chaque test pour des défauts déterministes.
_ENV_VARS = [
    "MISTRAL_API_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "FRANCE_TRAVAIL_CLIENT_ID",
    "FRANCE_TRAVAIL_CLIENT_SECRET",
    "FRANCE_TRAVAIL_SCOPE",
    "JOBTOOL_AGG_DEBUG",
    "JOBTOOL_AGG_CONFIG",
    "JOBTOOL_AGG_DB",
    "JOBTOOL_AGG_OUT",
]


@pytest.fixture
def clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_defaults_without_env(clean_env):
    s = AggregatorSettings(_env_file=None)
    # Identifiants absents -> None (la source sera ignorée/auto-isolée, jamais d'erreur).
    assert s.adzuna_app_id is None and s.adzuna_app_key is None
    assert s.france_travail_client_id is None and s.france_travail_client_secret is None
    # Scope : défaut stable, debug off, chemins par défaut de l'agrégateur.
    assert s.france_travail_scope == "api_offresdemploiv2 o2dsoffre"
    assert s.agg_debug is False
    assert s.config_path == Path("data/aggregator/config.yaml")
    assert s.db_path == Path("data/aggregator/jobs.db")
    assert s.export_dir == Path("output/aggregator/offres")


def test_reads_secrets_from_env(clean_env):
    clean_env.setenv("ADZUNA_APP_ID", "id-123")
    clean_env.setenv("ADZUNA_APP_KEY", "key-456")
    clean_env.setenv("FRANCE_TRAVAIL_CLIENT_ID", "ft-id")
    clean_env.setenv("FRANCE_TRAVAIL_CLIENT_SECRET", "ft-secret")
    clean_env.setenv("FRANCE_TRAVAIL_SCOPE", "scope-perso")

    s = AggregatorSettings(_env_file=None)
    assert s.adzuna_app_id == "id-123"
    assert s.adzuna_app_key == "key-456"
    assert s.france_travail_client_id == "ft-id"
    assert s.france_travail_client_secret == "ft-secret"
    assert s.france_travail_scope == "scope-perso"


def test_mistral_key_read_from_dotenv_without_exported_var(clean_env, tmp_path):
    """La clé du modèle vient du `.env` même quand rien n'est exporté.

    Régression : seules les commandes CLI appellent `load_dotenv()`. Le serveur API ne
    voyait donc jamais un `.env` pourtant correct, et la génération échouait sur
    « MISTRAL_API_KEY absente ». La clé passe désormais par le settings, lu dans tout
    processus — voir core/llm/client.make_client.
    """
    (tmp_path / ".env").write_text("MISTRAL_API_KEY=clé-du-fichier\n", encoding="utf-8")
    clean_env.chdir(tmp_path)

    assert AggregatorSettings().mistral_api_key == "clé-du-fichier"

    from jobtool.core.llm.client import make_client

    assert make_client() is not None, "client construit sans variable exportée"


def test_exported_mistral_key_wins_over_dotenv(clean_env, tmp_path):
    """Une clé exportée l'emporte sur le fichier (bascule ponctuelle de clé)."""
    (tmp_path / ".env").write_text("MISTRAL_API_KEY=clé-du-fichier\n", encoding="utf-8")
    clean_env.chdir(tmp_path)
    clean_env.setenv("MISTRAL_API_KEY", "clé-exportée")

    from jobtool.core.llm.client import make_client

    client = make_client()
    assert client.sdk_configuration.security.api_key == "clé-exportée"


def test_missing_mistral_key_says_where_dotenv_is_searched(clean_env, tmp_path):
    """Sans clé nulle part : l'erreur nomme le dossier où le `.env` a été cherché."""
    clean_env.chdir(tmp_path)  # tmp_path ne contient aucun .env

    from jobtool.core.llm.client import make_client

    with pytest.raises(RuntimeError) as err:
        make_client()
    assert "MISTRAL_API_KEY absente" in str(err.value)
    assert str(tmp_path) in str(err.value), "le message dit OÙ le .env est cherché"


def test_debug_flag_and_path_overrides(clean_env):
    clean_env.setenv("JOBTOOL_AGG_DEBUG", "1")
    clean_env.setenv("JOBTOOL_AGG_CONFIG", "/tmp/criteres.yaml")
    clean_env.setenv("JOBTOOL_AGG_DB", "/tmp/jobs.db")
    clean_env.setenv("JOBTOOL_AGG_OUT", "/tmp/offres")

    s = AggregatorSettings(_env_file=None)
    assert s.agg_debug is True, "JOBTOOL_AGG_DEBUG=1 -> debug actif"
    assert s.config_path == Path("/tmp/criteres.yaml")
    assert s.db_path == Path("/tmp/jobs.db")
    assert s.export_dir == Path("/tmp/offres")
