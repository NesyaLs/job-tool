"""Dédoublonnage ENTRE SOURCES : règles pures, sans base ni réseau.

Une même annonce publiée sur plusieurs sources (le site carrière de l'entreprise,
France Travail, Adzuna…) doit apparaître UNE fois. Le stockage relie donc chaque
OCCURRENCE (une source, un identifiant) à une offre CANONIQUE ; ce module décide, pour
une occurrence nouvelle, à quelle offre canonique elle appartient — ou à aucune.

Trois niveaux, du plus sûr au moins sûr :
  1. `identifiant` : même source, même identifiant natif -> c'est la même occurrence, mise
     à jour (décidé par le stockage, qui connaît les occurrences) ;
  2. `url`         : même lien d'offre ou de candidature, une fois normalisé (paramètres de
     suivi retirés, liens connus des ATS ramenés à leur identifiant — sans appel réseau) ;
  3. `approchee`   : même entreprise (normalisée), même lieu, intitulés très proches, dates
     compatibles, contrats compatibles — au-dessus d'un seuil de similarité.

PRUDENCE : fusionner deux offres différentes est pire que laisser passer un doublon. D'où
des conditions CUMULATIVES au niveau 3, et ces refus explicites :
  - entreprise vide (fréquent chez France Travail) : jamais de fusion approchée ;
  - deux occurrences d'une MÊME source : la source dit elle-même qu'elles sont distinctes ;
  - lieux différents, contrats connus différents, dates trop éloignées.

Tout est DÉTERMINISTE (« le LLM ne mesure jamais ») : même entrée, même décision, même score.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

#: Niveaux de détection, dans l'ordre de confiance. `nouvelle` : aucune correspondance.
LEVELS = ("identifiant", "url", "approchee", "nouvelle")

#: Seuil de similarité des intitulés au-dessus duquel le niveau 3 fusionne.
FUZZY_THRESHOLD = 0.90
#: Borne basse des « doublons probables » listés pour calibrer le seuil.
PROBABLE_FLOOR = 0.75
#: Écart maximal entre deux dates de publication d'une même annonce.
DATE_WINDOW_DAYS = 21
#: Sans date de part ou d'autre, on exige une similarité plus forte.
UNDATED_THRESHOLD = 0.95

#: Priorité des sources pour les champs de l'offre canonique (plus petit = meilleur) :
#: l'ATS est la source primaire (texte complet, lien direct de candidature) ; France
#: Travail a un texte complet mais souvent sans entreprise ; Adzuna n'a qu'un extrait.
SOURCE_PRIORITY = {
    "smartrecruiters": 0,
    "workday": 0,
    "france_travail": 1,
    "adzuna": 2,
    "manual": 3,
}
_DEFAULT_PRIORITY = 2


def source_priority(source: str) -> int:
    return SOURCE_PRIORITY.get(source, _DEFAULT_PRIORITY)


def _plain(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return plain.replace("œ", "oe").replace("æ", "ae")


# --- Entreprise ----------------------------------------------------------------

#: Formes juridiques et mots de structure, retirés pour comparer les entreprises.
#: « france » n'y figure PAS : « Air France » deviendrait « air », et la prudence prime.
_LEGAL_WORDS = frozenset(
    """sa sas sasu sarl eurl sca sci snc scop scp gie selarl se ste societe
    group groupe holding gmbh ag kg ltd limited inc incorporated llc llp plc bv nv
    spa srl co corp corporation company cie""".split()
)


def company_key(company: str | None) -> str:
    """Entreprise comparable : casse, accents, ponctuation et forme juridique retirés.

    « ALTEN SA », « Alten » et « alten s.a. » -> « alten ». Chaîne vide si rien ne reste.
    """
    words = re.sub(r"[^a-z0-9]+", " ", _plain(company).replace(".", "")).split()
    kept = [w for w in words if w not in _LEGAL_WORDS]
    return " ".join(kept)


# --- Intitulé ------------------------------------------------------------------

#: Marqueurs de genre : « (H/F) », « F/H », « H/F/N », « m/w/d », « (H-F) », « F/H/NB ».
_GENDER = re.compile(
    r"\(?\b[hfmwdnx](?:\s*[/-]\s*(?:[hfmwdnx]|nb)){1,3}\b\)?", flags=re.IGNORECASE
)
#: Mentions de contrat et de durée, qui varient d'une source à l'autre pour une même
#: annonce (« Data Analyst - CDI », « Data Analyst (H/F) »).
_CONTRACT_WORDS = re.compile(
    r"\b(?:cdi|cdd|stage|stagiaire|alternance|alternant|alternante|apprenti|apprentie|"
    r"apprentissage|internship|intern|freelance|interim|temps plein|temps partiel|"
    r"full time|part time|en|de|des|du|d|la|le|les|l|a|au|et|pour|f|h)\b"
)
#: Écriture inclusive : « Alternant(e) », « Ingénieur.e », « développeur·se ».
_INCLUSIVE = re.compile(r"\((?:e|es|ne|se|euse|rice|trice)\)|[.·](?:e|es|ne|se)\b")
_DURATION = re.compile(r"\b\d+\s*(?:mois|months?|ans?|years?|semaines?|weeks?)\b")


def title_key(title: str | None, city: str | None = None) -> str:
    """Intitulé comparable : genre, contrat, durée, ville et ponctuation retirés.

    La ville de l'offre est retirée de son intitulé (« Data Scientist - PARIS ») : deux
    sources ne la mentionnent pas toujours, et le lieu est comparé à part.
    """
    text = _GENDER.sub(" ", _plain(title))
    text = _INCLUSIVE.sub("", text)
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    text = _DURATION.sub(" ", text)
    if city:
        city_words = re.sub(r"[^a-z0-9]+", " ", _plain(city)).strip()
        if city_words:
            text = re.sub(rf"\b{re.escape(city_words)}\b", " ", text)
    text = _CONTRACT_WORDS.sub(" ", text)
    return " ".join(text.split())


def title_similarity(a: str, b: str) -> float:
    """Similarité [0 ; 1] de deux clés d'intitulé, indépendante de l'ordre des mots.

    Ratio de `difflib` sur les mots TRIÉS : « Data Scientist Senior » et « Senior Data
    Scientist » valent 1 ; « Data Scientist » et « Data Scientist Senior » valent 0,8,
    sous le seuil — ce ne sont pas forcément les mêmes postes.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    return round(SequenceMatcher(None, sa, sb).ratio(), 4)


