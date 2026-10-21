"""Connecteur France Travail (API Offres d'emploi v2) — synchrone, isolé, throttlé.

Endpoint : GET https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search
Particularités CONFIRMÉES gérées ici :
  - l'API renvoie souvent **HTTP 206 Partial Content** quand c'est paginé : 200 ET 206 sont
    des SUCCÈS (un `== 200` strict raterait des offres) ;
  - pagination via le paramètre `range` au format `p-d` (ex. `0-49`) ;
  - quota **4 req/s par application** -> throttling (RateLimiter) ≤ 4 req/s ;
  - User-Agent explicite ; jamais de secret dans les logs.

ISOLATION : aucune erreur réseau/HTTP ne fait planter la run — on capture, on note dans
`self.errors`, et on renvoie les résultats PARTIELS déjà collectés.

MINIMISATION : le `raw` stocké est une LISTE BLANCHE de champs utiles ; les coordonnées de
contact (nom/courriel/téléphone) de l'annonce ne sont JAMAIS conservées.
"""

from __future__ import annotations

from typing import Any

import requests

from jobtool.aggregator._http import RateLimiter, http_error_detail
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.francetravail.auth import (
    FranceTravailAuth,
    FranceTravailAuthError,
)
from jobtool.aggregator.models import RawJob, Remote, SearchCriteria

# RateLimiter est ré-exporté ici (déplacé dans aggregator/_http.py) : les appelants
# historiques `from ...francetravail.connector import RateLimiter` restent valides.
__all__ = ["SEARCH_URL", "FranceTravailConnector", "RateLimiter", "map_offer_to_rawjob"]

SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
MAX_REQUESTS_PER_SECOND = 4
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_TIMEOUT = 20
_USER_AGENT = "jobtool-aggregator/0.1 (+personal job search tool)"


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def _minimized_raw(offer: dict[str, Any]) -> dict[str, Any]:
    """LISTE BLANCHE des champs conservés dans `raw` (jamais de contact personnel)."""
    lieu = offer.get("lieuTravail") or {}
    entreprise = offer.get("entreprise") or {}
    return {
        "id": offer.get("id"),
        "intitule": offer.get("intitule"),
        "typeContrat": offer.get("typeContrat"),
        "typeContratLibelle": offer.get("typeContratLibelle"),
        # Contrat et temps de travail : lus par la normalisation (contracts.py).
        "natureContrat": offer.get("natureContrat"),
        "alternance": offer.get("alternance"),
        "dureeTravailLibelleConverti": offer.get("dureeTravailLibelleConverti"),
        "dateCreation": offer.get("dateCreation"),
        "lieuTravail": {
            "libelle": lieu.get("libelle"),
            "commune": lieu.get("commune"),
            "codePostal": lieu.get("codePostal"),
        },
        "entreprise": {"nom": entreprise.get("nom")},
        # Offres de partenaires (origine 2) : le lien vers le site d'origine — souvent un
        # ATS — sert au dédoublonnage par URL. Nom et lien seulement, jamais de contact.
        "partenaires": [
            {"nom": pa.get("nom"), "url": pa.get("url")}
            for pa in ((offer.get("origineOffre") or {}).get("partenaires") or [])
            if isinstance(pa, dict)
        ],
        "romeCode": offer.get("romeCode"),
        "romeLibelle": offer.get("romeLibelle"),
        # NB : on n'inclut DÉLIBÉRÉMENT pas `offer["contact"]` (minimisation des données).
    }


def _map_remote(offer: dict[str, Any]) -> Remote:
    """France Travail n'expose pas un champ télétravail fiable -> « inconnu »."""
    return "inconnu"


def map_offer_to_rawjob(offer: dict[str, Any]) -> RawJob:
    """Mappe un objet `resultats[i]` de l'API France Travail vers un RawJob unifié."""
    lieu = offer.get("lieuTravail") or {}
    entreprise = offer.get("entreprise") or {}
    origine = offer.get("origineOffre") or {}
    return RawJob(
        source="france_travail",
        native_id=str(offer.get("id", "")),
        title=offer.get("intitule", "") or "",
        company=entreprise.get("nom", "") or "",
        location=lieu.get("libelle", "") or "",
        remote=_map_remote(offer),
        contract_type=_first(offer, "typeContratLibelle", "typeContrat", default="")
        or "",
        salary=None,
        description=offer.get("description", "") or "",
        url=origine.get("urlOrigine", "") or "",
        posted_date=offer.get("dateCreation"),
        raw=_minimized_raw(offer),
    )


