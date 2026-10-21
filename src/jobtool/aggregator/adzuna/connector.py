"""Connecteur Adzuna (méta-agrégateur) — synchrone, isolé, throttlé. National FR (cette brique).

Endpoint : GET https://api.adzuna.com/v1/api/jobs/{pays}/search/{page}  (pays = « fr », HTTPS).
Auth : `app_id` + `app_key` en QUERY params (pas d'OAuth -> pas d'auth.py). Jamais loggés.
Pagination : numéro de page 1-indexé dans le CHEMIN + `results_per_page`. Statut 200 = succès
(Adzuna ne renvoie pas de 206). Throttling CONSERVATEUR (≤ 1 req/s : limites du tier gratuit
non publiées). User-Agent explicite.

PÉRIMÈTRE (décisions validées) :
  - GÉO : NATIONAL uniquement -> on n'envoie que `what` (mots-clés), jamais `where`/`distance`
    (les critères INSEE de SearchCriteria ne sont pas transposables ; réconciliation = brique
    ultérieure). On ne touche pas à SearchCriteria.
  - DESCRIPTION : le snippet Adzuna est mappé tel quel dans `description` (limite assumée ;
    INTERDICTION d'aller chercher le texte complet derrière redirect_url = scraping).
  - SALAIRE : `Salary(min, max, currency="EUR", period="year")` ; les salaires PRÉDITS sont
    conservés ; `salary_is_predicted` est tracé dans `raw` (transparence).

ISOLATION : réseau, 4xx/5xx, ou clés absentes -> on note dans `self.errors` et on renvoie le
PARTIEL (jamais d'exception ; n'interrompt pas les autres connecteurs).
MINIMISATION : `raw` = LISTE BLANCHE (aucune donnée personnelle).

Le connecteur produit des `RawJob` ; la normalisation (hash/dédup/nettoyage) reste dans
normalize.py — AUCUNE logique de ce type ici.
"""

from __future__ import annotations

from typing import Any

import requests

from jobtool.aggregator._http import RateLimiter, http_error_detail
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.models import RawJob, Salary, SearchCriteria

BASE_URL = "https://api.adzuna.com/v1/api"
COUNTRY = "fr"  # PAYS FR (surtout pas « gb » copié d'un exemple de doc)
MAX_REQUESTS_PER_SECOND = 1  # conservateur : limites du tier gratuit non publiées
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_TIMEOUT = 20
_USER_AGENT = "jobtool-aggregator/0.1 (+personal job search tool)"

__all__ = ["BASE_URL", "COUNTRY", "AdzunaConnector", "map_offer_to_rawjob"]


def _map_contract(offer: dict[str, Any]) -> str:
    """Concatène contract_type (permanent/contract) et contract_time (full_time/…)."""
    parts = [offer.get("contract_type"), offer.get("contract_time")]
    return " / ".join(p for p in parts if p)


def _map_salary(offer: dict[str, Any]) -> Salary | None:
    """Salaire Adzuna -> Salary (EUR, annuel). None si ni min ni max. Prédit = conservé."""
    smin = offer.get("salary_min")
    smax = offer.get("salary_max")
    if smin is None and smax is None:
        return None
    return Salary(min=smin, max=smax, currency="EUR", period="year")


def _minimized_raw(offer: dict[str, Any]) -> dict[str, Any]:
    """LISTE BLANCHE des champs conservés dans `raw` (aucune donnée personnelle).

    Inclut les champs Adzuna sans équivalent dans Job : salary_is_predicted, latitude/longitude,
    contract_time, category, area — pour ne rien perdre d'utile sans élargir le schéma Job.
    """
    company = offer.get("company") or {}
    location = offer.get("location") or {}
    category = offer.get("category") or {}
    return {
        "id": offer.get("id"),
        "title": offer.get("title"),
        "company": {"display_name": company.get("display_name")},
        "location": {
            "display_name": location.get("display_name"),
            "area": location.get("area"),
        },
        "created": offer.get("created"),
        "contract_type": offer.get("contract_type"),
        "contract_time": offer.get("contract_time"),
        "salary_min": offer.get("salary_min"),
        "salary_max": offer.get("salary_max"),
        "salary_is_predicted": offer.get("salary_is_predicted"),
        "category": {"label": category.get("label"), "tag": category.get("tag")},
        "latitude": offer.get("latitude"),
        "longitude": offer.get("longitude"),
        "redirect_url": offer.get("redirect_url"),
    }


