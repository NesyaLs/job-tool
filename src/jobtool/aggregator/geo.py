"""Normalisation des LIEUX : pays, région, département, ville — pour toutes les sources.

DÉTERMINISTE et HORS LIGNE (« le LLM ne mesure jamais ») : un référentiel des communes
françaises est embarqué dans le paquet (`geo_data/`, généré par
`scripts/build_geo_referential.py` depuis geo.api.gouv.fr). Même entrée -> même lieu.

Chaque source décrit le lieu à sa façon, d'où une lecture par source avant un repli
commun sur le texte libre :
  - France Travail : code INSEE de la commune (`lieuTravail.commune`) — le plus sûr ;
  - Adzuna         : hiérarchie `area` = [pays, région, département, …, ville] ;
  - SmartRecruiters: `city` / `region` / `country` (ISO2) / `postalCode` ;
  - Workday        : un nom de ville (« PARIS », « Elancourt ») + le pays en toutes lettres ;
  - saisie manuelle et replis : « 75 - Paris », « Lyon (69) », « 69003 Lyon », « Nantes ».

HOMONYMES : « Saint-Denis » existe dans l'Aude, le Gard, la Seine-Saint-Denis et à La
Réunion. Sans autre indice (code postal, département), on retient la commune la PLUS
PEUPLÉE et on le trace (`precision = "commune_homonyme"`), pour que le choix reste
explicable et qu'on puisse le filtrer si besoin.

`precision` dit jusqu'où le lieu est connu : `commune`, `commune_homonyme`, `departement`,
`region`, `pays` ou `inconnu`. Une offre `inconnu` (ou seulement `pays`) n'a pas de lieu
exploitable pour un filtre par département.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

FRANCE = "FR"

#: Degrés de précision, du plus fin au plus grossier.
PRECISIONS = (
    "commune",
    "commune_homonyme",
    "departement",
    "region",
    "pays",
    "inconnu",
)


@dataclass(frozen=True)
class Place:
    """Lieu normalisé. Tous les champs sont facultatifs : les sources sont incomplètes."""

    country: str | None = None  # ISO 3166-1 alpha-2, majuscules (« FR »)
    region: str | None = None  # nom officiel (« Île-de-France »)
    department: str | None = None  # code INSEE (« 75 », « 2A », « 974 »)
    city: str | None = None  # nom officiel de la commune, ou nom tel que fourni
    precision: str = "inconnu"


UNKNOWN = Place()


# --- Clés de comparaison ------------------------------------------------------


def place_key(text: str | None) -> str:
    """Clé de comparaison d'un nom de lieu : sans casse, accents, tirets ni apostrophes.

    « ST SAUVEUR VILLAGES », « Saint-Sauveur-Villages » et « saint sauveur villages »
    donnent la même clé. Les abréviations « St » / « Ste » sont développées.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    plain = plain.replace("œ", "oe").replace("æ", "ae")
    words = re.sub(r"[^a-z0-9]+", " ", plain).split()
    expanded = [
        {"st": "saint", "ste": "sainte", "sts": "saints"}.get(w, w) for w in words
    ]
    return " ".join(expanded)


#: « Paris 14e Arrondissement », « Lyon 3e », « Marseille 1er » -> la commune.
_ARRONDISSEMENT = re.compile(
    r"^(paris|lyon|marseille)(?: \d{1,2}(?: ?(?:e|er|eme))?)(?: arrondissement)?$"
)


# --- Référentiel embarqué -----------------------------------------------------


@dataclass(frozen=True)
class _Referential:
    regions: dict[str, str]  # code -> nom
    departments: dict[str, tuple[str, str]]  # code -> (nom, code région)
    department_by_name: dict[str, str]  # clé -> code
    region_by_name: dict[str, str]  # clé -> nom officiel
    communes: dict[str, list[tuple[str, str, int]]]  # clé -> [(nom, dept, population)]
    postcodes: dict[str, str]  # code postal -> département (commune la plus peuplée)


#: Appellations courantes des régions, en plus des noms officiels.
_REGION_ALIASES = {
    "idf": "Île-de-France",
    "ile de france": "Île-de-France",
    "paca": "Provence-Alpes-Côte d'Azur",
    "aura": "Auvergne-Rhône-Alpes",
    "centre": "Centre-Val de Loire",
}


