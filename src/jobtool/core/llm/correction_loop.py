"""Boucle de correction bornée, générique et transverse.

Mécanique seulement : on génère une sortie structurée, on la passe à un `check`
fourni par l'appelant, et tant qu'il reste des problèmes (et des essais), on
renvoie au modèle SA tentative précédente + un message de correction (construit
par l'appelant), au plus `max_retries` fois. Le code ne « répare » jamais la
sortie : il détecte et redemande. La nature des `issues` (violations, dépassements
de budget, …) est opaque ici — seul l'appelant l'interprète.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from jobtool.core.llm.client import RetryFn, call_structured

T = TypeVar("T", bound=BaseModel)


def run_correction_loop(
    client,
    messages: list[dict],
    response_format: type[T],
    check: Callable[[T], list],
    build_correction: Callable[[list], str],
    max_retries: int = 2,
    step: str = "",
    on_attempt: Callable[[int, T, list], None] | None = None,
    on_retry: RetryFn | None = None,
) -> tuple[T, list]:
    """Génère puis recorrige tant que `check` renvoie des `issues` (borné).

    Retourne (dernière_sortie, issues_restantes). Ne lève pas sur issues :
    l'appelant voit le rapport et décide (sortie OK ssi issues est vide).

    `on_attempt` / `on_retry` sont de simples relais d'observabilité (no-op si absents) :
    la boucle ne produit aucune sortie par elle-même.

    `step` NOMME l'étape métier (« routage », « redaction », « cv_a »…). C'est le seul
    endroit qui connaisse à la fois l'étape et le numéro de tentative : les transmettre
    ici évite que chaque appelant ait à compter ses propres appels au modèle.
    """
    parsed = call_structured(
        client,
        messages,
        response_format,
        log_slug=step,
        log_attempt=1,
        on_retry=on_retry,
    )
    issues = check(parsed)
    attempt = 1
    if on_attempt is not None:
        on_attempt(attempt, parsed, issues)

    while issues and attempt <= max_retries:
        # Le modèle voit sa tentative précédente puis tout ce qui ne va pas,
        # dans un seul message. Le code ne corrige rien lui-même.
        messages.append(
            {"role": "assistant", "content": parsed.model_dump_json(indent=2)}
        )
        messages.append({"role": "user", "content": build_correction(issues)})

        parsed = call_structured(
            client,
            messages,
            response_format,
            log_slug=step,
            log_attempt=attempt + 1,
            on_retry=on_retry,
        )
        issues = check(parsed)
        attempt += 1
        if on_attempt is not None:
            on_attempt(attempt, parsed, issues)

    return parsed, issues
