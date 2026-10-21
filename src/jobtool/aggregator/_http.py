"""Briques HTTP PARTAGÉES par les connecteurs (throttling + remontée d'erreur sans secret).

Extrait de francetravail/connector.py pour réemploi par les autres connecteurs (Adzuna, …),
à COMPORTEMENT CONSTANT. Aucune logique métier ici : juste le limiteur de débit et la mise
en forme du corps d'erreur HTTP (jamais de secret : on n'inclut que statut/URL/params/corps).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import requests

# Longueur d'extrait du corps d'erreur en mode CONCIS (le mode debug ne tronque pas).
DEFAULT_ERROR_BODY_EXCERPT = 300


def safe_error_body(resp: requests.Response) -> str:
    """Corps d'une réponse d'erreur, lisible et SANS jamais lever.

    JSON compact si parsable (les API décrivent souvent le paramètre fautif en JSON), sinon
    texte brut. Le corps d'erreur ne contient pas de secret (les identifiants vivent dans la
    requête, pas dans la réponse) ; on peut donc le remonter tel quel.
    """
    try:
        return json.dumps(resp.json(), ensure_ascii=False)
    except Exception:  # noqa: BLE001 — corps non-JSON ou illisible
        try:
            return (resp.text or "").strip() or "(corps vide)"
        except Exception:  # noqa: BLE001
            return "(corps illisible)"


def http_error_detail(
    resp: requests.Response,
    url: str,
    params: dict[str, Any],
    *,
    debug: bool = False,
    excerpt: int = DEFAULT_ERROR_BODY_EXCERPT,
) -> str:
    """Message d'erreur HTTP ENRICHI : statut + URL + params + corps de la réponse.

    Aucun secret : `params` ne doit contenir que des critères (l'appelant exclut les
    identifiants). En mode concis le corps est tronqué à `excerpt` ; en debug, complet.
    """
    body = safe_error_body(resp)
    if not debug and len(body) > excerpt:
        body = (
            body[:excerpt]
            + f"… (+{len(body) - excerpt} car., --verbose pour tout voir)"
        )
    return f"HTTP {resp.status_code} sur {url} params={params} — corps: {body}"


class RateLimiter:
    """Limiteur simple : impose un intervalle minimal entre requêtes (≤ N req/s).

    `clock`/`sleep` injectables pour prouver le throttling sans attente réelle.
    """

    def __init__(
        self,
        max_per_second: float = 4,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        """Patiente si nécessaire pour ne pas dépasser le débit autorisé."""
        if self._last is not None:
            elapsed = self._clock() - self._last
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


# --- Retry réseau OPT-IN sur 429 / 5xx (utilisé par les connecteurs sans throttling serveur) ---
# Réglages par défaut (modifiables par l'appelant). Le retry rejoue le MÊME GET.
DEFAULT_MAX_RETRIES = (
    4  # nb max de RE-tentatives (au-delà : on renvoie la dernière réponse)
)
DEFAULT_BACKOFF_BASE = 1.0  # délai de base (s) ; back-off = base * 2**(k-1)


def _retry_after_seconds(resp: requests.Response) -> float | None:
    """Lit l'en-tête Retry-After (secondes entières) si présent et numérique, sinon None.

    On ne gère QUE la forme « nombre de secondes » (la forme date HTTP est rare ici et
    ajouterait du parsing fragile) ; à défaut on retombe sur le back-off exponentiel.
    """
    headers = getattr(resp, "headers", None) or {}
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value.strip()))
    except (ValueError, AttributeError):
        return None


def _request_with_retry(
    do_request: Callable[[], requests.Response],
    *,
    rate_limiter: RateLimiter | None,
    sleep: Callable[[float], None],
    max_retries: int,
    backoff_base: float,
    retry_statuses: tuple[int, ...],
) -> requests.Response:
    """Cœur PARTAGÉ du retry HTTP (méthode-agnostique) : 429/5xx + back-off + Retry-After.

    `do_request` exécute UNE tentative (GET ou POST) et renvoie la réponse. Toute la
    politique de réessai vit ici ; `get_with_retry`/`post_with_retry` ne font que fournir
    la fermeture de la requête. Comportement identique à l'ancien `get_with_retry`.
    """
    attempt = 0
    while True:
        if rate_limiter is not None:
            rate_limiter.wait()
        resp = do_request()
        if resp.status_code not in retry_statuses or attempt >= max_retries:
            return resp
        attempt += 1
        delay = _retry_after_seconds(resp)
        if delay is None:
            delay = backoff_base * (2 ** (attempt - 1))
        sleep(delay)


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Response:
    """GET enrobé d'un retry sur 429/5xx avec back-off exponentiel honorant Retry-After.

    - Avant chaque tentative, `rate_limiter.wait()` (si fourni) impose le débit.
    - Sur un statut « réessayable » : on attend (Retry-After si présent, sinon back-off
      exponentiel `backoff_base * 2**(k-1)`) puis on rejoue, jusqu'à `max_retries` fois.
    - Au-delà du plafond, on RENVOIE la dernière réponse telle quelle : c'est à l'appelant
      (le connecteur) de décider — il note l'erreur et garde le partiel (isolation projet).
    - Les erreurs réseau (requests.RequestException) NE sont PAS rattrapées ici : elles
      remontent, et le connecteur les capture comme aujourd'hui. `sleep` est injectable
      pour prouver le back-off sans attendre.
    """
    return _request_with_retry(
        lambda: session.get(url, params=params, headers=headers, timeout=timeout),
        rate_limiter=rate_limiter,
        sleep=sleep,
        max_retries=max_retries,
        backoff_base=backoff_base,
        retry_statuses=retry_statuses,
    )


def post_with_retry(
    session: requests.Session,
    url: str,
    *,
    json: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Response:
    """POST enrobé du MÊME retry que `get_with_retry` (corps JSON rejoué à l'identique).

    Utile pour les API dont le LISTING est en POST (ex. Workday CXS). Les statuts terminaux
    de configuration (400/404/422) NE sont volontairement PAS dans `retry_statuses` : les
    réessayer est inutile (requête ou tenant erroné), l'appelant note et isole.
    """
    return _request_with_retry(
        lambda: session.post(url, json=json, headers=headers, timeout=timeout),
        rate_limiter=rate_limiter,
        sleep=sleep,
        max_retries=max_retries,
        backoff_base=backoff_base,
        retry_statuses=retry_statuses,
    )
