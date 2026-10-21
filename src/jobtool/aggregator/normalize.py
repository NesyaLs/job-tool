"""Normalisation de texte, hash de déduplication, et RawJob -> Job.

Déduplication (Phase 1) : `dedup_hash = sha256(norm(company) | norm(title) | norm(location))`.
norm = minuscule, accents retirés, espaces normalisés, trim. Le fuzzy matching inter-sources
est HORS périmètre ici (Phase 2) : deux offres ne fusionnent que si leur triplet normalisé
est IDENTIQUE.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import urlsplit

from typing import Any

from jobtool.aggregator.contracts import normalize_contract, normalize_work_time
from jobtool.aggregator.geo import normalize_location
from jobtool.aggregator.models import Job, JobStatus, RawJob

#: Version des règles de normalisation (lieu, contrat). À INCRÉMENTER quand geo.py ou
#: contracts.py changent de résultat : les offres déjà en base sont alors recalculées à la
#: prochaine collecte (storage.renormalize_catalogue).
NORM_VERSION = 2  # 2 : identité d'URL des liens à routage par fragment (dedup.py)

#: Ports implicites : ne font pas partie de l'identité d'une URL.
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def normalize_text(value: str) -> str:
    """Minuscule + accents retirés + espaces normalisés + trim (clé de comparaison)."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    collapsed = re.sub(r"\s+", " ", without_accents.lower()).strip()
    return collapsed


def compute_dedup_hash(company: str, title: str, location: str) -> str:
    """Empreinte stable d'une offre (company+title+location normalisés)."""
    key = "|".join(normalize_text(part) for part in (company, title, location))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def normalize_url(url: str) -> str:
    """Identité canonique d'une annonce d'après son lien : schéma + hôte + chemin.

    On RETIRE ce qui ne désigne pas l'annonce : query string (paramètres de suivi type
    `utm_source`, identifiants de session), fragment, port implicite, barre oblique finale.
    Le schéma et l'hôte sont ramenés en minuscules (insensibles à la casse par la RFC 3986),
    mais PAS le chemin : `/Offres/A` et `/offres/a` peuvent désigner deux ressources.

    Renvoie "" si l'URL est vide ou inexploitable (pas d'hôte) : une chaîne vide n'est PAS
    une identité — deux offres sans lien ne doivent jamais être confondues.
    """
    if not url or not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    if not parts.scheme or not parts.hostname:
        return ""

    host = parts.hostname.lower()
    port = parts.port
    scheme = parts.scheme.lower()
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"

    path = parts.path.rstrip("/")
    return f"{scheme}://{host}{path}"


def url_native_id(url: str) -> str:
    """Empreinte stable d'une annonce d'après son lien (voir `normalize_url`).

    Sert d'identifiant natif aux offres saisies À LA MAIN, qui n'en ont aucun côté source :
    leur seul identifiant fiable est l'URL. Chaîne vide si l'URL est inexploitable.
    """
    key = normalize_url(url)
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalized_fields(
    source: str,
    title: str,
    location: str,
    contract_type: str,
    raw: dict[str, Any] | None,
) -> dict[str, Any]:
    """Champs normalisés (lieu, contrat, temps de travail) d'une offre, par le code seul."""
    place = normalize_location(source, location, raw)
    return {
        "country": place.country,
        "region": place.region,
        "department": place.department,
        "city": place.city,
        "place_precision": place.precision,
        "contract_kind": normalize_contract(source, contract_type, title, raw),
        "work_time": normalize_work_time(source, contract_type, raw),
    }


def raw_to_job(raw: RawJob, *, fetched_date: str | None = None) -> Job:
    """Convertit un RawJob (source quelconque) en Job unifié + dédupliqué.

    `id` interne = `dedup_hash` (identité canonique). Le statut initial est `new`.
    """
    dedup = compute_dedup_hash(raw.company, raw.title, raw.location)
    return Job(
        **normalized_fields(
            raw.source, raw.title, raw.location, raw.contract_type, raw.raw
        ),
        id=dedup,
        source=raw.source,
        native_id=raw.native_id,
        title=raw.title,
        company=raw.company,
        location=raw.location,
        remote=raw.remote,
        contract_type=raw.contract_type,
        salary=raw.salary,
        description=raw.description,
        url=raw.url,
        posted_date=raw.posted_date,
        fetched_date=fetched_date or _now_iso(),
        dedup_hash=dedup,
        status=JobStatus.new,
        raw=raw.raw,
    )
