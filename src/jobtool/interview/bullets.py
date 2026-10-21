"""Propositions de réalisations (puces) pour UNE expérience du profil.

Un seul appel au modèle, sans conversation : on lui donne son rôle, le reste du profil pour
qu'il comprenne le parcours, l'expérience ciblée et, si la personne en a écrit une, sa
consigne. Il renvoie quelques puces ; la personne choisit celles qu'elle garde.

Le modèle propose, le code vérifie (project_ctx.md §2) : longueur, doublons et surtout
CHIFFRES. Une puce ne peut citer un nombre que s'il figure déjà dans l'expérience ou dans la
consigne — sinon on redemande, puis on écarte la puce. Rien n'est écrit dans le profil ici :
c'est le front qui ajoute les puces retenues, par l'enregistrement habituel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.llm.correction_loop import run_correction_loop
from jobtool.core.traceability import Violation

# Bornes mesurées par le code (le modèle ne compte jamais).
MIN_BULLETS = 3
MAX_BULLETS = 5
MIN_CHARS = 30
MAX_CHARS = 200

_NUMBER_RE = re.compile(r"\d+")

SYSTEM_PROMPT = """\
Tu aides une personne à écrire les réalisations de son CV.
Propose 3 à 5 puces pour l'EXPÉRIENCE CIBLE, en français.
- Une puce = une phrase, commençant par un verbe d'action, 30 à 200 caractères.
- Sers-toi du reste du profil pour comprendre le parcours et choisir des formulations
  pertinentes, mais ne recopie pas les faits d'une autre expérience.
- N'invente ni chiffre, ni outil, ni résultat : aucun nombre qui ne figure pas dans
  l'expérience cible ou dans la consigne.
- Ne reprends pas une puce déjà écrite.
- Si une consigne est donnée, suis-la.
"""


class BulletSuggestions(BaseModel):
    """Sortie attendue du modèle."""

    bullets: list[str]


@dataclass(frozen=True)
class TargetExperience:
    """L'expérience telle qu'elle est à l'écran, peut-être pas encore enregistrée."""

    id: str
    title: str
    organization: str = ""
    type: str = "job"
    start: str = ""
    end: str = ""
    summary: str = ""
    bullets: list[str] = field(default_factory=list)


def _period(start: str, end: str) -> str:
    end = "aujourd'hui" if end == "present" else end
    return " → ".join(v for v in (start, end) if v)


def _profile_context(base: ExperienceBase | None, target_id: str) -> list[str]:
    """Le reste du profil, sans coordonnées : seul le parcours aide à proposer."""
    if base is None:
        return ["(profil encore vide)"]
    lines: list[str] = []
    if base.hard_skills:
        lines.append("Compétences techniques : " + ", ".join(base.hard_skills))
    if base.soft_skills:
        lines.append("Qualités : " + ", ".join(base.soft_skills))
    for edu in base.education:
        lines.append(
            f"Formation : {edu.degree}, {edu.school} ({_period(edu.start, edu.end)})"
        )
    others = [e for e in base.experiences if e.id != target_id]
    if others:
        lines.append("Autres expériences :")
    for exp in others:
        lines.append(
            f"- {exp.title} — {exp.organization} ({exp.type.value}, "
            f"{_period(exp.start, exp.end)})"
        )
        lines += [f"    • {ach.text}" for ach in exp.achievements]
    return lines or ["(profil encore vide)"]


def build_user_prompt(
    base: ExperienceBase | None, target: TargetExperience, instruction: str
) -> str:
    lines = ["PROFIL (contexte) :", *_profile_context(base, target.id), ""]
    lines += [
        "EXPÉRIENCE CIBLE :",
        f"Intitulé : {target.title}",
        f"Organisation : {target.organization or '(non précisée)'}",
        f"Type : {target.type}",
    ]
    if period := _period(target.start, target.end):
        lines.append(f"Période : {period}")
    if target.summary.strip():
        lines.append(f"Contexte : {target.summary.strip()}")
    if target.bullets:
        lines.append("Puces déjà écrites (à ne pas reprendre) :")
        lines += [f"    • {b}" for b in target.bullets]
    if instruction.strip():
        lines += ["", f"CONSIGNE DE LA PERSONNE : {instruction.strip()}"]
    return "\n".join(lines)


def _source_numbers(target: TargetExperience, instruction: str) -> set[str]:
    text = " ".join(
        [
            target.title,
            target.organization,
            target.summary,
            instruction,
            *target.bullets,
        ]
    )
    return set(_NUMBER_RE.findall(text))


def bullet_issues(bullet: str, target: TargetExperience, instruction: str) -> list[str]:
    """Manquements d'UNE puce (vide = recevable)."""
    issues: list[str] = []
    text = bullet.strip()
    if len(text) < MIN_CHARS:
        issues.append(f"trop courte ({len(text)} < {MIN_CHARS} caractères)")
    if len(text) > MAX_CHARS:
        issues.append(f"trop longue ({len(text)} > {MAX_CHARS} caractères)")
    invented = set(_NUMBER_RE.findall(text)) - _source_numbers(target, instruction)
    if invented:
        issues.append("chiffre absent de l'expérience : " + ", ".join(sorted(invented)))
    if text.casefold() in {b.strip().casefold() for b in target.bullets}:
        issues.append("reprend une puce déjà écrite")
    return issues


def check_suggestions(
    out: BulletSuggestions, target: TargetExperience, instruction: str
) -> list[Violation]:
    violations: list[Violation] = []
    if not MIN_BULLETS <= len(out.bullets) <= MAX_BULLETS:
        violations.append(
            Violation(
                kind="count",
                message=f"{len(out.bullets)} puces : il en faut entre {MIN_BULLETS} et {MAX_BULLETS}",
            )
        )
    for index, bullet in enumerate(out.bullets, start=1):
        for issue in bullet_issues(bullet, target, instruction):
            violations.append(
                Violation(kind="bullet", message=f"puce {index} : {issue}")
            )
    return violations


def _correction(issues: list) -> str:
    return (
        "Corrige ta proposition. Problèmes relevés :\n"
        + "\n".join(f"- {v.message}" for v in issues)
        + "\nRenvoie la liste complète des puces corrigées."
    )


def suggest_bullets(
    client,
    base: ExperienceBase | None,
    target: TargetExperience,
    instruction: str = "",
    max_retries: int = 1,
) -> list[str]:
    """Puces proposées, déjà filtrées : seules les puces recevables sont renvoyées.

    Après la boucle de correction, une puce encore fautive est ÉCARTÉE plutôt que
    corrigée par le code. La liste peut donc être vide.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(base, target, instruction)},
    ]
    out, _issues = run_correction_loop(
        client,
        messages,
        BulletSuggestions,
        check=lambda o: check_suggestions(o, target, instruction),
        build_correction=_correction,
        max_retries=max_retries,
        step="puces",
    )
    kept: list[str] = []
    for bullet in out.bullets:
        text = bullet.strip()
        if not bullet_issues(text, target, instruction) and text not in kept:
            kept.append(text)
    return kept[:MAX_BULLETS]
