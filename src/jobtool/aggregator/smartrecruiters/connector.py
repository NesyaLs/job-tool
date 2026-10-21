"""Connecteur SmartRecruiters (Posting API publique) — synchrone, isolé, throttlé.

Endpoints (lecture seule, SANS clé ni auth) :
  - LISTE  : GET {base}/{company}/postings?limit=&offset=&q=&country=&region=&city=&department=
             &releasedAfter=   -> { offset, limit, totalFound, content:[...] }
  - DÉTAIL : GET {base}/{company}/postings/{id}  -> reprend la liste + jobAd.sections,
             postingUrl, applyUrl.
La LISTE ne contient NI la description NI l'URL de candidature -> un appel DÉTAIL par offre
(N+1), borné par `criteria.max_results`. Si un détail échoue, l'offre est IGNORÉE (décision
projet) : on note l'erreur et on continue, sans planter.

PARTICULARITÉS gérées :
  - `limit` plafonné à 100 par l'API -> on borne le page_size ;
  - pagination par `offset` : on avance de len(content), on s'arrête si `content` est vide
    (garde-fou anti-boucle) OU si `offset >= totalFound` ;
  - une ENTREPRISE par appel : on itère sur la liste des `companies` configurées ;
  - géo SmartRecruiters (country ISO2 / region / city / department=ID numérique) NON
    transposable depuis les codes INSEE de SearchCriteria -> ces filtres viennent de la config
    propre à la source ; depuis SearchCriteria on ne réutilise que `q` et `releasedAfter` ;
  - 429/5xx -> retry + back-off + Retry-After (helper partagé _http.get_with_retry).

IDENTIFIANT INVALIDE : l'API répond 200 et une liste VIDE pour une entreprise qui n'existe
pas (« Visa », « Bosch » au lieu de « BoschGroup ») — exactement comme pour une entreprise
sans offre. Une première page vide est donc vérifiée par un appel SANS filtre : vide lui
aussi, l'identifiant est signalé comme probablement invalide au lieu de passer en silence.

ISOLATION : réseau, HTTP non-200, JSON invalide, ou AUCUNE entreprise configurée -> on note
dans `self.errors` et on renvoie le PARTIEL (jamais d'exception ; n'interrompt pas les autres
connecteurs). MINIMISATION : `raw` = LISTE BLANCHE (on EXCLUT `creator`, donnée personnelle).

Le connecteur produit des `RawJob` ; la normalisation (hash/dédup) reste dans normalize.py.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import requests

from jobtool.aggregator._http import RateLimiter, get_with_retry, http_error_detail
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.html_clean import html_to_text
from jobtool.aggregator.models import RawJob, Remote, SearchCriteria

__all__ = ["BASE_URL", "SmartRecruitersConnector", "map_posting_to_rawjob"]

BASE_URL = "https://api.smartrecruiters.com/v1/companies"
MAX_REQUESTS_PER_SECOND = (
    5  # conservateur : limites publiques non documentées (configurable)
)
_API_MAX_LIMIT = 100  # l'API plafonne `limit` à 100
_DEFAULT_PAGE_SIZE = 100
_DEFAULT_TIMEOUT = 20
_USER_AGENT = "jobtool-aggregator/0.1 (+personal job search tool)"

# Ordre logique des sections de description (clés de jobAd.sections).
_SECTION_ORDER = (
    "companyDescription",
    "jobDescription",
    "qualifications",
    "additionalInformation",
)


def _iter_sections(jobad: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    """Itère les sections de description, que `sections` soit un dict nommé ou une liste.

    Défensif : structures inattendues -> aucune section (description vide), jamais d'erreur.
    """
    sections = (jobad or {}).get("sections")
    if isinstance(sections, dict):
        for key in _SECTION_ORDER:
            sec = sections.get(key)
            if isinstance(sec, dict):
                yield sec
    elif isinstance(sections, list):
        for sec in sections:
            if isinstance(sec, dict):
                yield sec


def _sections_to_text(jobad: dict[str, Any] | None) -> str:
    """Concatène les sections (HTML -> texte), dans l'ordre logique, sections vides ignorées."""
    parts = [html_to_text(sec.get("text")) for sec in _iter_sections(jobad)]
    return "\n\n".join(p for p in parts if p)


def _map_location(location: dict[str, Any]) -> str:
    """Lieu lisible : `fullLocation` si présent, sinon « city, region, country » (parties non vides)."""
    full = location.get("fullLocation")
    if full:
        return str(full)
    parts = [location.get("city"), location.get("region"), location.get("country")]
    return ", ".join(str(p) for p in parts if p)


