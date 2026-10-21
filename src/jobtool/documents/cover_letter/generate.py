"""Pipeline de bout en bout de la lettre : passe 1 -> passe 2 -> assemblage du rendu.

Orchestration SPÉCIFIQUE lettre, alignée sur cv/generate.py : enchaîne les deux passes LLM
ÉTANCHES (chacune avec sa propre boucle de correction, déjà câblée dans passe1/passe2) puis
assemble le CoverLetterRender déterministe. Ne rend PAS le PDF ici (c'est le rôle de
cover_letter/render.py, appelé par le script CLI) : on sépare génération et rendu, comme le CV.

L'appel LLM vit dans make_client() à l'intérieur des passes : rien ne part au réseau à l'import.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import NamedTuple

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.llm.client import RetryFn
from jobtool.core.traceability import Violation
from jobtool.documents.cover_letter.assemble import build_cover_letter_render
from jobtool.documents.cover_letter.passe1 import run_passe1
from jobtool.documents.cover_letter.passe2 import run_passe2
from jobtool.documents.cover_letter.schema import (
    CoverLetterDraftOutput,
    CoverLetterRender,
    CoverLetterRoutingOutput,
)

#: Fin d'une tentative d'une passe : (n° de passe 1|2, n° de tentative, violations restantes).
#: Le n° de passe permet à l'interface de distinguer routage et rédaction à l'affichage ;
#: on transmet la LISTE de violations, pas un compte ni une phrase toute faite.
LetterAttemptFn = Callable[[int, int, list], None]


class CoverLetterResult(NamedTuple):
    """Sortie du pipeline : le rendu prêt + les décisions et violations restantes des 2 passes.

    `routing_issues` / `draft_issues` vides == chaque passe a convergé. L'appelant (CLI) décide
    quoi en faire (rendre ou signaler la non-convergence), comme le CV expose son rapport.
    """

    render: CoverLetterRender
    routing: CoverLetterRoutingOutput
    draft: CoverLetterDraftOutput
    routing_issues: list[Violation]
    draft_issues: list[Violation]


def generate_cover_letter(
    base: ExperienceBase,
    job_offer: str,
    max_retries: int = 2,
    examples_dir: Path | None = None,
    today: date | None = None,
    on_attempt: LetterAttemptFn | None = None,
    on_retry: RetryFn | None = None,
) -> CoverLetterResult:
    """Enchaîne passe 1 (routage) -> passe 2 (rédaction) -> assemblage du CoverLetterRender.

    Étanchéité : la passe 2 REÇOIT le routage de la passe 1, elle ne le refait pas. Ne lève
    pas sur violations : elles sont remontées dans le résultat pour que l'appelant décide.

    `on_attempt(passe, tentative, issues)` reçoit 1 ou 2 selon la passe, ce qui permet à
    l'interface de les distinguer à l'affichage. Absent (défaut) -> AUCUNE sortie : ce module
    ne parle pas à l'utilisateur. `on_retry` est relayé au transport (attentes après un 429).
    """
    p1 = (lambda a, _parsed, issues: on_attempt(1, a, issues)) if on_attempt else None
    p2 = (lambda a, _parsed, issues: on_attempt(2, a, issues)) if on_attempt else None

    routing, routing_issues = run_passe1(
        base, job_offer, max_retries=max_retries, on_attempt=p1, on_retry=on_retry
    )
    draft, draft_issues = run_passe2(
        base,
        routing,
        job_offer,
        max_retries=max_retries,
        examples_dir=examples_dir,
        on_attempt=p2,
        on_retry=on_retry,
    )
    render = build_cover_letter_render(base, routing, draft, today=today)
    return CoverLetterResult(render, routing, draft, routing_issues, draft_issues)
