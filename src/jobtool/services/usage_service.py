"""Enregistrement de la consommation du modèle (printless).

Pourquoi ce module existe. `core/db/schema.py` affirme depuis la création de la table :
« Écrite dès maintenant, pas “plus tard” : chaque génération coûte de l'argent réel, et une
consommation non enregistrée ne se rattrape jamais rétroactivement. » La table `llm_usage`,
son index, la colonne « Appels LLM » de l'administration et la tuile « Appels au modèle » du
front existaient bien — mais aucune ligne n'y a jamais été écrite. Les compteurs affichaient
zéro, et l'affirmation du schéma était fausse.

Une ligne par GÉNÉRATION (un CV, une lettre), et non par appel HTTP au modèle : c'est la
granularité que la colonne `kind` décrit, celle qu'un quota ou une facturation utiliseront,
et la seule que les couches appelantes connaissent honnêtement — une génération de CV
enchaîne deux appels structurés puis un nombre variable de rendus, que le domaine ne
remonte pas.

Les compteurs de jetons valent 0 tant que le client ne les remonte pas. C'est délibéré :
le NOMBRE d'appels est ce qui manquait le plus, et attendre les jetons pour commencer à
compter reviendrait à ne rien compter du tout.

Ne lève JAMAIS. Une comptabilité qui échoue ne doit pas faire échouer une génération déjà
payée — l'inverse ferait perdre le travail ET l'argent.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, insert

from jobtool.core.db import llm_usage, open_connection

logger = logging.getLogger(__name__)

#: Ce que reçoivent les services de génération : « note un appel de ce type, pour ce
#: modèle ». Ils ne connaissent ni base ni compte — c'est la composition qui les câble.
UsageRecorder = Callable[[str, str], None]


def noop_recorder(_kind: str, _model: str) -> None:
    """Enregistreur par défaut : n'écrit rien.

    Permet aux services de génération d'être construits sans base (CLI, tests de rendu)
    sans que chaque point d'appel ait à tester la présence d'un enregistreur.
    """


def record_llm_call(
    db: str | Path | Engine,
    user_id: uuid.UUID,
    *,
    kind: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    """Écrit une ligne de consommation. Journalise et poursuit en cas d'échec."""
    try:
        conn = open_connection(db)
        try:
            conn.execute(
                insert(llm_usage).values(
                    user_id=user_id,
                    kind=kind,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # la comptabilité ne fait jamais échouer la génération
        logger.warning("consommation du modèle non enregistrée", exc_info=True)


def recorder_for(db: str | Path | Engine, user_id: uuid.UUID) -> UsageRecorder:
    """Un enregistreur câblé sur une base et un compte, prêt à être injecté."""

    def record(kind: str, model: str) -> None:
        record_llm_call(db, user_id, kind=kind, model=model)

    return record


__all__ = ["UsageRecorder", "noop_recorder", "record_llm_call", "recorder_for"]