def _map_remote(location: dict[str, Any]) -> Remote:
    """remote=True -> « oui » ; sinon hybrid=True -> « hybride » ; remote=False -> « non » ; sinon inconnu."""
    remote = location.get("remote")
    hybrid = location.get("hybrid")
    if remote is True:
        return "oui"
    if hybrid is True:
        return "hybride"
    if remote is False:
        return "non"
    return "inconnu"


def _label(obj: Any) -> str:
    """`{... , label}` -> label (str) ; défensif (None/structure inattendue -> "")."""
    if isinstance(obj, dict):
        return str(obj.get("label") or "")
    return ""


def _minimized_raw(posting: dict[str, Any]) -> dict[str, Any]:
    """LISTE BLANCHE des champs conservés dans `raw` (aucune donnée personnelle : pas de `creator`)."""
    company = posting.get("company") or {}
    location = posting.get("location") or {}
    return {
        "id": posting.get("id"),
        "uuid": posting.get("uuid"),
        "refNumber": posting.get("refNumber"),
        "ref": posting.get("ref"),
        "releasedDate": posting.get("releasedDate"),
        "company": {
            "identifier": company.get("identifier"),
            "name": company.get("name"),
        },
        "location": {
            "city": location.get("city"),
            "region": location.get("region"),
            "country": location.get("country"),
            "postalCode": location.get(
                "postalCode"
            ),  # de l'établissement, pas d'une personne
            "remote": location.get("remote"),
            "hybrid": location.get("hybrid"),
        },
        "industry": {"label": _label(posting.get("industry"))},
        "department": {"label": _label(posting.get("department"))},
        "function": {"label": _label(posting.get("function"))},
        "typeOfEmployment": {
            "id": (posting.get("typeOfEmployment") or {}).get("id"),
            "label": _label(posting.get("typeOfEmployment")),
        },
        "experienceLevel": {"label": _label(posting.get("experienceLevel"))},
        "postingUrl": posting.get("postingUrl"),
        "applyUrl": posting.get("applyUrl"),
        "language": posting.get("language"),
    }


def map_posting_to_rawjob(detail: dict[str, Any], company_fallback: str) -> RawJob:
    """Mappe un posting DÉTAILLÉ SmartRecruiters vers un RawJob unifié.

    `detail` est l'objet du détail (superset de l'item de liste). `company_fallback` est
    l'identifiant d'entreprise interrogé, utilisé si `company.name` est absent.
    """
    company = detail.get("company") or {}
    location = detail.get("location") or {}
    return RawJob(
        source="smartrecruiters",
        native_id=str(detail.get("id") or ""),
        title=detail.get("name") or "",
        company=company.get("name") or company_fallback,
        location=_map_location(location),
        remote=_map_remote(location),
        contract_type=_label(detail.get("typeOfEmployment")),
        salary=None,  # non exposé par la Posting API
        description=_sections_to_text(detail.get("jobAd")),
        url=detail.get("applyUrl") or detail.get("postingUrl") or "",
        posted_date=detail.get("releasedDate"),
        raw=_minimized_raw(detail),
    )


