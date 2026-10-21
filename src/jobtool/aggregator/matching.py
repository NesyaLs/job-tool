"""Score de compatibilité d'une offre avec des critères de recherche — DÉTERMINISTE.

Sert la page « Découvrir » : une pile d'offres triées par score décroissant. Tout ce qui
est ici est du CALCUL, fait par le code et jamais par le LLM (« le LLM ne mesure jamais ») :
même offre + mêmes critères + même instant -> même score, au point près.

Module PUR : il ne lit ni n'écrit aucune base, n'importe ni services ni interface. Il ne
dépend que du modèle `Job`. C'est ce qui le rend déplaçable tel quel lors de la refonte
(il ne dépend que du modèle `Job`).

Formule (sur 100 points) :

    PERTINENCE  85   répartis entre les critères RENSEIGNÉS, au prorata de :
      mots-clés   45   2/3 × part des mots-clés présents dans l'INTITULÉ
                     + 1/3 × part des mots-clés présents dans la DESCRIPTION
      lieu        20   département demandé = 1 ; télétravail total = 0,8 ;
                       lieu inconnu = 0,3 ; autre département = 0
      contrat     20   type demandé = 1 ; type inconnu = 0,3 ; autre type = 0
    FRAÎCHEUR   15   0,5 ^ (âge en jours / 14) ; date inconnue = 0,3

Tous critères renseignés, on retrouve 45 + 20 + 20 + 15. Un critère non renseigné ne
compte pas : ses points vont aux autres (sans quoi une recherche sans lieu plafonnerait
à 80 %). Sans AUCUN critère de pertinence, celle-ci est NEUTRE (0,5) : la fraîcheur seule
ne peut pas afficher « 91 % compatible » avec une recherche qui n'a rien demandé.

Le pourcentage affiché est arrondi puis borné à [1 ; 99] : un 100 % promettrait une
adéquation parfaite que quatre critères grossiers ne peuvent pas établir.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from jobtool.aggregator.contracts import CONTRACT_PATTERNS, FIELD_PATTERNS
from jobtool.aggregator.models import Job

#: Types de contrat reconnus. Les sources écrivent le contrat en texte libre (« CDD - 12
#: Mois », « permanent full_time », « Profession libérale »…) : on le RANGE dans l'une de
#: ces familles, on ne le compare jamais tel quel.
ContractKind = Literal["cdi", "cdd", "interim", "stage", "alternance", "freelance"]
CONTRACT_KINDS: tuple[ContractKind, ...] = (
    "cdi",
    "cdd",
    "interim",
    "stage",
    "alternance",
    "freelance",
)

#: Pondérations (points). Leur somme vaut 100 quand tous les critères sont renseignés.
W_TITLE = 30.0
W_DESCRIPTION = 15.0
W_KEYWORDS = W_TITLE + W_DESCRIPTION
W_LOCATION = 20.0
W_CONTRACT = 20.0
W_FRESHNESS = 15.0
#: Part de la pertinence (mots-clés, lieu, contrat) : fixe, quels que soient les critères.
W_RELEVANCE = 100.0 - W_FRESHNESS
#: Pertinence retenue quand aucun critère de pertinence n'est renseigné.
NEUTRAL_RELEVANCE = 0.5

#: Demi-vie de la fraîcheur : une offre de 14 jours vaut la moitié d'une offre du jour.
FRESHNESS_HALF_LIFE_DAYS = 14.0
#: Crédit accordé quand l'information manque (date, lieu, contrat). Ni zéro (la source
#: n'a juste rien dit) ni plein (on ne peut pas prouver l'adéquation).
UNKNOWN_CREDIT = 0.3
#: Crédit d'un poste en télétravail complet quand un lieu est demandé.
REMOTE_CREDIT = 0.8

#: Bornes du pourcentage affiché.
MIN_PERCENT = 1
MAX_PERCENT = 99
#: Seuil par défaut sous lequel une offre n'est pas proposée dans la pile.
DEFAULT_MIN_PERCENT = 25

#: Mots vides ignorés dans les mots-clés : « chef de projet » cherche « chef » et « projet ».
_STOPWORDS = frozenset(
    "de des du la le les l d un une et ou en au aux a à pour par sur dans avec the of and "
    "for in to".split()
)

#: Les familles de contrat se reconnaissent désormais à la NORMALISATION (contracts.py),
#: qui range chaque offre dans `Job.contract_kind` à la collecte. Les motifs ne servent
#: plus ici qu'aux offres qui n'ont pas (encore) été normalisées.
_CONTRACT_PATTERNS = CONTRACT_PATTERNS
_CONTRACT_FIELD_PATTERNS = FIELD_PATTERNS

#: Département en tête de lieu, format France Travail : « 75 - Paris », « 2A - Ajaccio ».
_DEPT_PREFIX = re.compile(r"^\s*(\d{2,3}|2a|2b)\s*-")
#: Code postal dans un lieu libre : « 69003 Lyon », « Lyon (69) ».
_POSTCODE = re.compile(r"\b(\d{5})\b")
_DEPT_PAREN = re.compile(r"\((\d{2,3}|2a|2b)\)")


def normalize_text(text: str) -> str:
    """Minuscules, sans accents, espaces compactés — pour comparer sans se soucier de la graphie."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def keyword_tokens(keywords: str) -> list[str]:
    """Mots-clés utiles d'une saisie : normalisés, dédoublonnés, sans mots vides."""
    seen: list[str] = []
    for token in re.split(r"[^\w+#]+", normalize_text(keywords)):
        if len(token) < 2 and not token.isdigit():
            continue
        if token in _STOPWORDS or token in seen:
            continue
        seen.append(token)
    return seen


