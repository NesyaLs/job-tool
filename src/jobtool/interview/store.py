"""Persistance de la session d'entretien d'UN compte et d'UN profil (`interview_sessions`).

Même forme que `core/experience_base/photo_store.py` : un objet cloisonné par `user_id`,
EXPLICITE et sans valeur par défaut — un oubli de câblage est une TypeError, jamais la
session d'un autre compte. `db` est un moteur partagé ou le chemin de la base SQLite locale,
tranché par `open_connection`.

Une session illisible (schéma de session changé entre deux versions) n'est pas une erreur
fatale : elle est traitée comme absente, et l'utilisateur recommence un entretien. Le
profil, lui, n'est jamais touché par ce module.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.engine import Engine

from jobtool.core.db import interview_sessions, open_connection
from jobtool.interview.models import InterviewSession, now_iso

logger = logging.getLogger(__name__)


class InterviewSessionStore:
    """Session d'entretien d'UN compte."""

    def __init__(
        self, db: str | Path | Engine, user_id: uuid.UUID, *, profile_key: str
    ) -> None:
        """`profile_key` : un entretien par profil ("" = profil principal). Obligatoire,
        comme `user_id` : la conversation d'un profil n'alimente jamais un autre profil."""
        self._db = db
        self._user_id = user_id
        self._profile_key = profile_key

    def _where(self):
        return and_(
            interview_sessions.c.user_id == self._user_id,
            interview_sessions.c.profile_key == self._profile_key,
        )

    def load(self) -> InterviewSession | None:
        with open_connection(self._db) as conn:
            row = conn.execute(
                select(interview_sessions.c.data).where(self._where())
            ).first()
        if row is None:
            return None
        try:
            return InterviewSession.model_validate(row.data)
        except ValidationError:
            logger.warning("session d'entretien illisible, ignorée", exc_info=True)
            return None

    def save(self, session: InterviewSession) -> None:
        """Crée ou remplace la session du compte : une transaction, une ligne."""
        session.updated_at = now_iso()
        data = session.model_dump(mode="json")
        with open_connection(self._db) as conn:
            changed = conn.execute(
                update(interview_sessions)
                .where(self._where())
                .values(data=data, updated_at=session.updated_at)
            ).rowcount
            if not changed:
                conn.execute(
                    insert(interview_sessions).values(
                        user_id=self._user_id,
                        profile_key=self._profile_key,
                        data=data,
                        created_at=session.created_at,
                        updated_at=session.updated_at,
                    )
                )
            conn.commit()

    def delete(self) -> None:
        with open_connection(self._db) as conn:
            conn.execute(delete(interview_sessions).where(self._where()))
            conn.commit()


__all__ = ["InterviewSessionStore"]
