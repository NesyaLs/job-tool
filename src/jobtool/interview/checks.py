"""Contrôles DÉTERMINISTES d'une réponse de l'intervieweur. Le code mesure, le LLM non.

Trois familles, qui ne lèvent jamais (elles renvoient des constats, comme
`core/traceability.check_traceability`) :

1. STRUCTURE : chaque brouillon doit se valider dans le modèle de sa nature, et ses
   références (expérience, réalisation, messages sources) doivent exister.
2. ANTI-INVENTION : tout nombre d'une proposition doit figurer dans ce que l'utilisateur a
   écrit (messages ou profil déjà saisi). Une année de date aussi : « l'an dernier »
   converti en « 2025 » serait un calcul du LLM, donc interdit. Une soft skill exige un
   exemple concret (`evidence`) cité depuis la conversation.
3. FORME DU MESSAGE : une seule question, un message court.

Les constats de structure et de forme sont renvoyés au LLM (boucle de correction). Ceux du
garde-fou aussi, puis, s'ils persistent, la proposition est gardée mais BLOQUÉE : elle ne
peut être acceptée qu'après modification par l'utilisateur.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.interview.models import InterviewSession, Proposal, ProposalKind
from jobtool.interview.schema import (
    InterviewTurn,
    draft_fields,
    section_of,
    validate_payload,
)

#: Longueur maximale d'un message de l'intervieweur (caractères). Au-delà, ce n'est plus
#: « une question courte ».
MAX_MESSAGE_CHARS = 600

# --- Nombres ----------------------------------------------------------------

# Un nombre : chiffres, éventuellement groupés par espaces (« 1 200 ») ou décimaux.
_NUMBER = re.compile(r"\d+(?:[  ]\d{3})*(?:[.,]\d+)?")

# Nombres écrits en toutes lettres qu'un utilisateur emploie couramment. Sans eux, « douze
# tables » reformulé en « 12 tables » serait signalé à tort.
_WORDS = {
    "un": "1", "une": "1", "deux": "2", "trois": "3", "quatre": "4", "cinq": "5",
    "six": "6", "sept": "7", "huit": "8", "neuf": "9", "dix": "10", "onze": "11",
    "douze": "12", "treize": "13", "quatorze": "14", "quinze": "15", "seize": "16",
    "vingt": "20", "trente": "30", "quarante": "40", "cinquante": "50",
    "soixante": "60", "cent": "100", "mille": "1000", "dizaine": "10",
    "douzaine": "12", "vingtaine": "20", "centaine": "100",
}  # fmt: skip


def _normalize_number(raw: str) -> str:
    value = re.sub(r"[  ]", "", raw).replace(",", ".")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    """Nombres d'un texte, normalisés (« 1 200 » -> « 1200 », « 1,50 » -> « 1.5 »)."""
    found = {_normalize_number(m) for m in _NUMBER.findall(text)}
    for word in re.findall(r"[a-zàâçéèêëîïôûùüÿœ]+", text.lower()):
        if word in _WORDS:
            found.add(_WORDS[word])
    return found


def _years_of(value: str | None) -> set[str]:
    return set(re.findall(r"\d{4}", value or ""))


def user_corpus(session: InterviewSession, base: ExperienceBase | None) -> str:
    """Tout ce que l'utilisateur a écrit : ses messages, et son profil déjà saisi."""
    parts = session.user_texts()
    if base is not None:
        parts.append(base.model_dump_json())
    return "\n".join(parts)


#: Champs de TEXTE LIBRE contrôlés par le garde-fou. Les références (`experience_ref`,
#: `realisation_ref`, « p1 ») et les énumérations ne sont pas des faits : un « 1 » d'id
#: n'est pas un chiffre inventé.
TEXT_FIELDS = (
    "value", "degree", "school", "title", "organization", "summary", "text", "name",
    "level", "evidence", "skills",
)  # fmt: skip


