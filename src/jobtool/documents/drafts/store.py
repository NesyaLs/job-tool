"""Stockage des brouillons de documents (CV/lettre retouchés à la main).

Une ligne par document, dans la table `documents` de la base applicative. Comme pour les
offres, `user_id` est un paramètre EXPLICITE et sans défaut : toute lecture et toute
écriture sont cloisonnées par compte, et un oubli d'appelant est une TypeError plutôt
qu'une fuite silencieuse.

`doc_id` reste la clé : c'est lui qui circule dans les URL. Le compte est vérifié EN PLUS
à chaque accès — connaître un identifiant ne suffit jamais à lire le document d'autrui.

Pourquoi persister côté serveur plutôt que dans le navigateur : le texte retouché est
l'ENTRÉE de la prochaine compilation Typst, qui tourne ici. Le garder côté client
obligerait à le renvoyer à chaque rendu et le perdrait au moindre rechargement d'onglet.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection, Row

from jobtool.core.db import connect_sqlite, documents, open_connection
from jobtool.documents.drafts.models import DocumentDraft

__all__ = [
    "connect",
    "delete_for_profile",
    "get_draft",
    "latest_for_offer",
    "now_iso",
    "set_template",
    "open_connection",
    "save_draft",
    "set_edited_input",
    "set_overrides",
]


def now_iso() -> str:
    """Horodatage UTC ISO 8601 (source unique pour created_at / updated_at)."""
    return datetime.now(UTC).isoformat()


#: Ouverture de connexion : définie dans core/db, parce que les offres en ont besoin aussi
#: et qu'un domaine n'importe jamais un autre domaine (ce module avait sa propre copie, au
#: mot près, de celle de `aggregator/storage`). Ré-exportée sous ses noms d'usage.
connect = connect_sqlite


def _row_to_draft(row: Row[Any]) -> DocumentDraft:
    return DocumentDraft(
        doc_id=row.doc_id,
        offer_id=row.offer_id,
        kind=row.kind,
        template=row.template,
        render_input=row.render_input,
        original_texts=row.original_texts,
        overrides=row.overrides,
        edited_input=row.edited_input,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def save_draft(
    conn: Connection, user_id: uuid.UUID, draft: DocumentDraft, *, profile_key: str
) -> None:
    """Insère ou met à jour un brouillon (idempotent sur doc_id).

    `profile_key` : profil qui a généré le document ("" = principal), posé à la création.
    """
    exists = conn.execute(
        select(documents.c.doc_id).where(
            documents.c.doc_id == draft.doc_id, documents.c.user_id == user_id
        )
    ).first()
    values = {
        "render_input": draft.render_input,
        "original_texts": draft.original_texts,
        "overrides": draft.overrides,
        "edited_input": draft.edited_input,
        "template": draft.template,
        "updated_at": draft.updated_at,
    }
    if exists is None:
        conn.execute(
            insert(documents).values(
                doc_id=draft.doc_id,
                user_id=user_id,
                offer_id=draft.offer_id,
                kind=draft.kind,
                profile_key=profile_key,
                created_at=draft.created_at,
                **values,
            )
        )
    else:
        conn.execute(
            update(documents)
            .where(documents.c.doc_id == draft.doc_id, documents.c.user_id == user_id)
            .values(**values)
        )
    conn.commit()


def get_draft(
    conn: Connection, user_id: uuid.UUID, doc_id: str, *, profile_key: str
) -> DocumentDraft | None:
    """Charge un brouillon DU COMPTE et DU PROFIL par son id, ou None.

    Le profil compte autant que le compte : un document se contrôle contre la base
    d'expériences du profil qui l'a généré. Ouvert sous un autre profil, il serait comparé
    à la mauvaise base — il y est donc introuvable.
    """
    row = conn.execute(
        select(documents).where(
            documents.c.doc_id == doc_id,
            documents.c.user_id == user_id,
            documents.c.profile_key == profile_key,
        )
    ).first()
    return _row_to_draft(row) if row is not None else None


def set_overrides(
    conn: Connection, user_id: uuid.UUID, doc_id: str, overrides: dict[str, str]
) -> bool:
    """Remplace la table des retouches d'un brouillon. False si l'id est inconnu du compte."""
    changed = (
        conn.execute(
            update(documents)
            .where(documents.c.doc_id == doc_id, documents.c.user_id == user_id)
            .values(overrides=overrides, updated_at=now_iso())
        ).rowcount
        > 0
    )
    conn.commit()
    return changed


def set_edited_input(
    conn: Connection, user_id: uuid.UUID, doc_id: str, data: dict | None
) -> bool:
    """Pose (ou retire, avec None) la source réécrite du mode pro. False si id inconnu.

    Écriture SÉPARÉE de `set_overrides` à dessein : les deux colonnes ne se touchent
    jamais, donc quitter le mode pro rend au document ses retouches de zones intactes.
    """
    changed = (
        conn.execute(
            update(documents)
            .where(documents.c.doc_id == doc_id, documents.c.user_id == user_id)
            .values(edited_input=data, updated_at=now_iso())
        ).rowcount
        > 0
    )
    conn.commit()
    return changed


def set_template(
    conn: Connection, user_id: uuid.UUID, doc_id: str, template: str
) -> bool:
    """Change le template d'un brouillon (texte et retouches intacts). False si id inconnu."""
    changed = (
        conn.execute(
            update(documents)
            .where(documents.c.doc_id == doc_id, documents.c.user_id == user_id)
            .values(template=template, updated_at=now_iso())
        ).rowcount
        > 0
    )
    conn.commit()
    return changed


def latest_for_offer(
    conn: Connection,
    user_id: uuid.UUID,
    offer_id: str,
    kind: str,
    templates: tuple[str, ...] | None = None,
    *,
    profile_key: str,
) -> DocumentDraft | None:
    """Dernier brouillon (par date de mise à jour) d'une offre, pour un type de document.

    Permet de RETROUVER une retouche en cours au lieu d'en repartir de zéro — donc de ne
    pas relancer inutilement le LLM quand l'utilisateur rouvre l'écran d'édition.

    `templates` (optionnel) restreint aux brouillons dont le champ `template` vaut l'une
    de ces valeurs : l'appelant choisit les équivalences (ex. « » et « classic »).
    """
    conditions = [
        documents.c.user_id == user_id,
        documents.c.profile_key == profile_key,
        documents.c.offer_id == offer_id,
        documents.c.kind == kind,
    ]
    if templates is not None:
        conditions.append(documents.c.template.in_(templates))
    row = conn.execute(
        select(documents)
        .where(*conditions)
        .order_by(documents.c.updated_at.desc())
        .limit(1)
    ).first()
    return _row_to_draft(row) if row is not None else None


def delete_for_profile(
    conn: Connection, user_id: uuid.UUID, profile_key: str
) -> list[str]:
    """Supprime les brouillons d'un profil (supprimé) ; renvoie leurs ids.

    Deux requêtes quel que soit le nombre de documents. Les ids servent à l'appelant pour
    effacer les dossiers de travail correspondants.
    """
    where = (documents.c.user_id == user_id, documents.c.profile_key == profile_key)
    ids = [r.doc_id for r in conn.execute(select(documents.c.doc_id).where(*where))]
    if ids:
        conn.execute(delete(documents).where(*where))
    conn.commit()
    return ids
