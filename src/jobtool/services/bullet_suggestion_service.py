"""Propositions de réalisations pour une expérience du profil (printless).

Reçoit tout déjà cloisonné sur le compte (base d'expériences, client du modèle, consommation,
journal), comme le service d'entretien. N'écrit JAMAIS dans la base : il renvoie des textes,
la personne choisit ce qu'elle ajoute.
"""

from __future__ import annotations

from collections.abc import Callable

from jobtool.core.experience_base.loader import BaseLoadError
from jobtool.core.experience_base.store import ExperienceBaseMissing, ExperienceBaseRepo
from jobtool.core.llm.client import MODEL
from jobtool.interview.bullets import TargetExperience, suggest_bullets
from jobtool.services.llm_journal_service import GenerationJournal, noop_journal
from jobtool.services.usage_service import UsageRecorder, noop_recorder

#: Type d'appel dans `llm_usage` et `llm_runs`.
USAGE_KIND = "bullets"


class SuggestionError(RuntimeError):
    """Le modèle n'a pas pu proposer (réseau, clé, réponse inexploitable)."""


class BulletSuggestionService:
    def __init__(
        self,
        repo: ExperienceBaseRepo,
        client_factory: Callable[[], object],
        record_usage: UsageRecorder = noop_recorder,
        journal: GenerationJournal = noop_journal,
    ) -> None:
        self._repo = repo
        self._client_factory = client_factory
        self._record_usage = record_usage
        self._journal = journal

    def suggest(self, target: TargetExperience, instruction: str = "") -> list[str]:
        """Puces proposées pour `target` (l'expérience telle qu'elle est à l'écran)."""
        try:
            base = self._repo.load()
        except (ExperienceBaseMissing, BaseLoadError):
            base = None  # profil neuf ou illisible : on propose sans contexte
        try:
            client = self._client_factory()
            with self._journal.record(USAGE_KIND):
                bullets = suggest_bullets(client, base, target, instruction)
        except Exception as exc:  # noqa: BLE001 — issue lisible, jamais un faux succès
            raise SuggestionError(f"proposition impossible : {exc}") from exc
        finally:
            self._record_usage(USAGE_KIND, MODEL)
        return bullets