# --- URL -------------------------------------------------------------------------

#: Paramètres de suivi : ils ne désignent pas l'annonce.
_TRACKING = re.compile(
    r"^(?:utm_.*|oga|se|v|source|src|ref|refs|referrer|trk|tracking.*|gclid|fbclid|"
    r"mc_.*|xtor|from|origin|campaign.*|cmp.*|medium|share|shared|lang|locale)$",
    flags=re.IGNORECASE,
)
_SR_POSTING = re.compile(
    r"^(?:jobs|careers)\.smartrecruiters\.com$", flags=re.IGNORECASE
)
_WD_HOST = re.compile(r"^([^.]+)\.wd\d+\.myworkdayjobs\.com$", flags=re.IGNORECASE)
_WD_REQ = re.compile(r"_((?:[A-Za-z]+_?)?\d[\w-]*)$")


def offer_url_key(url: str | None) -> str:
    """Identité d'une annonce d'après un lien — "" si le lien est inexploitable.

    Liens connus, ramenés à l'identifiant de l'annonce (plusieurs formes d'URL mènent à
    la même : lien d'offre, lien de candidature, locale…) :
      - SmartRecruiters : `smartrecruiters:<entreprise>:<id>` ;
      - Workday         : `workday:<tenant>:<réf. de poste>` ;
      - France Travail  : `france_travail:<id>` ;
      - Adzuna          : `adzuna:<id>`.
    Autres liens : schéma + hôte (sans « www. ») + chemin + paramètres NON techniques,
    triés. Aucune redirection n'est suivie (pas d'appel réseau).
    """
    if not url or not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if not parts.scheme or not host:
        return ""
    segments = [s for s in parts.path.split("/") if s]

    if _SR_POSTING.match(host) and len(segments) >= 2:
        posting = re.match(r"^(\d{6,})", segments[1])
        if posting:
            return f"smartrecruiters:{segments[0].lower()}:{posting.group(1)}"
    wd = _WD_HOST.match(host)
    if wd and "job" in segments:
        req = _WD_REQ.search(segments[-1])
        if req:
            return f"workday:{wd.group(1).lower()}:{req.group(1).upper()}"
    if host.endswith("francetravail.fr") or host.endswith("pole-emploi.fr"):
        if segments and segments[-2:-1] == ["detail"]:
            return f"france_travail:{segments[-1].upper()}"
    if host.endswith("adzuna.fr") or host.endswith("adzuna.com"):
        ad = next((s for s in reversed(segments) if s.isdigit()), None)
        if ad:
            return f"adzuna:{ad}"

    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not _TRACKING.match(k)
    )
    path = parts.path.rstrip("/")
    key = f"{host}{path}"
    if query:
        key = f"{key}?{urlencode(query)}"
    # Applications à routage par fragment (« …/p/#/apply/job/u7vl5jk0zr8/… », BEETWEEN) :
    # l'annonce est DANS le fragment. Le retirer confondait toutes les annonces du site
    # (constaté sur une vraie collecte : 9 annonces distinctes, une seule identité).
    if "/" in parts.fragment:
        key = f"{key}#{parts.fragment.rstrip('/')}"
    return key