class FranceTravailConnector(Connector):
    """Interroge l'endpoint search avec pagination, 200/206, throttling et isolation."""

    source = "france_travail"

    def __init__(
        self,
        auth: FranceTravailAuth,
        session: requests.Session | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
        rate_limiter: RateLimiter | None = None,
        user_agent: str = _USER_AGENT,
        debug: bool = False,
    ) -> None:
        super().__init__()
        self._auth = auth
        self._session = session or requests.Session()
        self._page_size = page_size
        self._rate = rate_limiter or RateLimiter()
        self._user_agent = user_agent
        # debug=True : corps d'erreur COMPLET dans les messages ; sinon extrait borné.
        self._debug = debug

    def _params(self, criteria: SearchCriteria, start: int, end: int) -> dict[str, Any]:
        params: dict[str, Any] = {"range": f"{start}-{end}"}
        if criteria.mots_cles:
            params["motsCles"] = criteria.mots_cles
        # Filtre géographique EXCLUSIF (commune+rayon OU departement, jamais les deux) :
        # la règle déterministe vit dans SearchCriteria.resolve_geo (l'avertissement éventuel
        # est consommé côté CLI). On envoie `departement` tel quel (virgule gérée par l'API).
        geo, _ = criteria.resolve_geo()
        params.update(geo)
        if criteria.min_creation_date:
            params["minCreationDate"] = criteria.min_creation_date
        if criteria.max_creation_date:
            params["maxCreationDate"] = criteria.max_creation_date
        return params

    def _http_error_detail(
        self, resp: requests.Response, params: dict[str, Any]
    ) -> str:
        """Message d'erreur HTTP enrichi (statut + URL + params + corps), via le helper partagé.

        Aucun secret : `params` ne contient que les critères de recherche ; le token n'est que
        dans les en-têtes, jamais inclus ici.
        """
        return http_error_detail(resp, SEARCH_URL, params, debug=self._debug)

    def fetch(self, criteria: SearchCriteria) -> list[RawJob]:
        """Récupère les offres en paginant. Ne lève jamais : renvoie le partiel sur erreur."""
        self.errors = []
        collected: list[RawJob] = []
        start = 0

        while start < criteria.max_results:
            end = min(start + self._page_size, criteria.max_results) - 1
            params = self._params(criteria, start, end)
            try:
                self._rate.wait()  # throttling ≤ 4 req/s
                token = self._auth.get_token()
                resp = self._session.get(
                    SEARCH_URL,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "User-Agent": self._user_agent,
                    },
                    timeout=_DEFAULT_TIMEOUT,
                )
            except requests.RequestException as exc:
                # Isolation : on garde le partiel, on note l'erreur, on arrête proprement.
                self.errors.append(f"erreur réseau page {start}-{end} : {exc}")
                break
            except FranceTravailAuthError as exc:
                # Échec d'auth (ex. 400 sur le token) : NON fatal non plus -> partiel + arrêt.
                self.errors.append(f"auth échouée page {start}-{end} : {exc}")
                break

            # 204 : la recherche n'a AUCUN résultat (réponse sans corps) — ce n'est pas
            # une erreur. Constaté sur une vraie collecte (motsCles=« alten »).
            if resp.status_code == 204:
                break
            # 200 ET 206 sont des SUCCÈS ; tout le reste = erreur -> partiel + arrêt.
            # On REMONTE le corps de la réponse (l'API y décrit le paramètre fautif), l'URL
            # et les PARAMS envoyés — sans secret (le token vit dans les en-têtes, non loggés).
            if resp.status_code not in (200, 206):
                self.errors.append(self._http_error_detail(resp, params))
                break

            try:
                payload = resp.json()
            except ValueError as exc:
                self.errors.append(f"réponse JSON invalide page {start}-{end} : {exc}")
                break

            results = payload.get("resultats") or []
            for offer in results:
                collected.append(map_offer_to_rawjob(offer))

            # Fin de pagination : 200 (contenu complet), page vide, ou page non pleine.
            if resp.status_code == 200 or not results or len(results) < self._page_size:
                break
            start += self._page_size

        return collected