def map_offer_to_rawjob(offer: dict[str, Any]) -> RawJob:
    """Mappe un objet `results[i]` de l'API Adzuna vers un RawJob unifié."""
    company = offer.get("company") or {}
    location = offer.get("location") or {}
    return RawJob(
        source="adzuna",
        native_id=str(offer.get("id", "")),
        title=offer.get("title", "") or "",
        company=company.get("display_name", "") or "",
        location=location.get("display_name", "") or "",
        remote="inconnu",  # Adzuna n'expose pas de champ télétravail fiable
        contract_type=_map_contract(offer),
        salary=_map_salary(offer),
        description=offer.get("description", "") or "",  # snippet (limite assumée)
        url=offer.get("redirect_url", "") or "",
        posted_date=offer.get("created"),
        raw=_minimized_raw(offer),
    )


class AdzunaConnector(Connector):
    """Interroge l'endpoint search Adzuna (national FR) avec pagination, throttling, isolation."""

    source = "adzuna"

    def __init__(
        self,
        app_id: str | None,
        app_key: str | None,
        session: requests.Session | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
        rate_limiter: RateLimiter | None = None,
        user_agent: str = _USER_AGENT,
        debug: bool = False,
        country: str = COUNTRY,
    ) -> None:
        super().__init__()
        self._app_id = app_id
        self._app_key = app_key
        self._session = session or requests.Session()
        self._page_size = page_size
        self._rate = rate_limiter or RateLimiter(max_per_second=MAX_REQUESTS_PER_SECOND)
        self._user_agent = user_agent
        self._debug = debug
        self._country = country

    def _url(self, page: int) -> str:
        return f"{BASE_URL}/jobs/{self._country}/search/{page}"

    def _params(self, criteria: SearchCriteria) -> dict[str, Any]:
        """Query params. National : `what` seulement (jamais where/distance dans cette brique)."""
        params: dict[str, Any] = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "results_per_page": self._page_size,
            "content-type": "application/json",
        }
        if criteria.mots_cles:
            params["what"] = criteria.mots_cles
        return params

    @staticmethod
    def _redacted(params: dict[str, Any]) -> dict[str, Any]:
        """Params SANS les secrets (app_id/app_key) — pour les messages d'erreur."""
        return {k: v for k, v in params.items() if k not in ("app_id", "app_key")}

    def fetch(self, criteria: SearchCriteria) -> list[RawJob]:
        """Récupère les offres en paginant. Ne lève jamais : renvoie le partiel sur erreur."""
        self.errors = []
        collected: list[RawJob] = []

        if not self._app_id or not self._app_key:
            # Clés absentes : on IGNORE Adzuna sans planter la run ni les autres connecteurs.
            self.errors.append(
                "clés Adzuna absentes (ADZUNA_APP_ID / ADZUNA_APP_KEY) : source ignorée"
            )
            return collected

        page = 1
        while len(collected) < criteria.max_results:
            params = self._params(criteria)
            safe_params = self._redacted(params)  # jamais de secret dans les logs
            url = self._url(page)
            try:
                self._rate.wait()  # throttling ≤ 1 req/s
                resp = self._session.get(
                    url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": self._user_agent,
                    },
                    timeout=_DEFAULT_TIMEOUT,
                )
            except requests.RequestException as exc:
                self.errors.append(f"erreur réseau page {page} : {exc}")
                break

            if resp.status_code != 200:
                self.errors.append(
                    http_error_detail(resp, url, safe_params, debug=self._debug)
                )
                break

            try:
                payload = resp.json()
            except ValueError as exc:
                self.errors.append(f"réponse JSON invalide page {page} : {exc}")
                break

            results = payload.get("results") or []
            for offer in results:
                collected.append(map_offer_to_rawjob(offer))

            # Fin de pagination : page vide ou non pleine (plus de pages utiles).
            if not results or len(results) < self._page_size:
                break
            page += 1

        return collected[: criteria.max_results]