#: Préfixes des identités d'URL qui désignent une ANNONCE à coup sûr (identifiant de la
#: source dans le lien). Les autres liens peuvent être génériques : la page carrière d'un
#: partenaire de France Travail est parfois la même pour toutes ses offres.
_SPECIFIC_PREFIXES = ("smartrecruiters:", "workday:", "france_travail:", "adzuna:")
#: Similarité d'intitulé exigée quand deux offres partagent un lien NON spécifique : le
#: seuil du niveau 3. Un lien générique (page carrière, lien de suivi d'un diffuseur)
#: n'est qu'un indice ; à 0,6, « Data Analyst - Power BI » et « Data Analyst Banque »
#: fusionnaient (vraie collecte). Prudence : mieux vaut un doublon qu'une fausse fusion.
GENERIC_URL_TITLE_FLOOR = FUZZY_THRESHOLD


def url_match_ok(url_key: str, title_a: str, title_b: str) -> bool:
    """Un lien commun suffit-il à conclure ? Oui s'il désigne une annonce précise ; sinon
    seulement si les intitulés se ressemblent (un lien générique ne prouve rien)."""
    if url_key.startswith(_SPECIFIC_PREFIXES):
        return True
    return title_similarity(title_a, title_b) >= GENERIC_URL_TITLE_FLOOR


def occurrence_url_keys(url: str | None, raw: dict[str, Any] | None) -> set[str]:
    """Toutes les identités d'URL d'une occurrence : son lien, plus ses liens connus.

    France Travail relaie des offres de PARTENAIRES (sites carrière, jobboards) : leur
    lien d'origine est souvent celui de l'ATS, ce qui permet de reconnaître l'annonce
    sans rien deviner. SmartRecruiters expose un lien d'offre ET un lien de candidature.
    """
    raw = raw or {}
    urls = [url]
    urls += [p.get("url") for p in raw.get("partenaires") or [] if isinstance(p, dict)]
    urls += [raw.get("postingUrl"), raw.get("applyUrl"), raw.get("externalUrl")]
    return {k for k in (offer_url_key(u) for u in urls if u) if k}


def new_offer_id(source: str, native_id: str) -> str:
    """Identifiant d'une offre canonique créée pour une occurrence (même format que
    `dedup_hash` : 16 caractères hexadécimaux)."""
    return hashlib.sha256(f"{source}|{native_id}".encode()).hexdigest()[:16]


# --- Niveau 3 --------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """Ce que le niveau 3 compare d'une offre (ou d'une occurrence)."""

    company_key: str
    title_key: str
    department: str | None
    city: str | None
    posted_date: str | None
    contract_kind: str
    sources: frozenset[str]


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


#: Familles de contrat qu'une source peut nommer l'une pour l'autre : le « contract »
#: d'Adzuna couvre le CDD comme la mission d'intérim.
_COMPATIBLE_CONTRACTS = {frozenset({"cdd", "interim"})}


