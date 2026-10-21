"""Parsing d'une URL de site carrière Workday -> identité d'API (CXS).

Une « source Workday » = une URL publique de site carrière :

    https://{tenant}.{wd_server}.myworkdayjobs.com/{locale}/{site}

Règles VÉRIFIÉES (cartographie projet) :
  - `tenant`    = 1er label du host (≠ nom de société : `airliquidehr`, `bdf`) ;
  - `wd_server` = `wd\\d+` — wd1/wd3/wd5 MAIS AUSSI wd10, wd103 -> jamais deviné, lu dans l'URL ;
  - `locale`    = `^[a-z]{2}-[A-Z]{2}$`, OPTIONNELLE dans l'URL et ABSENTE du path API (front-end) ;
  - `site`      = segment après le locale (ou 1er segment) ; ≠ tenant -> fallback `site = tenant`.

Le path API CXS ne contient PAS le locale. On accepte aussi qu'on colle directement une URL CXS
(`/wday/cxs/{tenant}/{site}/...`) : on en réextrait le site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_HOST_RE = re.compile(r"^(?P<tenant>[^.]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com$")
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


class WorkdayUrlError(ValueError):
    """URL de site carrière Workday non reconnue (host hors *.wd<N>.myworkdayjobs.com)."""


@dataclass(frozen=True)
class WorkdaySite:
    """Identité d'un site carrière Workday + fabrique des URL d'API CXS.

    `site` est SENSIBLE À LA CASSE (`NVIDIAExternalCareerSite`) : on ne le normalise pas.
    Le `tenant` vient du host (insensible à la casse) -> minuscule.
    """

    tenant: str
    wd_server: str
    site: str
    locale: str | None = None

    @property
    def base(self) -> str:
        return f"https://{self.tenant}.{self.wd_server}.myworkdayjobs.com"

    @property
    def cxs(self) -> str:
        """Préfixe CXS commun au listing et au détail (SANS locale : absent du path API)."""
        return f"{self.base}/wday/cxs/{self.tenant}/{self.site}"

    def list_url(self) -> str:
        return f"{self.cxs}/jobs"

    def detail_url(self, external_path: str) -> str:
        """URL détail : `external_path` commence déjà par `/job...` -> concaténation directe."""
        return f"{self.cxs}{external_path}"


def parse_careers_url(url: str) -> WorkdaySite:
    """Décompose une URL carrière (ou CXS) en `WorkdaySite`. Lève `WorkdayUrlError` si le host n'est pas Workday."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    match = _HOST_RE.match(host)
    if not match:
        raise WorkdayUrlError(
            f"URL Workday non reconnue : {url!r} "
            "(host attendu : {tenant}.wd<N>.myworkdayjobs.com)"
        )
    tenant = match.group("tenant")
    wd_server = match.group("wd")
    segments = [s for s in parsed.path.split("/") if s]

    # URL CXS collée : /wday/cxs/{tenant}/{site}/...  -> site à l'index 3.
    if len(segments) >= 4 and segments[0] == "wday" and segments[1] == "cxs":
        return WorkdaySite(tenant=tenant, wd_server=wd_server, site=segments[3])

    locale: str | None = None
    if segments and _LOCALE_RE.match(segments[0]):
        locale = segments.pop(0)
    site = segments[0] if segments else tenant  # fallback : site = tenant
    return WorkdaySite(tenant=tenant, wd_server=wd_server, site=site, locale=locale)
