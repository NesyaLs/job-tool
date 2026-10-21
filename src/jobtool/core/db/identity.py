"""Identité des comptes au niveau de la base — partagée par tous les domaines.

`LOCAL_USER_ID` vit ici, et non dans un domaine, pour deux raisons :
  - les offres ET les documents en ont besoin, et un domaine ne doit jamais importer un
    autre domaine (aggregator ne connaît pas documents, et réciproquement) ;
  - c'est une notion de STOCKAGE, pas de métier : le domaine ne sait pas ce qu'est un
    compte, il reçoit un identifiant.

En production, l'identité vient de l'authentification. Cette constante ne sert qu'au mode
mono-poste (fichier SQLite local) et à la suite de tests, où il n'existe qu'un compte.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from jobtool.core.db.schema import users


class AccountConflict(RuntimeError):
    """Une identité authentifiée réclame un e-mail déjà rattaché à un autre compte."""


#: Compte unique du mode mono-poste (SQLite local et tests).
LOCAL_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def ensure_user(
    conn: Connection, user_id: uuid.UUID, *, email: str, name: str = ""
) -> None:
    """Crée le compte s'il n'existe pas (idempotent). Ne modifie jamais un compte existant."""
    found = conn.execute(select(users.c.id).where(users.c.id == user_id)).first()
    if found is None:
        conn.execute(
            insert(users).values(
                id=user_id,
                email=email,
                display_name=name,
                is_admin=False,
                created_at=datetime.now(UTC).isoformat(),
            )
        )


def resolve_account(
    conn: Connection, auth_id: str, email: str, name: str = ""
) -> uuid.UUID:
    """Identifiant INTERNE du compte correspondant à une identité authentifiée.

    Trois cas, dans cet ordre :
      1. `auth_id` déjà connu -> c'est le compte, on le renvoie ;
      2. sinon, un compte porte DÉJÀ cet e-mail -> on le RATTACHE (on inscrit l'auth_id).
         C'est ce qui permet à un compte créé avant l'authentification — celui de la
         reprise des données locales — de retrouver ses offres à la première connexion.
         Le rattachement par e-mail n'est légitime que parce que le fournisseur a VÉRIFIÉ
         cet e-mail : sans cette garantie, ce serait une prise de contrôle de compte ;
      3. sinon, c'est une inscription : on crée le compte.

    Aucune clé étrangère n'est touchée : l'identifiant interne ne change jamais.
    """
    email = email.strip().lower()

    by_auth = conn.execute(select(users.c.id).where(users.c.auth_id == auth_id)).first()
    if by_auth is not None:
        return by_auth.id

    by_email = conn.execute(
        select(users.c.id, users.c.auth_id).where(users.c.email == email)
    ).first()
    if by_email is not None:
        if by_email.auth_id is None:
            conn.execute(
                users.update().where(users.c.id == by_email.id).values(auth_id=auth_id)
            )
            conn.commit()
            return by_email.id
        # Même e-mail mais DÉJÀ rattaché à une autre identité : on refuse plutôt que de
        # deviner. Deux comptes distincts ne peuvent pas partager un e-mail.
        raise AccountConflict(
            f"l'e-mail {email} est déjà rattaché à une autre identité"
        )

    user_id = uuid.uuid4()
    conn.execute(
        insert(users).values(
            id=user_id,
            auth_id=auth_id,
            email=email,
            display_name=name,
            is_admin=False,  # l'administration ne s'auto-attribue pas
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    conn.commit()
    return user_id


def ensure_local_user(conn: Connection) -> uuid.UUID:
    """Garantit l'existence du compte mono-poste et le renvoie.

    Appelé par chaque `connect()` SQLite : les tables `documents` et `user_offers`
    référencent `users`, une base fraîche doit donc avoir ce compte avant toute écriture.
    """
    ensure_user(conn, LOCAL_USER_ID, email="local@jobtool", name="Poste local")
    return LOCAL_USER_ID
