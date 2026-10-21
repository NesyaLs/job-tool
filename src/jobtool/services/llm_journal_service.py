"""Journal des échanges avec le modèle : enregistrer une génération, et purger les anciennes.

Ce que ce module ajoute à `usage_service` : celui-ci COMPTE les générations (pour facturer
et poser des quotas), celui-ci en garde la TRACE (ce qui est parti au modèle, ce qui en est
revenu) pour l'onglet d'administration. Les deux vivent côte à côte et ne se remplacent pas
— la comptabilité doit survivre à la purge du journal.

Il branche le point d'accroche publié par `core/llm/capture` : la génération elle-même n'est
pas touchée, elle ne sait même pas qu'on l'écoute.

CONTENU CLIENT : les lignes écrites ici contiennent la base d'expériences du compte, la
description de l'offre et le texte généré. C'est assumé (lecture réservée à
l'administration), et borné par la purge : `JOURNAL_KEEP_PER_KIND` générations PAR TYPE,
tous comptes confondus.

Ne lève JAMAIS. Une génération déjà payée ne doit pas échouer parce que son journal n'a pas
pu s'écrire : tout échec est journalisé par `logging`, et la génération continue.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, delete, insert, select, update

from jobtool.core.db import llm_exchanges, llm_runs, open_connection
from jobtool.core.llm.capture import ExchangeRecord, capture_exchanges

logger = logging.getLogger(__name__)

#: Nombre de générations conservées PAR TYPE (cv, letter), tous comptes confondus.
#: Au-delà, les plus anciennes sont supprimées avec leurs échanges. Seuil unique, ici.
JOURNAL_KEEP_PER_KIND = 10

#: Un message d'erreur plus long que ça n'apprend plus rien et gonfle la table.
MAX_ERROR_CHARS = 1000


def _now() -> str:
    return datetime.now(UTC).isoformat()


class GenerationJournal:
    """Interface reçue par les services de génération : « enregistre cette génération ».

    Implémentation par défaut : ne rien écrire. Elle permet de construire un service de
    génération SANS base (CLI, batch, tests de rendu) sans que chaque point d'appel ait à
    tester la présence d'un journal — même rôle que `usage_service.noop_recorder`.
    """

    @contextmanager
    def record(
        self, kind: str, *, offer_id: str | None = None, template: str = ""
    ) -> Iterator[None]:
        """Enveloppe une génération. Par défaut, ne fait rien du tout."""
        yield


#: Journal inactif, prêt à être utilisé comme valeur par défaut.
noop_journal = GenerationJournal()


@dataclass(frozen=True)
class DbGenerationJournal(GenerationJournal):
    """Journal qui écrit dans la base, pour un compte donné.

    Une connexion par écriture (2 à 6 par génération), comme `record_llm_call` : garder une
    connexion ouverte pendant toute une génération — qui dure une minute — coûterait plus
    cher au pool que ces quelques allers-retours, négligeables face à l'appel au modèle.
    """

    db: str | Path | Engine
    user_id: uuid.UUID

    @contextmanager
    def record(
        self, kind: str, *, offer_id: str | None = None, template: str = ""
    ) -> Iterator[None]:
        """Ouvre une génération, capture ses appels au modèle, la referme, puis purge.

        L'exception d'une génération qui échoue est TOUJOURS relevée telle quelle : le
        journal la note (`status = error`), il ne l'avale pas.
        """
        run_id = uuid.uuid4().hex
        started = self._open(run_id, kind, offer_id, template)
        state = _RunState()
        try:
            with capture_exchanges(state.collect):
                yield
        except Exception as exc:
            if started:
                self._close(run_id, state, error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            if started:
                self._close(run_id, state, error=None)
        finally:
            if started:
                self._purge(kind)

    # --- écritures (chacune best-effort) ------------------------------------

    def _open(
        self, run_id: str, kind: str, offer_id: str | None, template: str
    ) -> bool:
        """Insère la génération en cours. False si l'écriture a échoué (on n'écrira rien)."""
        return self._write(
            insert(llm_runs).values(
                run_id=run_id,
                user_id=self.user_id,
                kind=kind,
                offer_id=offer_id,
                template=template,
                model="",
                status="running",
                calls=0,
                started_at=_now(),
            ),
            "génération non enregistrée au journal",
        )

    def _close(self, run_id: str, state: _RunState, *, error: str | None) -> None:
        self._write(
            update(llm_runs)
            .where(llm_runs.c.run_id == run_id)
            .values(
                status="error" if error else "ok",
                error=error[:MAX_ERROR_CHARS] if error else None,
                calls=state.calls,
                model=state.model,
                ended_at=_now(),
            ),
            "fin de génération non enregistrée au journal",
        )
        for seq, record in enumerate(state.records, start=1):
            self._write(
                insert(llm_exchanges).values(
                    run_id=run_id,
                    seq=seq,
                    step=record.step,
                    attempt=record.attempt,
                    retries_429=record.retries_429,
                    messages=record.messages,
                    response=record.response,
                    created_at=_now(),
                ),
                "échange non enregistré au journal",
            )

    def _purge(self, kind: str) -> None:
        """Ne garde que les `JOURNAL_KEEP_PER_KIND` dernières générations de CE type.

        Deux quotas séparés (un par type) : dix CV d'affilée n'effacent aucune lettre. La
        suppression est idempotente — deux purges simultanées ne se gênent pas.
        """
        try:
            conn = open_connection(self.db)
            try:
                keep = select(llm_runs.c.run_id).where(
                    llm_runs.c.kind == kind,
                    llm_runs.c.run_id.in_(
                        select(llm_runs.c.run_id)
                        .where(llm_runs.c.kind == kind)
                        .order_by(llm_runs.c.started_at.desc())
                        .limit(JOURNAL_KEEP_PER_KIND)
                    ),
                )
                doomed = select(llm_runs.c.run_id).where(
                    llm_runs.c.kind == kind, llm_runs.c.run_id.not_in(keep)
                )
                # Les échanges d'abord : la clé étrangère refuserait l'ordre inverse.
                conn.execute(
                    delete(llm_exchanges).where(llm_exchanges.c.run_id.in_(doomed))
                )
                conn.execute(
                    delete(llm_runs).where(
                        llm_runs.c.kind == kind, llm_runs.c.run_id.not_in(keep)
                    )
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — la purge ne fait jamais échouer une génération
            logger.warning("purge du journal des échanges impossible", exc_info=True)

    def _write(self, statement, failure: str) -> bool:
        """Exécute une écriture. Journalise et renvoie False en cas d'échec."""
        try:
            conn = open_connection(self.db)
            try:
                conn.execute(statement)
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — l'observabilité ne casse jamais la génération
            logger.warning(failure, exc_info=True)
            return False
        return True


@dataclass
class _RunState:
    """Ce qu'on retient d'une génération pendant qu'elle se déroule.

    Les échanges sont gardés en mémoire puis écrits à la fermeture : ils ne comptent que
    quelques kilo-octets, et une génération interrompue n'a jamais laissé de ligne
    d'échange rattachée à une génération qui n'existe pas encore.
    """

    records: list[ExchangeRecord] = field(default_factory=list)
    model: str = ""

    @property
    def calls(self) -> int:
        return len(self.records)

    def collect(self, record: ExchangeRecord) -> None:
        self.records.append(record)
        self.model = record.model or self.model


def journal_for(db: str | Path | Engine, user_id: uuid.UUID) -> GenerationJournal:
    """Un journal câblé sur une base et un compte, prêt à être injecté."""
    return DbGenerationJournal(db=db, user_id=user_id)


__all__ = [
    "JOURNAL_KEEP_PER_KIND",
    "DbGenerationJournal",
    "GenerationJournal",
    "journal_for",
    "noop_journal",
]
