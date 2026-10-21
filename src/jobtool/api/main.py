"""Point d'entrée de l'API HTTP jobtool (FastAPI).

Lancement :
  - via le CLI :   jobtool api            (démarre uvicorn ; voir cli/api.py)
  - directement :  uvicorn jobtool.api.main:app --reload

`create_app()` assemble l'application (montage des routers) sans aucun effet de bord réseau
à l'import : importer ce module ne nécessite ni clé ni connexion. La config (chemins de la
base SQLite et de la base d'expériences) vient du settings existant via les dépendances
(api/deps.py), surchargeables dans les tests.

Le front statique (dossier `Interface/` à la racine du dépôt) est servi par la MÊME app, en
MÊME ORIGINE que l'API : le navigateur peut donc faire ses `fetch` en chemins relatifs, sans
CORS. Voir le montage StaticFiles (en dernier) dans `create_app`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from jobtool.api.routers import (
    admin,
    aggregate,
    api_keys,
    documents,
    interview,
    offers,
    profile,
    profiles,
    session,
)

# Front livré à la racine du dépôt (hors paquet) : src/jobtool/api/main.py -> parents[3] = racine.
FRONT_DIR = Path(__file__).resolve().parents[3] / "Interface"


class _RevalidatedStaticFiles(StaticFiles):
    """StaticFiles + `Cache-Control: no-cache` : le navigateur REVALIDE au lieu de deviner.

    StaticFiles n'envoie qu'un `etag`/`last-modified`. Sans `Cache-Control`, les navigateurs
    appliquent une fraîcheur HEURISTIQUE et peuvent resservir leur copie de app.js sans
    demander au serveur — une modification du front reste alors invisible jusqu'à un
    rechargement forcé. `no-cache` ne désactive PAS le cache : il impose la revalidation, qui
    coûte un 304 vide quand le fichier n'a pas changé.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    """Construit l'application FastAPI (routers + front statique). Aucune logique métier ici."""
    app = FastAPI(
        title="jobtool API",
        version="0.1.0",
        summary="Couche HTTP par-dessus les services jobtool (offres, génération CV/lettre).",
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Sonde de vie (ne touche ni base ni LLM)."""
        return {"status": "ok"}

    # Routes API enregistrées AVANT le front : elles restent prioritaires.
    app.include_router(offers.router)
    app.include_router(profile.router)
    app.include_router(profiles.router)
    app.include_router(interview.router)
    app.include_router(aggregate.router)
    app.include_router(documents.router)
    app.include_router(admin.router)
    app.include_router(session.router)
    app.include_router(api_keys.router)

    # Front monté EN DERNIER. Un mount "/" est un attrape-tout ; comme Starlette teste les
    # routes dans l'ordre d'ajout, /offers, /health, /docs… (ci-dessus) gagnent, et seul le
    # RESTE retombe sur les fichiers statiques. `html=True` sert index.html à la racine et
    # résout les chemins relatifs (styles.css, app.js). Monté seulement si le dossier existe
    # (un install hors dépôt n'a pas Interface/ : l'API reste utilisable sans le front).
    if FRONT_DIR.is_dir():
        app.mount(
            "/", _RevalidatedStaticFiles(directory=FRONT_DIR, html=True), name="front"
        )
    return app


app = create_app()
