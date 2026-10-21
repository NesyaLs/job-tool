"""Connecteur Workday (API CXS publique des sites carrière) — synchrone, isolé, throttlé.

Endpoints (lecture seule, SANS clé ni auth ; cf. workday/urls.py pour le parsing d'URL) :
  - LISTE  : POST {base}/wday/cxs/{tenant}/{site}/jobs
             header OBLIGATOIRE : Content-Type: application/json
             body : {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": "..."}
             -> { total, jobPostings[], facets[], userAuthenticated }
  - DÉTAIL : GET {base}/wday/cxs/{tenant}/{site}{externalPath}  (externalPath commence par /job)
             -> { jobPostingInfo, hiringOrganization, ... }
La LISTE ne contient NI la description, NI la vraie URL de candidature, NI le `jobReqId`, NI la
date ISO -> un appel DÉTAIL par offre (N+1), borné par `criteria.max_results`. Un détail en échec
-> offre IGNORÉE (décision projet) : on note et on continue.

PIÈGES de pagination Workday (vérifiés empiriquement) gérés ici :
  - `limit` plafonné à 20 par l'API (≥21 -> HTTP 400) -> constante _LIMIT ;
  - `total` fiable UNIQUEMENT sur offset=0 (revient à 0 ensuite) -> lu page 0 seulement ;
  - `total` plafonné à 2000 ; offset hors fenêtre NE renvoie ni erreur ni page vide : il REBOUCLE
    sur la page 0 -> GARDE ANTI-CLAMP : une page 100 % doublons (par `externalPath`) arrête la boucle ;
  - arrêt = page vide OU page 100 % doublons OU `offset >= total` (page 0).
Tenant > 2000 offres : non couvert par offset seul — d'où le filtre PAYS ci-dessous, qui ramène
Thales de ~5000 offres à ~1500 en France.

FILTRE PAYS : Workday ne filtre pas par code ISO mais par l'identifiant d'une valeur du facet
`locationCountry`, lisible dans la première page. Si le pays est configuré, on le cherche par
son NOM (« France ») et on relance la liste avec ce facet ; un site qui n'expose pas ce facet
(tout est dans un seul pays, cas de la Banque de France) est interrogé sans filtre.

CONTRAT : `timeType` (« Full time ») est un TEMPS DE TRAVAIL, pas un type de contrat : il reste
dans `raw`, et `contract_type` n'est pas renseigné. Le contrat se lit parfois dans les
`bulletFields` de la liste (« CDI », « Stage »), conservés dans `raw` pour la normalisation.

ISOLATION : réseau, HTTP non-200 (422/404/400/5xx), JSON invalide, URL non Workday, ou AUCUN site
configuré -> on note dans `self.errors` et on renvoie le PARTIEL (jamais d'exception ; n'interrompt
pas les autres connecteurs). 422/404/400 sont TERMINAUX (tenant/site/requête erronés) : on n'insiste pas.
MINIMISATION : `raw` = LISTE BLANCHE (Workday n'expose pas de contact ; on ne garde que l'utile).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import requests

from jobtool.aggregator._http import (
    RateLimiter,
    get_with_retry,
    http_error_detail,
    post_with_retry,
)
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.html_clean import html_to_text
from jobtool.aggregator.models import RawJob, Remote, SearchCriteria
from jobtool.aggregator.workday.urls import (
    WorkdaySite,
    WorkdayUrlError,
    parse_careers_url,
)

__all__ = ["WorkdayConnector", "map_posting_to_rawjob"]

_LIMIT = 20  # l'API plafonne `limit` à 20 (≥21 -> HTTP 400)
_TOTAL_CAP = (
    2000  # `total` plafonné par l'API ; fenêtre offset atteignable = 2000 items
)
MAX_REQUESTS_PER_SECOND = (
    3  # ~0.33 s entre requêtes (poli ; aucun 429 observé mais on reste sage)
)
_DEFAULT_TIMEOUT = 20
_USER_AGENT = "jobtool-aggregator/0.1 (+personal job search tool)"


def _map_locations(info: dict[str, Any]) -> str:
    """« ville principale + villes additionnelles » (parties non vides), depuis le DÉTAIL."""
    locations: list[Any] = [info.get("location")]
    extra = info.get("additionalLocations")
    if isinstance(extra, list):
        locations.extend(extra)
    return ", ".join(str(loc) for loc in locations if loc)


def _map_remote(remote_type: Any) -> Remote:
    """`remoteType` Workday (str libre) -> valeur unifiée. Absent/inconnu -> « inconnu » (défensif)."""
    if not remote_type:
        return "inconnu"
    text = str(remote_type).lower()
    if "remote" in text:
        return "oui"
    if "hybrid" in text or "flex" in text:
        return "hybride"
    if "on-site" in text or "onsite" in text or "on site" in text:
        return "non"
    return "inconnu"


def _minimized_raw(
    detail: dict[str, Any], site: WorkdaySite, listing: dict[str, Any] | None = None
) -> dict[str, Any]:
    """LISTE BLANCHE des champs conservés dans `raw` (provenance + champs utiles, aucun contact)."""
    info = detail.get("jobPostingInfo") or {}
    org = detail.get("hiringOrganization") or {}
    country = info.get("country") or {}
    return {
        "tenant": site.tenant,
        "wd_server": site.wd_server,
        "site": site.site,
        "jobReqId": info.get("jobReqId"),
        "jobPostingId": info.get("jobPostingId"),
        "title": info.get("title"),
        "externalUrl": info.get("externalUrl"),
        "startDate": info.get("startDate"),
        "timeType": info.get("timeType"),
        "remoteType": info.get("remoteType"),
        "location": info.get("location"),
        "additionalLocations": info.get("additionalLocations"),
        "country": {"descriptor": country.get("descriptor")},
        "canApply": info.get("canApply"),
        "company": {"name": org.get("name")},
        "bulletFields": list((listing or {}).get("bulletFields") or []),
    }


def map_posting_to_rawjob(
    detail: dict[str, Any],
    site: WorkdaySite,
    listing: dict[str, Any] | None = None,
) -> RawJob:
    """Mappe un DÉTAIL Workday (+ l'item de listing en repli) vers un RawJob unifié.

    `native_id` = `jobReqId` (ID canonique fiable, tous formats : JR_17954, REQ515750, …) ;
    repli = `externalPath` de la liste si le détail n'a pas de `jobReqId`. `company` retombe sur
    le `tenant` si `hiringOrganization.name` est absent (le tenant ≠ société, mais c'est le meilleur repli).
    """
    info = detail.get("jobPostingInfo") or {}
    org = detail.get("hiringOrganization") or {}
    listing = listing or {}
    native_id = info.get("jobReqId") or listing.get("externalPath") or ""
    return RawJob(
        source="workday",
        native_id=str(native_id),
        title=info.get("title") or listing.get("title") or "",
        company=org.get("name") or site.tenant,
        location=_map_locations(info),
        remote=_map_remote(info.get("remoteType") or listing.get("remoteType")),
        contract_type="",  # `timeType` = temps de travail, pas contrat (voir l'en-tête)
        salary=None,  # non exposé par l'API CXS
        description=html_to_text(info.get("jobDescription")),
        url=info.get("externalUrl") or "",
        posted_date=info.get("startDate"),  # ISO fiable (pas `postedOn`, relatif)
        raw=_minimized_raw(detail, site, listing),
    )


class WorkdayConnector(Connector):
    """Interroge les sites carrière Workday configurés (liste POST + détail GET), avec isolation."""

    source = "workday"
    uses_search = False  # catalogue des entreprises configurées, pas une recherche

    def __init__(
        self,
        sites: list[str],
        country: str | None = None,
        max_per_site: int | None = None,
        session: requests.Session | None = None,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        user_agent: str = _USER_AGENT,
        debug: bool = False,
    ) -> None:
        super().__init__()
        self._site_urls = [s for s in (sites or []) if s]
        self._country = (country or "").strip()
        self._max_per_site = max_per_site
        self._session = session or requests.Session()
        self._rate = rate_limiter or RateLimiter(max_per_second=MAX_REQUESTS_PER_SECOND)
        self._sleep = sleep  # injectable : prouve le back-off sans attente réelle
        self._user_agent = user_agent
        self._debug = debug

    # --- en-têtes / corps ----------------------------------------------------
    def _headers(self) -> dict[str, str]:
        # Content-Type est le SEUL header obligatoire de l'API CXS (sans lui : HTTP 500).
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }

    def _list_body(
        self,
        criteria: SearchCriteria,
        offset: int,
        facets: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        """Corps du POST listing. `searchText` = filtrage plein texte ; `facets` = filtre pays."""
        return {
            "appliedFacets": dict(facets or {}),
            "limit": _LIMIT,
            "offset": offset,
            "searchText": criteria.mots_cles or "",
        }

    # --- requêtes throttlées + retry ----------------------------------------
    def _post_list(self, site: WorkdaySite, body: dict[str, Any]) -> requests.Response:
        return post_with_retry(
            self._session,
            site.list_url(),
            json=body,
            headers=self._headers(),
            timeout=_DEFAULT_TIMEOUT,
            rate_limiter=self._rate,
            sleep=self._sleep,
        )

    def _get_detail(self, site: WorkdaySite, external_path: str) -> requests.Response:
        return get_with_retry(
            self._session,
            site.detail_url(external_path),
            headers=self._headers(),
            timeout=_DEFAULT_TIMEOUT,
            rate_limiter=self._rate,
            sleep=self._sleep,
        )

    # --- détail d'une offre --------------------------------------------------
    def _fetch_detail(
        self, site: WorkdaySite, posting: dict[str, Any]
    ) -> RawJob | None:
        """Récupère + mappe le DÉTAIL d'une offre. Échec -> None (offre ignorée) + erreur notée."""
        external_path = posting.get("externalPath")
        if not external_path:
            self.errors.append(
                f"{site.tenant}/{site.site} : offre sans externalPath dans la liste, ignorée"
            )
            return None
        try:
            resp = self._get_detail(site, str(external_path))
        except requests.RequestException as exc:
            self.errors.append(
                f"détail {site.tenant}{external_path} : erreur réseau : {exc}"
            )
            return None
        if resp.status_code != 200:
            self.errors.append(
                http_error_detail(
                    resp, site.detail_url(str(external_path)), {}, debug=self._debug
                )
            )
            return None
        try:
            detail = resp.json()
        except ValueError as exc:
            self.errors.append(
                f"détail {site.tenant}{external_path} : JSON invalide : {exc}"
            )
            return None
        return map_posting_to_rawjob(detail, site, listing=posting)

    # --- pagination d'un site ------------------------------------------------
    def _country_facet(self, payload: dict[str, Any]) -> dict[str, list[str]] | None:
        """Facet `locationCountry` correspondant au pays configuré, ou None s'il n'existe pas.

        Les facets sont imbriqués (ex. `locationMainGroup` -> `locationCountry`) : on les
        parcourt tous. Comparaison sur le nom, sans casse (« France » / « france »).
        """
        wanted = self._country.casefold()
        stack = list(payload.get("facets") or [])
        while stack:
            facet = stack.pop()
            if not isinstance(facet, dict):
                continue
            param = facet.get("facetParameter")
            for value in facet.get("values") or []:
                if not isinstance(value, dict):
                    continue
                if value.get("facetParameter"):  # sous-groupe : on descend
                    stack.append(value)
                    continue
                descriptor = str(value.get("descriptor") or "").casefold()
                if (
                    param == "locationCountry"
                    and descriptor == wanted
                    and value.get("id")
                ):
                    return {"locationCountry": [str(value["id"])]}
        return None

    def _post_page(
        self,
        site: WorkdaySite,
        criteria: SearchCriteria,
        offset: int,
        facets: dict[str, list[str]] | None,
    ) -> dict[str, Any] | None:
        """Une page de listing, ou None (erreur notée). Ne lève pas (isolation)."""
        body = self._list_body(criteria, offset, facets)
        list_url = site.list_url()
        try:
            resp = self._post_list(site, body)
        except requests.RequestException as exc:
            self.errors.append(
                f"{site.tenant}/{site.site} : erreur réseau offset {offset} : {exc}"
            )
            return None
        if resp.status_code != 200:
            self.errors.append(
                http_error_detail(
                    resp,
                    list_url,
                    {"offset": offset, "limit": _LIMIT},
                    debug=self._debug,
                )
            )
            return None
        try:
            payload = resp.json()
        except ValueError as exc:
            self.errors.append(
                f"{site.tenant}/{site.site} : JSON invalide offset {offset} : {exc}"
            )
            return None
        return payload if isinstance(payload, dict) else {}

    # --- pagination d'un site ------------------------------------------------
    def _fetch_site(
        self, site: WorkdaySite, criteria: SearchCriteria, collected: list[RawJob]
    ) -> None:
        """Pagine le LISTING d'un site (POST) et collecte les détails. Ne lève pas (isolation)."""
        seen_paths: set[str] = set()
        offset = 0
        total: int | None = None
        facets: dict[str, list[str]] | None = None
        limit = criteria.max_results
        if self._max_per_site is not None:
            limit = min(limit, len(collected) + self._max_per_site)
        while len(collected) < limit:
            payload = self._post_page(site, criteria, offset, facets)
            if payload is None:
                return
            if offset == 0 and self._country and facets is None:
                # Première page : on y lit l'identifiant du pays, puis on relance filtré.
                facets = self._country_facet(payload) or {}
                if facets:
                    payload = self._post_page(site, criteria, offset, facets)
                    if payload is None:
                        return

            postings = payload.get("jobPostings") or []
            if not postings:  # page vide -> stop
                return
            if offset == 0:  # `total` fiable UNIQUEMENT ici (sinon revient à 0)
                raw_total = payload.get("total")
                total = raw_total if isinstance(raw_total, int) else None

            # Dédup par externalPath = aussi la GARDE ANTI-CLAMP (offset hors fenêtre reboucle page 0).
            new_postings = [
                p
                for p in postings
                if isinstance(p, dict) and p.get("externalPath") not in seen_paths
            ]
            if not new_postings:  # page 100 % doublons -> clamp détecté -> stop
                return
            for posting in new_postings:
                if len(collected) >= limit:
                    break
                path = posting.get("externalPath")
                if (
                    path
                ):  # path-less posting : non traçable, rejeté au détail de toute façon
                    seen_paths.add(str(path))
                raw = self._fetch_detail(site, posting)
                if raw is not None:
                    collected.append(raw)

            offset += len(postings)  # avance d'une page complète (= _LIMIT)
            if total is not None and offset >= total:
                return

    def fetch(self, criteria: SearchCriteria) -> list[RawJob]:
        """Récupère les offres de tous les sites configurés. Ne lève jamais : renvoie le partiel."""
        self.errors = []
        collected: list[RawJob] = []

        if not self._site_urls:
            self.errors.append(
                "aucun site Workday configuré "
                "(section `workday.sites` du config.yaml) : source ignorée"
            )
            return collected

        for url in self._site_urls:
            if len(collected) >= criteria.max_results:
                break
            try:
                site = parse_careers_url(url)
            except WorkdayUrlError as exc:
                self.errors.append(f"URL Workday ignorée : {exc}")
                continue
            self._fetch_site(site, criteria, collected)

        return collected[: criteria.max_results]
