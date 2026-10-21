"""Ce qui distingue un CV d'une lettre, réuni en un seul endroit.

Un document retouchable est d'un `DocumentKind`, et ce type commande six choses : quel
template compiler, où vivent les champs dans le `render_input`, quelles chaînes doivent
rester extractibles du PDF, si des bornes de contenu (traçabilité, budget) s'appliquent,
quelle forme doit avoir une source réécrite à la main (mode pro), et comment compléter
une entrée de compilation enregistrée avant l'ajout d'un champ.

Ces quatre réponses étaient données par autant de `if kind == "cv"` répartis dans trois
modules — `overrides.py`, `checks.py` et le service d'édition. Rien ne les reliait : ajouter
un troisième type de document (ou en corriger un) obligeait à retrouver chaque branche, et
rien ne signalait celle qu'on avait manquée.

Ici, un type = une LIGNE de table. Le code appelant lit la ligne au lieu de rebrancher.

Ce module reste dans le DOMAINE : il ne connaît que des templates, des dicts de rendu et des
contrôles. Le choix du GÉNÉRATEUR (quel appel LLM produit le brouillon) n'y figure pas, et
ne peut pas y figurer — il appartient à `services/`, et un domaine n'importe jamais la
couche service.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.traceability import Violation
from jobtool.documents.cv.profiles import TemplateProfile
from jobtool.documents.drafts.models import DocumentKind

#: Le dict prêt-à-compiler d'un document (ce que la génération produit, ce que Typst reçoit).
#: Sa forme dépend du type de document ; seul ce module et les templates la connaissent.
RenderInput = dict


class SourceError(ValueError):
    """Source de rendu réécrite (mode pro) inexploitable ; le message pointe le champ fautif."""


def _unchanged(data: RenderInput) -> RenderInput:
    """Entrée de compilation laissée telle quelle (le CV n'a rien à compléter)."""
    return data


@dataclass(frozen=True)
class DocumentKindPolicy:
    """Tout ce que le type d'un document commande, en une valeur.

    `needs_experience_base` : seul le CV a des bornes de contenu (traçabilité des puces vers
    la base, budget du template). L'exposer en drapeau évite au service de CHARGER la base
    d'expériences pour une lettre — une lecture inutile, qui coûte un aller-retour sur une
    base distante.
    """

    template_path: Callable[[str], Path]
    apply_overrides: Callable[[RenderInput, dict[str, str]], RenderInput]
    ats_targets: Callable[[RenderInput], list[str]]
    content_issues: Callable[
        [RenderInput, ExperienceBase | None, TemplateProfile | None],
        tuple[list[Violation], list[str]],
    ]
    needs_experience_base: bool
    # Mode pro : valide et complète une source écrite à la main (lève SourceError).
    normalize_source: Callable[[RenderInput], RenderInput]
    # Complète l'entrée de compilation avant CHAQUE rendu : un brouillon enregistré avant
    # l'ajout d'un champ lu par les templates (ex. `salutation`) se recompile quand même.
    complete: Callable[[RenderInput], RenderInput] = _unchanged


def _cv_template_path(template: str) -> Path:
    """Template du CV. `template` vide -> `classic` : un brouillon d'avant les presets."""
    from jobtool.documents.cv.render import resolve_template

    return resolve_template(template or "classic")


def _letter_template_path(template: str) -> Path:
    """Template de la lettre, par son nom. `""` / `classic` -> `sobre` (brouillons anciens).

    Un nom inconnu lève LetterTemplateError : jamais de repli sur un autre template.
    """
    from jobtool.documents.cover_letter.registry import letter_template_path

    return letter_template_path(template)


def _normalizer(
    normalize: Callable[[RenderInput], RenderInput],
) -> Callable[[RenderInput], RenderInput]:
    """Traduit l'erreur de validation en SourceError lisible (chemin du champ + raison)."""
    from pydantic import ValidationError

    def run(data: RenderInput) -> RenderInput:
        try:
            return normalize(data)
        except ValidationError as exc:
            problems = [
                f"{'.'.join(str(part) for part in error['loc']) or 'source'} : "
                f"{error['msg']}"
                for error in exc.errors()[:5]
            ]
            raise SourceError("source invalide — " + " ; ".join(problems)) from None

    return run


def _build() -> dict[DocumentKind, DocumentKindPolicy]:
    """Construit la table. Fonction plutôt que littéral : les imports restent locaux."""
    from jobtool.documents.drafts.checks import (
        cv_ats_targets,
        cv_content_issues,
        letter_ats_targets,
        no_content_issues,
    )
    from jobtool.documents.cover_letter.source import normalize_letter_source
    from jobtool.documents.cv.source import normalize_cv_source
    from jobtool.documents.drafts.overrides import apply_cv, apply_letter

    return {
        "cv": DocumentKindPolicy(
            template_path=_cv_template_path,
            apply_overrides=apply_cv,
            ats_targets=cv_ats_targets,
            content_issues=cv_content_issues,
            needs_experience_base=True,
            normalize_source=_normalizer(normalize_cv_source),
        ),
        "letter": DocumentKindPolicy(
            template_path=_letter_template_path,
            apply_overrides=apply_letter,
            ats_targets=letter_ats_targets,
            content_issues=no_content_issues,
            needs_experience_base=False,
            normalize_source=_normalizer(normalize_letter_source),
            complete=normalize_letter_source,
        ),
    }


_POLICIES: dict[DocumentKind, DocumentKindPolicy] | None = None


def policy_for(kind: DocumentKind) -> DocumentKindPolicy:
    """La ligne de table d'un type de document. Lève sur un type inconnu, sans repli.

    Pas de valeur par défaut : un type inconnu est un défaut de programmation, et retomber
    silencieusement sur le CV produirait un document faux plutôt qu'une erreur.
    """
    global _POLICIES
    if _POLICIES is None:
        _POLICIES = _build()
    try:
        return _POLICIES[kind]
    except KeyError:
        raise ValueError(
            f"type de document inconnu : {kind!r} "
            f"(connus : {', '.join(sorted(_build()))})"
        ) from None


__all__ = ["DocumentKindPolicy", "RenderInput", "SourceError", "policy_for"]
