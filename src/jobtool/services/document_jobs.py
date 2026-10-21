"""Génération des documents d'une offre EN ARRIÈRE-PLAN : CV puis lettre, sans bloquer l'écran.

Une tâche enchaîne les deux documents avec les templates par défaut du profil, par le MÊME
chemin que le tiroir (`DocumentEditService.open_document`) : un document déjà préparé pour
l'offre est rouvert, sans rappeler le modèle, et le résultat se retrouve ensuite tel quel
dans le tiroir.

Où vit l'état, et ses limites assumées : les tâches sont tenues EN MÉMOIRE du processus.
Elles ne servent qu'à suivre une génération en cours et à prévenir quand elle finit ; ce qui
dure, ce sont les documents, rangés en base comme les autres. Un redémarrage du serveur perd
donc le suivi (pas les documents déjà écrits), et plusieurs processus ne partagent pas leurs
tâches — acceptable pour un outil personnel ou à quelques comptes, servi par un seul
processus.

Couche SERVICE : aucun print. Les documents sont produits par des callbacks fournis par
l'appelant, qui a déjà cloisonné le service de documents sur le bon compte.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

KINDS = ("cv", "letter")

#: Propriétaire d'une tâche : le compte ET le profil (un profil ne voit pas les tâches d'un autre).
Owner = tuple[uuid.UUID, str]

#: Produit UN document et renvoie son identifiant. Lève en cas d'échec.
MakeDocument = Callable[[str], str]

#: Au-delà, une tâche terminée n'est plus listée (les documents, eux, restent).
KEEP_FINISHED = timedelta(hours=6)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class JobStep:
    kind: str
    status: str = "pending"  # pending | running | done | error
    doc_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class DocumentJob:
    id: str
    offer_id: str
    offer_title: str
    steps: tuple[JobStep, ...]
    started_at: datetime
    finished_at: datetime | None = None

    @property
    def status(self) -> str:
        """running tant qu'un document reste à faire ; sinon done, error ou partial."""
        if self.finished_at is None:
            return "running"
        failed = sum(step.status == "error" for step in self.steps)
        if failed == 0:
            return "done"
        return "error" if failed == len(self.steps) else "partial"


@dataclass
class _Entry:
    owner: Owner
    job: DocumentJob
    lock: threading.Lock = field(default_factory=threading.Lock)


class DocumentJobs:
    """Registre des tâches de TOUS les comptes du processus (un seul, partagé)."""

    def __init__(self, executor: Executor | None = None) -> None:
        # Deux générations à la fois au plus : chacune enchaîne des appels au modèle, dont
        # le quota par minute est la vraie limite.
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="documents"
        )
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def start(
        self, owner: Owner, offer_id: str, offer_title: str, make: MakeDocument
    ) -> DocumentJob:
        """Lance la génération des documents de l'offre ; renvoie la tâche.

        Une tâche déjà en cours pour la même offre est renvoyée telle quelle : un double
        clic ne lance pas deux générations.
        """
        with self._lock:
            self._forget_old()
            for entry in self._entries.values():
                if (
                    entry.owner == owner
                    and entry.job.offer_id == offer_id
                    and entry.job.finished_at is None
                ):
                    return entry.job
            job = DocumentJob(
                id=uuid.uuid4().hex[:16],
                offer_id=offer_id,
                offer_title=offer_title,
                steps=tuple(JobStep(kind) for kind in KINDS),
                started_at=_now(),
            )
            entry = _Entry(owner, job)
            self._entries[job.id] = entry
        self._executor.submit(self._run, entry, make)
        return self.get(owner, job.id) or job

    def list(self, owner: Owner) -> list[DocumentJob]:
        """Tâches du propriétaire, les plus récentes d'abord."""
        with self._lock:
            self._forget_old()
            jobs = [e.job for e in self._entries.values() if e.owner == owner]
        return sorted(jobs, key=lambda job: job.started_at, reverse=True)

    def get(self, owner: Owner, job_id: str) -> DocumentJob | None:
        with self._lock:
            entry = self._entries.get(job_id)
        return entry.job if entry is not None and entry.owner == owner else None

    # --- exécution ----------------------------------------------------------

    def _run(self, entry: _Entry, make: MakeDocument) -> None:
        for index, kind in enumerate(KINDS):
            self._set_step(entry, index, status="running")
            try:
                doc_id = make(kind)
            except Exception as exc:  # noqa: BLE001 — l'échec est rapporté, la lettre est tentée
                self._set_step(
                    entry, index, status="error", error=str(exc) or type(exc).__name__
                )
            else:
                self._set_step(entry, index, status="done", doc_id=doc_id)
        with entry.lock:
            entry.job = replace(entry.job, finished_at=_now())

    @staticmethod
    def _set_step(entry: _Entry, index: int, **changes: object) -> None:
        with entry.lock:
            steps = list(entry.job.steps)
            steps[index] = replace(steps[index], **changes)  # type: ignore[arg-type]
            entry.job = replace(entry.job, steps=tuple(steps))

    def _forget_old(self) -> None:
        limit = _now() - KEEP_FINISHED
        for job_id in [
            job_id
            for job_id, entry in self._entries.items()
            if entry.job.finished_at is not None and entry.job.finished_at < limit
        ]:
            del self._entries[job_id]