class SmartRecruitersConnector(Connector):
    """Interroge la Posting API (liste + détail) par entreprise, avec pagination et isolation."""

    source = "smartrecruiters"
    uses_search = False  # catalogue des entreprises configurées, pas une recherche

    def __init__(
        self,
        companies: list[str],
        country: str | None = None,
        region: str | None = None,
        city: str | None = None,
        department: str | None = None,
        max_per_company: int | None = None,
        session: requests.Session | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        user_agent: str = _USER_AGENT,
        debug: bool = False,
    ) -> None:
        super().__init__()
        self._companies = [c for c in (companies or []) if c]
        self._country = country
        self._region = region
        self._city = city
        self._department = department
        self._max_per_company = max_per_company
        self._session = session or requests.Session()
        self._page_size = min(page_size, _API_MAX_LIMIT)  # API plafonne à 100
        self._rate = rate_limiter or RateLimiter(max_per_second=MAX_REQUESTS_PER_SECOND)
        self._sleep = sleep  # injectable : prouve le back-off sans attente réelle
        self._user_agent = user_agent
        self._debug = debug

    # --- en-têtes / urls / params -------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "User-Agent": self._user_agent}

    def _list_url(self, company: str) -> str:
        return f"{BASE_URL}/{company}/postings"

    def _detail_url(self, company: str, posting_id: str) -> str:
        return f"{BASE_URL}/{company}/postings/{posting_id}"

    def _list_params(self, criteria: SearchCriteria, offset: int) -> dict[str, Any]:
        """Query params de la LISTE. Aucun secret (API publique) -> remontables tels quels."""
        params: dict[str, Any] = {"limit": self._page_size, "offset": offset}
        if criteria.mots_cles:
            params["q"] = criteria.mots_cles
        if criteria.min_creation_date:
            params["releasedAfter"] = criteria.min_creation_date
        # Filtres propres à SmartRecruiters (depuis la config de la source).
        if self._country:
            params["country"] = self._country
        if self._region:
            params["region"] = self._region
        if self._city:
            params["city"] = self._city
        if self._department:
            params["department"] = (
                self._department
            )  # ID numérique (le label -> HTTP 400)
        return params

    def _get(self, url: str, params: dict[str, Any] | None) -> requests.Response:
        """GET throttlé + retry 429/5xx (helper partagé). Les RequestException remontent."""
        return get_with_retry(
            self._session,
            url,
            params=params,
            headers=self._headers(),
            timeout=_DEFAULT_TIMEOUT,
            rate_limiter=self._rate,
            sleep=self._sleep,
        )

    # --- détail d'une offre --------------------------------------------------
    def _fetch_detail(self, company: str, item: dict[str, Any]) -> RawJob | None:
        """Récupère + mappe le DÉTAIL d'une offre. Échec -> None (offre ignorée) + erreur notée."""
        posting_id = str(item.get("id") or "")
        if not posting_id:
            self.errors.append(f"{company} : offre sans id dans la liste, ignorée")
            return None
        url = self._detail_url(company, posting_id)
        try:
            resp = self._get(url, None)
        except requests.RequestException as exc:
            self.errors.append(f"détail {company}/{posting_id} : erreur réseau : {exc}")
            return None
        if resp.status_code != 200:
            self.errors.append(http_error_detail(resp, url, {}, debug=self._debug))
            return None
        try:
            detail = resp.json()
        except ValueError as exc:
            self.errors.append(f"détail {company}/{posting_id} : JSON invalide : {exc}")
            return None
        return map_posting_to_rawjob(detail, company_fallback=company)

    # --- pagination d'une entreprise ----------------------------------------
    def _looks_invalid(self, company: str) -> bool:
        """Vrai si l'entreprise n'a AUCUNE offre, même sans filtre : identifiant suspect.

        Un appel de plus, mais seulement quand la première page filtrée est vide. Une
        erreur réseau ici ne conclut rien : on ne signale que ce qu'on a constaté.
        """
        url = self._list_url(company)
        try:
            resp = self._get(url, {"limit": 1, "offset": 0})
            payload = resp.json() if resp.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        return not payload.get("content") and not payload.get("totalFound")

    def _filtered(self, criteria: SearchCriteria) -> bool:
        """Vrai si la liste est interrogée avec au moins un filtre (mot-clé, géo, date…)."""
        return bool(
            criteria.mots_cles
            or criteria.min_creation_date
            or self._country
            or self._region
            or self._city
            or self._department
        )

    def _fetch_company(
        self, company: str, criteria: SearchCriteria, collected: list[RawJob]
    ) -> None:
        """Pagine la LISTE d'une entreprise et collecte les détails. Ne lève pas (isolation)."""
        offset = 0
        limit = criteria.max_results
        if self._max_per_company is not None:
            limit = min(limit, len(collected) + self._max_per_company)
        while len(collected) < limit:
            params = self._list_params(criteria, offset)
            url = self._list_url(company)
            try:
                resp = self._get(url, params)
            except requests.RequestException as exc:
                self.errors.append(f"{company} : erreur réseau offset {offset} : {exc}")
                return
            if resp.status_code != 200:
                self.errors.append(
                    http_error_detail(resp, url, params, debug=self._debug)
                )
                return
            try:
                payload = resp.json()
            except ValueError as exc:
                self.errors.append(f"{company} : JSON invalide offset {offset} : {exc}")
                return

            content = payload.get("content") or []
            if not content:  # garde-fou anti-boucle : page vide -> stop
                if offset == 0 and (
                    not self._filtered(criteria) or self._looks_invalid(company)
                ):
                    self.errors.append(
                        f"{company} : aucune offre, même sans filtre — identifiant "
                        "SmartRecruiters probablement invalide (vérifie le segment "
                        f"d'URL de https://jobs.smartrecruiters.com/{company})"
                    )
                return
            for item in content:
                if len(collected) >= limit:
                    break
                raw = self._fetch_detail(company, item)
                if raw is not None:
                    collected.append(raw)

            offset += len(content)
            total = payload.get("totalFound")
            if isinstance(total, int) and offset >= total:
                return

    def fetch(self, criteria: SearchCriteria) -> list[RawJob]:
        """Récupère les offres de toutes les entreprises. Ne lève jamais : renvoie le partiel."""
        self.errors = []
        collected: list[RawJob] = []

        if not self._companies:
            self.errors.append(
                "aucune entreprise SmartRecruiters configurée "
                "(section `smartrecruiters.companies` du config.yaml) : source ignorée"
            )
            return collected

        for company in self._companies:
            if len(collected) >= criteria.max_results:
                break
            self._fetch_company(company, criteria, collected)

        return collected[: criteria.max_results]
