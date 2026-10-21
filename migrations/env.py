"""Contexte Alembic — branché sur le settings du projet, pas sur alembic.ini.

L'URL de la base n'est PAS écrite dans alembic.ini : elle contient un mot de passe. Elle
vient du même endroit que tous les autres secrets du projet — le settings pydantic, qui
lit `.env` (project_ctx.md : « Secrets via settings, jamais en dur, jamais loggés »).

`target_metadata` pointe le schéma unique du code : `alembic revision --autogenerate`
compare donc les tables réelles à celles que le code déclare, sans schéma dupliqué.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from jobtool.core.db.schema import metadata
from jobtool.settings import AggregatorSettings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _database_url() -> str:
    """URL de la base à migrer, depuis le settings (jamais depuis alembic.ini)."""
    url = AggregatorSettings().database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL absente : renseigne-la dans .env "
            "(Supabase -> Project Settings -> Database -> Connection string -> URI)."
        )
    return url


def run_migrations_offline() -> None:
    """Génère le SQL sans se connecter (`alembic upgrade --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Applique les migrations sur la base réelle."""
    url = _database_url()
    # Même réglage anti-pgbouncer que la couche applicative (voir core/db/engine.py).
    connect_args = {} if url.startswith("sqlite") else {"prepare_threshold": None}
    engine = create_engine(url, connect_args=connect_args)

    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