def invented_numbers(kind: ProposalKind, payload: dict, corpus: str) -> list[str]:
    """Constats : nombres (et années de dates) de la proposition absents du corpus."""
    known = numbers_in(corpus)
    issues: list[str] = []
    for key in TEXT_FIELDS:
        value = payload.get(key)
        texts = value if isinstance(value, list) else [value]
        for text in texts:
            if not isinstance(text, str):
                continue
            for number in sorted(numbers_in_digits(text)):
                if number not in known:
                    issues.append(
                        f"le nombre « {number} » n'apparaît dans aucun message de "
                        "l'utilisateur"
                    )
    for key in ("start", "end"):
        for year in sorted(_years_of(payload.get(key))):
            if year not in known:
                issues.append(f"l'année {year} n'a pas été donnée par l'utilisateur")
    return issues


def numbers_in_digits(text: str) -> set[str]:
    """Nombres ÉCRITS EN CHIFFRES d'un texte (ce que l'on contrôle côté proposition)."""
    return {_normalize_number(m) for m in _NUMBER.findall(text)}


# --- Compétences --------------------------------------------------------------


def skill_key(name: str) -> str:
    """Clé de dédoublonnage : casse, accents et ponctuation ignorés (« Node.js » = « nodejs »)."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9+#]", "", text.lower())


def existing_skill(base: ExperienceBase | None, name: str) -> tuple[str, str] | None:
    """(catégorie, orthographe existante) si la compétence est déjà dans une liste maître."""
    if base is None:
        return None
    key = skill_key(name)
    for category, values in (("hard", base.hard_skills), ("soft", base.soft_skills)):
        for value in values:
            if skill_key(value) == key:
                return category, value
    return None


# --- Références -------------------------------------------------------------


def _experience_ids(base: ExperienceBase | None) -> set[str]:
    return {e.id for e in base.experiences} if base else set()


def _achievement_ids(base: ExperienceBase | None) -> set[str]:
    if base is None:
        return set()
    return {a.id for e in base.experiences for a in e.achievements}


def _live_of(session: InterviewSession, kind: ProposalKind) -> set[str]:
    """Ids de propositions citables : en attente, ou acceptées (résolues à l'application)."""
    return {
        p.id for p in session.proposals if p.kind is kind and p.status != "rejected"
    }


# --- Contrôle d'une réponse -------------------------------------------------


@dataclass
class TurnReport:
    """Résultat du contrôle d'une réponse : propositions retenues et constats."""

    proposals: list[Proposal] = field(default_factory=list)
    #: Constats à renvoyer au LLM (structure, forme, garde-fou).
    issues: list[str] = field(default_factory=list)
    #: Brouillons écartés sans être des erreurs (doublon d'une compétence existante).
    dropped: list[str] = field(default_factory=list)


def check_turn(
    turn: InterviewTurn,
    session: InterviewSession,
    base: ExperienceBase | None,
    *,
    next_index: int,
) -> TurnReport:
    """Valide et contrôle une réponse. Les propositions n'ont PAS encore été ajoutées.

    `next_index` : numéro du premier brouillon. L'id `p{next_index + position - 1}` est
    attribué par POSITION, retenu ou non, pour qu'une réalisation puisse citer une
    expérience proposée plus haut dans la MÊME réponse.
    """
    report = TurnReport()
    report.issues.extend(_message_issues(turn.message))

    corpus = user_corpus(session, base)
    user_ids = session.user_message_ids()
    last_user = session.last_user_message_id()
    experiences = _experience_ids(base) | _live_of(session, ProposalKind.experience)
    achievements = _achievement_ids(base) | _live_of(session, ProposalKind.realisation)
    seen_skills = {
        skill_key(p.payload.get("name", ""))
        for p in session.pending()
        if p.kind is ProposalKind.competence
    }

    for n, draft in enumerate(turn.proposals, start=1):
        proposal_id = f"p{next_index + n - 1}"
        label = f"proposition {proposal_id} ({draft.kind.value})"
        payload, errors = validate_payload(draft.kind, draft_fields(draft))
        if payload is None:
            report.issues.extend(f"{label} : {e}" for e in errors)
            continue

        ref_issues = _reference_issues(draft.kind, payload, experiences, achievements)
        if ref_issues:
            report.issues.extend(f"{label} : {e}" for e in ref_issues)
            continue

        if draft.kind is ProposalKind.competence:
            known = existing_skill(base, payload["name"])
            key = skill_key(payload["name"])
            if known is not None:
                category, spelled = known
                if not payload.get("realisation_ref"):
                    report.dropped.append(f"compétence déjà présente : {spelled}")
                    continue
                payload["name"], payload["category"] = spelled, category
            elif key in seen_skills:
                report.dropped.append(f"compétence déjà proposée : {payload['name']}")
                continue
            seen_skills.add(key)
            if payload["category"] == "soft" and not payload.get("evidence"):
                report.issues.append(
                    f"{label} : une soft skill doit citer, dans « evidence », l'exemple "
                    "concret de la conversation qui l'appuie — sinon ne la propose pas"
                )
                continue

        sources = [s for s in draft.sources if s in user_ids]
        if not sources and last_user is not None:
            sources = [last_user]

        warnings = invented_numbers(draft.kind, payload, corpus)
        if warnings:
            report.issues.extend(f"{label} : {w}" for w in warnings)

        report.proposals.append(
            Proposal(
                id=proposal_id,
                kind=draft.kind,
                payload=payload,
                sources=sources,
                section=section_of(draft.kind, payload, session.current),
                warnings=warnings,
                blocking=bool(warnings),
            )
        )
        if draft.kind is ProposalKind.experience:
            experiences.add(proposal_id)
        if draft.kind is ProposalKind.realisation:
            achievements.add(proposal_id)
    return report


def _reference_issues(
    kind: ProposalKind, payload: dict, experiences: set[str], achievements: set[str]
) -> list[str]:
    if (
        kind is ProposalKind.realisation
        and payload["experience_ref"] not in experiences
    ):
        return [
            f"experience_ref « {payload['experience_ref']} » inconnu : cite l'id d'une "
            "expérience du profil ou d'une proposition d'expérience en attente"
        ]
    ref = payload.get("realisation_ref") if kind is ProposalKind.competence else None
    if ref and ref not in achievements:
        return [f"realisation_ref « {ref} » inconnu"]
    return []


def _message_issues(message: str) -> list[str]:
    issues: list[str] = []
    text = message.strip()
    if not text:
        issues.append("le message est vide : pose une question")
    if text.count("?") > 1:
        issues.append("pose UNE seule question par message")
    if len(text) > MAX_MESSAGE_CHARS:
        issues.append(
            f"message trop long ({len(text)} caractères, maximum {MAX_MESSAGE_CHARS})"
        )
    return issues


def recheck_edit(
    proposal: Proposal,
    payload: dict,
    session: InterviewSession,
    base: ExperienceBase | None,
) -> tuple[dict | None, list[str]]:
    """Valide une proposition MODIFIÉE par l'utilisateur. Pas de garde-fou sur les nombres :
    un texte que l'utilisateur a écrit lui-même est, par définition, un fait qu'il donne."""
    clean, errors = validate_payload(proposal.kind, payload)
    if clean is None:
        return None, errors
    ref_issues = _reference_issues(
        proposal.kind,
        clean,
        _experience_ids(base) | _live_of(session, ProposalKind.experience),
        _achievement_ids(base) | _live_of(session, ProposalKind.realisation),
    )
    return (None, ref_issues) if ref_issues else (clean, [])


__all__ = [
    "MAX_MESSAGE_CHARS",
    "TurnReport",
    "check_turn",
    "existing_skill",
    "TEXT_FIELDS",
    "invented_numbers",
    "numbers_in",
    "recheck_edit",
    "skill_key",
    "user_corpus",
]