def _contains_word(haystack: str, token: str) -> bool:
    """`token` apparaît en DÉBUT de mot : « data » trouve « datascientist » et « data »,
    pas « metadata ». Le préfixe tolère pluriels et dérivés (« développeur » / « développeurs »)."""
    return re.search(r"(?<![\w])" + re.escape(token), haystack) is not None


def contract_kinds(job: Job) -> set[ContractKind]:
    """Familles de contrat d'une offre : sa famille normalisée, sinon les motifs.

    Une famille normalisée est UNE décision (l'intitulé « Stage / Alternance » est une
    alternance) ; les motifs, eux, peuvent en trouver plusieurs.
    """
    if job.contract_kind in CONTRACT_KINDS:
        return {job.contract_kind}  # type: ignore[arg-type]
    field = normalize_text(job.contract_type)
    text = f"{field} {normalize_text(job.title)}"
    found = {k for k, pattern in _CONTRACT_PATTERNS.items() if pattern.search(text)}
    found |= {
        k for k, pattern in _CONTRACT_FIELD_PATTERNS.items() if pattern.search(field)
    }
    return {k for k in CONTRACT_KINDS if k in found}


def department_of(location: str) -> str | None:
    """Code de département d'un lieu (« 75 », « 2A », « 974 »), ou None si introuvable."""
    loc = normalize_text(location)
    if not loc:
        return None
    for pattern in (_DEPT_PREFIX, _DEPT_PAREN):
        m = pattern.search(loc)
        if m:
            return m.group(1).upper()
    m = _POSTCODE.search(loc)
    if m:
        code = m.group(1)
        return code[:3] if code.startswith("97") else code[:2]
    return None