def contracts_compatible(a: str, b: str) -> bool:
    """Deux familles de contrat peuvent-elles décrire la même annonce ?

    Inconnu d'un côté : oui (rien ne s'y oppose). Connues des deux côtés : seulement
    égales, ou nommées l'une pour l'autre par les sources (CDD / intérim). Un stage et un
    CDI au même intitulé sont deux postes.
    """
    if a in ("inconnu", "") or b in ("inconnu", "") or a == b:
        return True
    return frozenset({a, b}) in _COMPATIBLE_CONTRACTS


def fuzzy_score(a: Profile, b: Profile) -> float | None:
    """Score de similarité si les deux offres PEUVENT être la même, sinon None.

    None dit « jamais, quel que soit l'intitulé » (entreprise, lieu, contrat, source ou
    dates incompatibles) ; un score dit « comparables », à comparer au seuil.
    """
    if not a.company_key or a.company_key != b.company_key:
        return None
    if a.sources & b.sources:
        return None  # une même source les distingue elle-même
    if a.department is None or b.department is None or a.department != b.department:
        return None
    if a.city and b.city and _plain(a.city) != _plain(b.city):
        return None
    if not contracts_compatible(a.contract_kind, b.contract_kind):
        return None
    da, db = _day(a.posted_date), _day(b.posted_date)
    if da and db and abs((da - db).days) > DATE_WINDOW_DAYS:
        return None
    return title_similarity(a.title_key, b.title_key)


def threshold_for(a: Profile, b: Profile) -> float:
    """Seuil applicable : plus exigeant quand une date manque (rien ne borne l'écart)."""
    if _day(a.posted_date) and _day(b.posted_date):
        return FUZZY_THRESHOLD
    return UNDATED_THRESHOLD


def best_match(
    profile: Profile, candidates: list[tuple[str, Profile]]
) -> tuple[str, float] | None:
    """Meilleure offre canonique au-dessus du seuil, ou None. Égalité : le plus petit id
    (déterministe)."""
    best: tuple[str, float] | None = None
    for offer_id, other in candidates:
        score = fuzzy_score(profile, other)
        if score is None or score < threshold_for(profile, other):
            continue
        if best is None or score > best[1] or (score == best[1] and offer_id < best[0]):
            best = (offer_id, score)
    return best


# --- Offre canonique -------------------------------------------------------------


def best_fields(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    """Champs de l'offre canonique, pris aux occurrences selon la priorité des sources.

    Chaque champ vient de la meilleure source qui le RENSEIGNE (une entreprise vide chez
    France Travail est prise à l'ATS) ; le lieu, de la source qui le connaît le plus
    précisément ; la date de publication est la PLUS ANCIENNE (première parution). Tous
    les liens restent sur les occurrences.
    """
    ordered = sorted(
        occurrences,
        key=lambda o: (
            source_priority(o["source"]),
            o.get("first_seen_at") or "",
            o["source"],
            o["native_id"],
        ),
    )
    lead = ordered[0]

    def first(field: str, empty: tuple = ("", None)) -> Any:
        for occ in ordered:
            if occ.get(field) not in empty:
                return occ[field]
        return lead.get(field)

    precision_rank = {
        "commune": 0,
        "commune_homonyme": 1,
        "departement": 2,
        "region": 3,
        "pays": 4,
        "inconnu": 5,
    }
    place = min(
        ordered,
        key=lambda o: precision_rank.get(o.get("place_precision") or "inconnu", 5),
    )
    contract = next(
        (o for o in ordered if o.get("contract_kind") not in ("inconnu", "", None)),
        lead,
    )
    dates = sorted(o["posted_date"] for o in ordered if o.get("posted_date"))
    return {
        "source": lead["source"],
        "native_id": lead["native_id"],
        "title": lead.get("title") or first("title"),
        "company": first("company"),
        "location": place.get("location") or first("location"),
        "country": place.get("country"),
        "region": place.get("region"),
        "department": place.get("department"),
        "city": place.get("city"),
        "place_precision": place.get("place_precision") or "inconnu",
        "remote": first("remote", ("", None, "inconnu")),
        "contract_type": contract.get("contract_type") or first("contract_type"),
        "contract_kind": contract.get("contract_kind") or "inconnu",
        "work_time": first("work_time"),
        "salary": first("salary", ("", None, {})),
        "description": first("description"),
        "url": first("url"),
        "posted_date": dates[0] if dates else None,
        "raw": lead.get("raw") or {},
    }
