"""Type de contrat et temps de travail : une énumération commune à toutes les sources.

DÉTERMINISTE (aucun LLM) : chaque source écrit le contrat à sa façon — codes France
Travail (« MIS », « LIB »), vocabulaire anglais d'Adzuna (« permanent »), identifiants
SmartRecruiters (« intern »), puces Workday (« CDI », « Regular Employee »)… On le RANGE
dans l'une des familles de `CONTRACT_KINDS`, dans cet ordre de confiance :

  1. l'INTITULÉ, pour l'alternance et le stage : ce sont des marqueurs explicites qu'une
     source contredit souvent par un champ générique (SmartRecruiters classe « Apprenti
     Développeur IA » et « Stage – Consultant » en `permanent`, constaté sur des offres
     réelles) ;
  2. les champs structurés de la source ;
  3. le texte libre du champ contrat ;
  4. l'intitulé, pour les autres familles (CDI, CDD, intérim, freelance) ;
  5. à défaut : `inconnu`.

La DESCRIPTION n'est jamais lue : « stage » et « CDI » y apparaissent dans bien d'autres
sens (« après un stage… », « poste en CDI à terme »).

Le TEMPS DE TRAVAIL (plein / partiel) est une autre information, que les sources mêlent
souvent au contrat (Workday `timeType`, Adzuna `contract_time`, SmartRecruiters
« Full-time ») : il est rangé à part et ne sert jamais à déduire le contrat.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

ContractKind = Literal[
    "cdi", "cdd", "stage", "alternance", "interim", "freelance", "inconnu"
]
CONTRACT_KINDS: tuple[ContractKind, ...] = (
    "cdi",
    "cdd",
    "stage",
    "alternance",
    "interim",
    "freelance",
    "inconnu",
)
UNKNOWN_CONTRACT: ContractKind = "inconnu"

WorkTime = Literal["temps_plein", "temps_partiel"]


def _norm(text: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.lower().split())


#: Familles reconnues dans du texte NORMALISÉ (intitulé ou champ contrat).
CONTRACT_PATTERNS: dict[ContractKind, re.Pattern[str]] = {
    "cdi": re.compile(r"\bcdi\b|duree indeterminee"),
    "cdd": re.compile(r"\bcdd\b|duree determinee"),
    "interim": re.compile(r"\binterim|travail temporaire"),
    "stage": re.compile(r"\bstage\b|\bstagiaire|\binternship|\bintern\b|\binterns\b"),
    "alternance": re.compile(
        r"\balternan|\bapprenti|professionnalisation|work.study|apprenticeship"
    ),
    "freelance": re.compile(r"\bfreelance|\bliberal|\bindependant|\bportage"),
}

#: Vocabulaire anglais des sources, lu dans le SEUL champ contrat : dans un intitulé,
#: « Contract manager » n'est pas un CDD.
FIELD_PATTERNS: dict[ContractKind, re.Pattern[str]] = {
    "cdi": re.compile(r"\bpermanent\b|\bregular\b"),
    "cdd": re.compile(r"\bcontract\b|\btemporary\b|\bfixed.term"),
    "stage": re.compile(r"\bintern\b"),
}

#: Priorité quand un texte évoque plusieurs familles (« Stage / Alternance » : c'est
#: l'alternance, plus contraignante, qui décrit le poste).
_PRIORITY: tuple[ContractKind, ...] = (
    "alternance",
    "stage",
    "interim",
    "freelance",
    "cdd",
    "cdi",
)


def _first(kinds: set[ContractKind]) -> ContractKind | None:
    for kind in _PRIORITY:
        if kind in kinds:
            return kind
    return None


def kinds_in(text: str, *, field: bool = False) -> set[ContractKind]:
    """Familles évoquées par un texte. `field=True` : c'est un champ contrat (vocabulaire
    anglais des sources autorisé)."""
    norm = _norm(text)
    found = {k for k, p in CONTRACT_PATTERNS.items() if p.search(norm)}
    if field:
        found |= {k for k, p in FIELD_PATTERNS.items() if p.search(norm)}
    return found


#: Segment NIÉ d'un intitulé, jusqu'à la fin de la parenthèse ou du membre de phrase :
#: « Data Analyst (5 ans d'expérience hors stage et alternance) - CDI » ne décrit ni un
#: stage ni une alternance (cas réel France Travail).
_NEGATED = re.compile(
    r"\b(?:hors|sauf|sans|pas d[e']?|non|excluding|except|no)\b[^()\-–|/]*"
)


def affirmative_title(title: str) -> str:
    """Intitulé normalisé, privé de ses segments niés."""
    return _NEGATED.sub(" ", _norm(title))


# --- Champs structurés, par source ------------------------------------------

#: Codes `typeContrat` de France Travail.
_FT_TYPES: dict[str, ContractKind] = {
    "CDI": "cdi",
    "DIN": "cdi",  # CDI intérimaire
    "CDD": "cdd",
    "DDI": "cdd",  # CDD d'insertion
    "SAI": "cdd",  # saisonnier
    "MIS": "interim",
    "TTI": "interim",  # travail temporaire d'insertion
    "LIB": "freelance",  # profession libérale
    "FRA": "freelance",  # franchise
    "CCE": "freelance",  # profession commerciale
    "REP": "freelance",  # reprise d'entreprise
}


def _france_travail(raw: dict[str, Any]) -> ContractKind | None:
    nature = _norm(raw.get("natureContrat"))
    if raw.get("alternance") is True or "apprentissage" in nature:
        return "alternance"
    if "professionnalisation" in nature:
        return "alternance"
    kind = _FT_TYPES.get(str(raw.get("typeContrat") or "").upper())
    if kind:
        return kind
    if "non salarie" in nature:
        return "freelance"
    return None


_ADZUNA_TYPES: dict[str, ContractKind] = {"permanent": "cdi", "contract": "cdd"}


def _adzuna(raw: dict[str, Any]) -> ContractKind | None:
    return _ADZUNA_TYPES.get(_norm(raw.get("contract_type")))


#: Identifiants `typeOfEmployment` de SmartRecruiters (« permanent » = poste pérenne).
_SR_TYPES: dict[str, ContractKind] = {
    "permanent": "cdi",
    "intern": "stage",
    "internship": "stage",
    "contract": "cdd",
    "temporary": "cdd",
    "freelance": "freelance",
    "apprenticeship": "alternance",
}


def _smartrecruiters(raw: dict[str, Any]) -> ContractKind | None:
    toe = raw.get("typeOfEmployment") or {}
    kind = _SR_TYPES.get(_norm(toe.get("id")))
    if kind:
        return kind
    return _first(kinds_in(str(toe.get("label") or ""), field=True))


def _workday(raw: dict[str, Any]) -> ContractKind | None:
    # Les puces de la liste portent parfois le contrat (« CDI », « Stage ») ou la
    # catégorie de salarié (« Regular Employee », « Fixed Term Employee »).
    kinds: set[ContractKind] = set()
    for bullet in raw.get("bulletFields") or []:
        kinds |= kinds_in(str(bullet), field=True)
    return _first(kinds)


_BY_SOURCE = {
    "france_travail": _france_travail,
    "adzuna": _adzuna,
    "smartrecruiters": _smartrecruiters,
    "workday": _workday,
}


def normalize_contract(
    source: str, contract_type: str, title: str, raw: dict[str, Any] | None = None
) -> ContractKind:
    """Famille de contrat d'une offre (voir l'ordre de confiance dans l'en-tête)."""
    raw = raw or {}
    in_title = kinds_in(affirmative_title(title))
    for strong in ("alternance", "stage"):
        if strong in in_title:
            return strong  # type: ignore[return-value]
    reader = _BY_SOURCE.get(source)
    kind = reader(raw) if reader else None
    if kind:
        return kind
    kind = _first(kinds_in(contract_type, field=True))
    if kind:
        return kind
    return _first(in_title) or UNKNOWN_CONTRACT


# --- Temps de travail ---------------------------------------------------------


def _work_time_of(text: Any) -> WorkTime | None:
    norm = _norm(text).replace("-", " ").replace("_", " ")
    if re.search(r"\bpart(iel)? ?time\b|temps partiel|\bpartiel\b", norm):
        return "temps_partiel"
    if re.search(r"\bfull ?time\b|temps plein|\bplein\b", norm):
        return "temps_plein"
    return None


def normalize_work_time(
    source: str, contract_type: str, raw: dict[str, Any] | None = None
) -> WorkTime | None:
    """Temps plein / partiel, s'il est dit quelque part. Jamais déduit du contrat."""
    raw = raw or {}
    candidates = [
        raw.get("dureeTravailLibelleConverti"),  # France Travail
        raw.get("contract_time"),  # Adzuna
        (raw.get("typeOfEmployment") or {}).get("label"),  # SmartRecruiters
        raw.get("timeType"),  # Workday
        contract_type,
    ]
    for value in candidates:
        found = _work_time_of(value)
        if found:
            return found
    return None
