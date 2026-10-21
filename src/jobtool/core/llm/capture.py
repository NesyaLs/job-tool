"""Point d'accroche des échanges avec le modèle : le noyau publie, la couche service branche.

`core/llm/client.call_structured` est le SEUL endroit où les messages envoyés et la réponse
existent en entier — mais c'est du transport : il ne connaît ni compte, ni base, ni offre.
Il publie donc chaque appel ici, et qui veut les conserver installe un collecteur autour de
la génération (`services/llm_journal_service.py`).

Pourquoi une `ContextVar` plutôt qu'un paramètre passé de main en main : `call_structured`
est appelé au fond de `correction_loop`, `passe1`, `passe2`, `cv/generate` et `cv/fill`.
Faire descendre un enregistreur jusque-là obligerait cinq modules de DOMAINE à porter une
préoccupation d'observabilité qui ne les regarde pas. Une `ContextVar` est par ailleurs
isolée par thread et par tâche asynchrone : deux générations simultanées (FastAPI exécute
chaque route synchrone dans son propre thread) ne mélangent pas leurs échanges.

Sans collecteur installé — CLI, batch, tests de rendu — `publish` ne fait rien, comme
`usage_service.noop_recorder`. Ce module n'écrit RIEN par lui-même et ne dépend d'aucune
base : c'est une prise, pas un journal. Le log opt-in par fichiers (`JOBTOOL_LLM_LOG`) reste
un chemin séparé, inchangé.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExchangeRecord:
    """UN appel au modèle, tel qu'il est parti et tel qu'il est revenu.

    `messages` et `response` sont INTÉGRAUX (aucun troncage) : le journal sert justement à
    lire ce que le modèle a vraiment reçu. `messages` doit être une COPIE figée au moment
    de l'appel — la boucle de correction réutilise et ALLONGE la liste qu'elle passe.
    `step` et `attempt` situent l'appel dans la génération (routage, rédaction, n-ième
    correction).
    """

    model: str
    step: str
    attempt: int
    retries_429: int
    messages: list[dict]
    response: dict | None


#: Ce que reçoit un collecteur : un appel capturé. Il décide seul quoi en faire.
ExchangeSink = Callable[[ExchangeRecord], None]

_sink: ContextVar[ExchangeSink | None] = ContextVar("llm_exchange_sink", default=None)


@contextmanager
def capture_exchanges(sink: ExchangeSink) -> Iterator[None]:
    """Installe `sink` pour tous les appels au modèle faits DANS ce bloc.

    Le jeton rendu par `set` est réutilisé pour restaurer l'état précédent : deux blocs
    imbriqués se comportent comme une pile, et rien ne fuit hors du `with`.
    """
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def publish(record: ExchangeRecord) -> None:
    """Transmet un appel au collecteur installé, s'il y en a un. Ne lève JAMAIS.

    Un collecteur qui échoue (base injoignable) ne doit pas faire échouer une génération
    déjà payée : on journalise et on continue — même règle que `usage_service`.
    """
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(record)
    except Exception:  # noqa: BLE001 — l'observabilité ne casse jamais la génération
        logger.warning("échange avec le modèle non enregistré", exc_info=True)


__all__ = ["ExchangeRecord", "ExchangeSink", "capture_exchanges", "publish"]