def _read_csv(name: str) -> list[dict[str, str]]:
    text = files("jobtool.aggregator").joinpath("geo_data", name).read_text("utf-8")
    return list(csv.DictReader(text.splitlines()))


@cache
def _referential() -> _Referential:
    """Charge le référentiel une fois par processus (≈ 35 000 communes, < 0,2 s)."""
    regions = {r["code"]: r["nom"] for r in _read_csv("regions.csv")}
    departments = {
        d["code"]: (d["nom"], d["region"]) for d in _read_csv("departements.csv")
    }
    department_by_name = {
        place_key(nom): code for code, (nom, _) in departments.items()
    }
    region_by_name = {place_key(nom): nom for nom in regions.values()}
    region_by_name.update({place_key(k): v for k, v in _REGION_ALIASES.items()})

    communes: dict[str, list[tuple[str, str, int]]] = {}
    postcodes: dict[str, tuple[str, int]] = {}
    for row in _read_csv("communes.csv"):
        dept, pop = row["departement"], int(row["population"] or 0)
        communes.setdefault(place_key(row["nom"]), []).append((row["nom"], dept, pop))
        for cp in filter(None, row["codes_postaux"].split(";")):
            if cp not in postcodes or postcodes[cp][1] < pop:
                postcodes[cp] = (dept, pop)
    for homonyms in communes.values():
        homonyms.sort(key=lambda c: -c[2])  # la plus peuplée d'abord
    return _Referential(
        regions=regions,
        departments=departments,
        department_by_name=department_by_name,
        region_by_name=region_by_name,
        communes=communes,
        postcodes={cp: dept for cp, (dept, _) in postcodes.items()},
    )


def region_of(department: str | None) -> str | None:
    """Nom de la région d'un département (« 92 » -> « Île-de-France »)."""
    ref = _referential()
    entry = ref.departments.get(department or "")
    return ref.regions.get(entry[1]) if entry else None


def department_name(code: str | None) -> str | None:
    """Nom d'un département (« 92 » -> « Hauts-de-Seine »)."""
    entry = _referential().departments.get(code or "")
    return entry[0] if entry else None


def known_department(code: str | None) -> str | None:
    """Le code s'il désigne un département existant, normalisé (« 2a » -> « 2A »)."""
    if not code:
        return None
    code = code.strip().upper()
    if len(code) == 1 and code.isdigit():
        code = "0" + code
    return code if code in _referential().departments else None


def department_from_insee(commune_code: str | None) -> str | None:
    """Département d'un code INSEE de commune (« 92026 » -> « 92 », « 97302 » -> « 973 »)."""
    code = (commune_code or "").strip().upper()
    if len(code) != 5:
        return None
    return known_department(code[:3] if code.startswith("97") else code[:2])


def department_from_postcode(postcode: str | None) -> str | None:
    """Département d'un code postal. Le référentiel tranche la Corse (20xxx) et l'outre-mer."""
    cp = (postcode or "").strip()
    if not re.fullmatch(r"\d{5}", cp):
        return None
    found = _referential().postcodes.get(cp)
    if found:
        return found
    if cp.startswith("97"):
        return known_department(cp[:3])
    return known_department(cp[:2]) if not cp.startswith("20") else None


# --- Pays ---------------------------------------------------------------------

#: Noms de pays (français et anglais, tels que les sources les écrivent) -> ISO2.
_COUNTRIES = {
    "france": "FR",
    "belgique": "BE",
    "belgium": "BE",
    "suisse": "CH",
    "switzerland": "CH",
    "luxembourg": "LU",
    "allemagne": "DE",
    "germany": "DE",
    "deutschland": "DE",
    "espagne": "ES",
    "spain": "ES",
    "italie": "IT",
    "italy": "IT",
    "portugal": "PT",
    "royaume uni": "GB",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "irlande": "IE",
    "ireland": "IE",
    "pays bas": "NL",
    "netherlands": "NL",
    "etats unis": "US",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "canada": "CA",
    "monaco": "MC",
    "india": "IN",
    "inde": "IN",
    "singapore": "SG",
    "australia": "AU",
    "poland": "PL",
    "pologne": "PL",
    "romania": "RO",
    "roumanie": "RO",
    "morocco": "MA",
    "maroc": "MA",
    "tunisia": "TN",
    "tunisie": "TN",
    "mexico": "MX",
    "philippines": "PH",
    "finland": "FI",
    "sweden": "SE",
}


