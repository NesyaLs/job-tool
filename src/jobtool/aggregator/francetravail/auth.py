"""OAuth2 « client credentials » France Travail : obtention + CACHE du token.

Endpoint token (réalité confirmée) :
  POST https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire
  corps form-urlencoded : grant_type=client_credentials, client_id, client_secret, scope.

Le token est mis en cache et réutilisé tant qu'il n'est pas (presque) expiré (`expires_in`).
SECRETS : jamais journalisés. `session` (requests.Session) et `clock` sont injectables pour
les tests (preuve sans réseau). Aucune dépendance hors `requests`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import requests

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
# Marge de sécurité : on rafraîchit un peu AVANT l'expiration réelle (horloges, latence).
_EXPIRY_SAFETY_SECONDS = 60.0
_DEFAULT_TIMEOUT = 20


def _safe_token_error_body(resp: requests.Response) -> str:
    """Corps d'erreur du endpoint token, lisible et sans jamais lever (ni exposer de secret)."""
    try:
        return json.dumps(resp.json(), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        try:
            return (resp.text or "").strip() or "(corps vide)"
        except Exception:  # noqa: BLE001
            return "(corps illisible)"


class FranceTravailAuthError(RuntimeError):
    """Échec d'authentification (sans jamais exposer le secret)."""


class FranceTravailAuth:
    """Fournit un access_token valide, en le mettant en cache jusqu'à expiration."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scope: str,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        user_agent: str = "jobtool-aggregator/0.1 (+personal job search tool)",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._session = session or requests.Session()
        self._clock = clock
        self._user_agent = user_agent
        self._token: str | None = None
        self._expiry: float = 0.0  # instant (clock) avant lequel le token reste valide

    def get_token(self) -> str:
        """Retourne un token valide : depuis le cache si possible, sinon en redemande un."""
        if self._token is not None and self._clock() < self._expiry:
            return self._token
        return self._request_token()

    def _request_token(self) -> str:
        try:
            resp = self._session.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._scope,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": self._user_agent,
                },
                timeout=_DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            # On n'inclut JAMAIS les identifiants dans le message.
            raise FranceTravailAuthError(f"échec réseau à l'obtention du token : {exc}")

        if resp.status_code != 200:
            # On remonte le CORPS de l'erreur (ex. {"error":"invalid_scope"}) — utile au
            # diagnostic — mais JAMAIS les identifiants (qui ne sont que dans la requête).
            raise FranceTravailAuthError(
                f"obtention du token refusée (HTTP {resp.status_code}) — corps: "
                f"{_safe_token_error_body(resp)}"
            )

        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise FranceTravailAuthError("réponse token sans access_token")
        expires_in = float(payload.get("expires_in", 0) or 0)
        self._token = token
        self._expiry = self._clock() + max(0.0, expires_in - _EXPIRY_SAFETY_SECONDS)
        return token