def _parse_day(value: str | None) -> date | None:
    """Jour de publication (les 10 premiers caractères ISO), ou None si illisible."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


@dataclass(frozen=True)
class MatchCriteria:
    """Critères de la pile. Tous facultatifs ; un critère vide ne pèse pas dans le score."""

    keywords: str = ""
    departments: tuple[str, ...] = ()  # codes INSEE (« 75 », « 2A »)
    contracts: tuple[ContractKind, ...] = ()


@dataclass(frozen=True)
class CriterionScore:
    """Contribution d'un critère : `value` dans [0, 1], `weight` en points NORMALISÉS
    (la somme des poids des critères retenus vaut 100). `points` = value × weight."""

    key: str
    label: str
    value: float
    weight: float
    detail: str

    @property
    def points(self) -> float:
        return self.value * self.weight


@dataclass(frozen=True)
class MatchResult:
    job: Job
    percent: int
    criteria: tuple[CriterionScore, ...]


def _keywords_score(job: Job, tokens: list[str]) -> tuple[float, str]:
    title = normalize_text(job.title)
    description = normalize_text(job.description)
    in_title = sum(_contains_word(title, t) for t in tokens)
    in_desc = sum(_contains_word(description, t) for t in tokens)
    n = len(tokens)
    value = (W_TITLE * in_title / n + W_DESCRIPTION * in_desc / n) / W_KEYWORDS
    return value, f"{in_title}/{n} dans l'intitulé, {in_desc}/{n} dans la description"


def _location_score(job: Job, departments: tuple[str, ...]) -> tuple[float, str]:
    wanted = {d.strip().upper() for d in departments if d.strip()}
    dept = job.department or department_of(job.location)
    if dept is not None and dept in wanted:
        return 1.0, f"département {dept} demandé"
    if job.remote == "oui":
        return REMOTE_CREDIT, "télétravail complet"
    if dept is None:
        return UNKNOWN_CREDIT, "lieu non précisé"
    return 0.0, f"département {dept} hors recherche"


def _contract_score(job: Job, contracts: tuple[ContractKind, ...]) -> tuple[float, str]:
    kinds = contract_kinds(job)
    if kinds & set(contracts):
        return 1.0, job.contract_type or "type reconnu dans l'intitulé"
    if not kinds:
        return UNKNOWN_CREDIT, "contrat non précisé"
    return 0.0, job.contract_type or ", ".join(sorted(kinds))


def _freshness_score(job: Job, today: date) -> tuple[float, str]:
    day = _parse_day(job.posted_date)
    if day is None:
        return UNKNOWN_CREDIT, "date de publication inconnue"
    age = max(0, (today - day).days)
    value = 0.5 ** (age / FRESHNESS_HALF_LIFE_DAYS)
    if age == 0:
        return value, "publiée aujourd'hui"
    return value, f"publiée il y a {age} jour{'s' if age > 1 else ''}"


def score_job(
    job: Job, criteria: MatchCriteria, *, now: datetime | None = None
) -> MatchResult:
    """Score d'UNE offre. `now` est injectable pour des tests reproductibles."""
    today = (now or datetime.now(UTC)).date()
    raw: list[tuple[str, str, float, float, str]] = []

    tokens = keyword_tokens(criteria.keywords)
    if tokens:
        value, detail = _keywords_score(job, tokens)
        raw.append(("keywords", "Mots-clés", value, W_KEYWORDS, detail))
    if any(d.strip() for d in criteria.departments):
        value, detail = _location_score(job, criteria.departments)
        raw.append(("location", "Lieu", value, W_LOCATION, detail))
    if criteria.contracts:
        value, detail = _contract_score(job, criteria.contracts)
        raw.append(("contract", "Contrat", value, W_CONTRACT, detail))

    relevance_weight = sum(weight for _k, _l, _v, weight, _d in raw)
    if raw:
        scale = W_RELEVANCE / relevance_weight
        weighted = [(k, lab, v, w * scale, det) for k, lab, v, w, det in raw]
    else:
        weighted = [
            (
                "neutral",
                "Critères",
                NEUTRAL_RELEVANCE,
                W_RELEVANCE,
                "aucun critère renseigné : compatibilité neutre",
            )
        ]
    value, detail = _freshness_score(job, today)
    weighted.append(("freshness", "Fraîcheur", value, W_FRESHNESS, detail))

    scored = tuple(
        CriterionScore(key=key, label=label, value=value, weight=weight, detail=detail)
        for key, label, value, weight, detail in weighted
    )
    return MatchResult(
        job=job, percent=to_percent(sum(c.points for c in scored)), criteria=scored
    )


def to_percent(points: float) -> int:
    """Points (0–100) -> pourcentage affiché : arrondi au plus proche, borné à [1 ; 99]."""
    return max(MIN_PERCENT, min(MAX_PERCENT, int(points + 0.5)))


def rank_jobs(
    jobs: Iterable[Job],
    criteria: MatchCriteria,
    *,
    now: datetime | None = None,
    min_percent: int = DEFAULT_MIN_PERCENT,
    exclude: Iterable[str] = (),
) -> list[MatchResult]:
    """Offres retenues pour la pile, de la plus compatible à la moins compatible.

    Écarte celles de `exclude` (déjà affichées côté client) et celles sous `min_percent`.
    Ordre TOTAL et stable : score décroissant, puis publication la plus récente, puis
    identité — deux appels successifs rendent la même pile, condition pour que le
    préchargement n'affiche ni doublon ni trou.
    """
    now = now or datetime.now(UTC)
    skip = set(exclude)
    results = [
        score_job(job, criteria, now=now) for job in jobs if job.dedup_hash not in skip
    ]
    kept = [r for r in results if r.percent >= min_percent]
    # Tri en deux passes (stable) : clé secondaire d'abord, puis la principale.
    kept.sort(key=lambda r: r.job.dedup_hash)
    kept.sort(key=lambda r: (r.job.posted_date or "")[:10], reverse=True)
    kept.sort(key=lambda r: r.percent, reverse=True)
    return kept