def country_code(text: str | None) -> str | None:
    """ISO2 d'un pays écrit en toutes lettres ou déjà en code (« fr », « France »)."""
    if not text:
        return None
    raw = text.strip()
    named = _COUNTRIES.get(place_key(raw))  # avant le code : « UK » se dit « GB »
    if named:
        return named
    return raw.upper() if re.fullmatch(r"[A-Za-z]{2}", raw) else None


# --- Résolution ---------------------------------------------------------------


def _commune(name: str | None, department: str | None = None) -> Place | None:
    """Commune française par son nom (et son département s'il est connu)."""
    key = place_key(name)
    if not key:
        return None
    m = _ARRONDISSEMENT.match(key)
    if m:
        key = m.group(1)
    communes = _referential().communes
    homonyms = communes.get(key)
    if not homonyms and department:
        # France Travail omet l'article : « 78 - Mureaux » (Les Mureaux), « 92 - Plessis-Robinson ».
        for article in ("les ", "le ", "la ", "l "):
            homonyms = communes.get(article + key)
            if homonyms:
                break
    if not homonyms:
        return None
    if department:
        homonyms = [c for c in homonyms if c[1] == department]
        if not homonyms:
            return None
    nom, dept, _ = homonyms[0]
    precision = "commune" if len(homonyms) == 1 else "commune_homonyme"
    return Place(FRANCE, region_of(dept), dept, nom, precision)


def _from_department(dept: str | None, city: str | None = None) -> Place | None:
    """Lieu connu au département près ; précisé à la commune si `city` y est trouvée."""
    dept = known_department(dept)
    if not dept:
        return None
    if city and place_key(city) == place_key(department_name(dept)):
        city = None  # « 95 - Val-d'Oise » : c'est le département, pas une ville
    if dept == "75" and not city:  # Paris est à la fois commune et département
        city = "Paris"
    precise = _commune(city, dept) if city else None
    if precise is not None:
        return precise
    return Place(FRANCE, region_of(dept), dept, _clean_city(city), "departement")


def _clean_city(city: str | None) -> str | None:
    """Nom de ville tel que fourni, rendu lisible (« NEUILLY SUR SEINE » -> casse titre)."""
    text = (city or "").strip()
    if not text:
        return None
    return text.title() if text.isupper() else text


def _region_only(name: str | None) -> Place | None:
    region = _referential().region_by_name.get(place_key(name))
    return Place(FRANCE, region, None, None, "region") if region else None


def _department_by_name(name: str | None) -> str | None:
    return _referential().department_by_name.get(place_key(name))


_DEPT_PREFIX = re.compile(r"^\s*(\d{2,3}|2[abAB])\s*-\s*(.*)$")
_DEPT_PAREN = re.compile(r"\((\d{2,3}|2[abAB])\)")
_POSTCODE = re.compile(r"\b(\d{5})\b")


def parse_free_text(location: str | None) -> Place:
    """Lieu décrit en texte libre (saisie manuelle, replis de toutes les sources).

    Ordre, du plus sûr au moins sûr : préfixe « NN - Ville » (format France Travail),
    code postal, « (NN) », puis chaque morceau séparé par des virgules comme commune,
    département, région ou pays.
    """
    text = (location or "").strip()
    if not text:
        return UNKNOWN

    m = _DEPT_PREFIX.match(text)
    if m:
        code, rest = m.group(1), m.group(2).strip()
        if code == "99":  # code « étranger » de France Travail : « 99 - Belgique »
            iso = country_code(rest)
            return Place(iso, None, None, None, "pays") if iso else UNKNOWN
        found = _from_department(code, rest or None)
        if found is not None:
            return found

    m = _POSTCODE.search(text)
    if m:
        dept = department_from_postcode(m.group(1))
        city = _POSTCODE.sub(" ", text).strip(" ,-") or None
        found = _from_department(dept, city)
        if found is not None:
            return found

    m = _DEPT_PAREN.search(text)
    if m:
        city = _DEPT_PAREN.sub(" ", text).strip(" ,-") or None
        found = _from_department(m.group(1), city)
        if found is not None:
            return found

    # « Homeworker - UK », « Paris, France » : virgules, barres et tirets ENTOURÉS
    # d'espaces (« Saint-Denis » garde les siens).
    parts = [p.strip() for p in re.split(r"[,;/|]|\s-\s", text) if p.strip()]
    country = None
    for part in parts:
        country = country or country_code(part)
    if country not in (None, FRANCE):
        return Place(country, None, None, _clean_city(parts[0]), "pays")
    for part in parts:
        found = _commune(part)
        if found is not None:
            return found
    for part in parts:
        dept = _department_by_name(part)
        if dept:
            return Place(FRANCE, region_of(dept), dept, None, "departement")
    for part in parts:
        found = _region_only(part)
        if found is not None:
            return found
    if country == FRANCE:
        return Place(FRANCE, None, None, None, "pays")
    return UNKNOWN


