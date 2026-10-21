"""Commande `jobtool api` : démarre le serveur HTTP FastAPI via uvicorn.

Sur le modèle des autres commandes : l'import LOURD (uvicorn) est DIFFÉRÉ dans la fonction
pour que `jobtool api --help` réponde sans charger le serveur. L'app elle-même est désignée
par chaîne d'import (`jobtool.api.main:app`) pour permettre `--reload` (uvicorn réimporte).

`--local` force le mode mono-poste (SQLite, sans authentification) même si `.env` déclare
Postgres/Supabase : les variables d'environnement l'emportent sur `.env` dans
pydantic-settings, et une valeur VIDE est traitée comme absente partout. Elles sont posées
dans l'environnement du process (et non dans un objet settings) pour être héritées par le
sous-process que lance `--reload`.
"""

import os

import typer

#: Variables qui font sortir du mode mono-poste ; `--local` les neutralise.
_REMOTE_ENV_VARS = (
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_JWKS_URL",
    "SUPABASE_PUBLISHABLE_KEY",
)


def api(
    host: str = typer.Option("127.0.0.1", help="adresse d'écoute (défaut : 127.0.0.1)"),
    port: int = typer.Option(8000, help="port d'écoute (défaut : 8000)"),
    reload: bool = typer.Option(
        False, help="recharge à chaud sur modification du code (dev)"
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="mode mono-poste : SQLite local, sans Supabase (ignore DATABASE_URL "
        "et SUPABASE_* du .env)",
    ),
) -> None:
    """Démarre l'API HTTP (uvicorn) — routes /offers + génération CV/lettre."""
    import uvicorn

    if local:
        for name in _REMOTE_ENV_VARS:
            os.environ[name] = ""
        typer.echo("Mode local : SQLite, sans authentification.")

    uvicorn.run("jobtool.api.main:app", host=host, port=port, reload=reload)