# --- Par source ---------------------------------------------------------------


def _france_travail(location: str, raw: dict[str, Any]) -> Place:
    lieu = raw.get("lieuTravail") or {}
    libelle = str(lieu.get("libelle") or location or "")
    m = _DEPT_PREFIX.match(libelle)
    city_hint = m.group(2).strip() if m else None
    dept = department_from_insee(lieu.get("commune")) or department_from_postcode(
        lieu.get("codePostal")
    )
    if dept:
        found = _from_department(dept, city_hint)
        if found is not None:
            return found
    return parse_free_text(libelle)


def _adzuna(location: str, raw: dict[str, Any]) -> Place:
    area = [str(a) for a in ((raw.get("location") or {}).get("area") or []) if a]
    if not area:
        return parse_free_text(location)
    iso = country_code(area[0])
    if iso and iso != FRANCE:
        return Place(
            iso, None, None, _clean_city(area[-1]) if len(area) > 1 else None, "pays"
        )
    # [France, région, département, (arrondissement…), ville]
    dept = _department_by_name(area[2]) if len(area) > 2 else None
    city = area[-1] if len(area) > 3 else None
    if dept:
        return _from_department(dept, city) or UNKNOWN
    if len(area) > 2:  # la ville à la place du département : [France, région, ville]
        found = _commune(area[-1])
        if found is not None:
            return found
    if len(area) > 1:
        found = _region_only(area[1])
        if found is not None:
            return found
    return Place(FRANCE, None, None, None, "pays") if iso == FRANCE else UNKNOWN


def _smartrecruiters(location: str, raw: dict[str, Any]) -> Place:
    loc = raw.get("location") or {}
    iso = country_code(loc.get("country"))
    city = loc.get("city")
    if iso and iso != FRANCE:
        return Place(iso, None, None, _clean_city(city), "pays")
    dept = department_from_postcode(loc.get("postalCode"))
    found = _from_department(dept, city) if dept else _commune(city) if city else None
    if found is not None:
        return found
    region = _region_only(loc.get("region"))
    if region is not None:
        return region
    if iso == FRANCE:
        return Place(FRANCE, None, None, _clean_city(city), "pays")
    return parse_free_text(location)


def _workday(location: str, raw: dict[str, Any]) -> Place:
    iso = country_code((raw.get("country") or {}).get("descriptor"))
    city = str(raw.get("location") or "").strip()
    if iso and iso != FRANCE:
        return Place(iso, None, None, _clean_city(city) or None, "pays")
    # « Chatellerault-Csc-France » : les sites internes suffixent parfois le pays.
    city = re.sub(r"[-\s]+(france|fr)$", "", city, flags=re.I)
    found = _commune(city) if city else None
    if found is not None:
        return found
    found = parse_free_text(city or location)
    if found.precision != "inconnu":
        return found
    return Place(FRANCE, None, None, _clean_city(city), "pays") if iso else UNKNOWN


_BY_SOURCE = {
    "france_travail": _france_travail,
    "adzuna": _adzuna,
    "smartrecruiters": _smartrecruiters,
    "workday": _workday,
}


def normalize_location(
    source: str, location: str, raw: dict[str, Any] | None = None
) -> Place:
    """Lieu normalisé d'une offre, d'après les champs de SA source puis le texte libre."""
    reader = _BY_SOURCE.get(source)
    if reader is not None:
        place = reader(location or "", raw or {})
        if place.precision != "inconnu":
            return place
    return parse_free_text(location)
