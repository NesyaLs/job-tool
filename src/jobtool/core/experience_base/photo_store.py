"""Dépôt de la photo de profil : une ligne de `profile_photos` par compte.

Même découpage que `store.py` pour la base d'expériences : le stockage est isolé derrière
un objet cloisonné par `user_id`, que les couches au-dessus reçoivent déjà construit. Le
`user_id` est EXPLICITE et sans valeur par défaut — un oubli de câblage doit être une
TypeError immédiate, jamais la photo d'un autre compte.

La photo stockée est TOUJOURS normalisée (cf. `photo.normalize_photo`) : ce module ne
valide rien, il range ce qu'on lui donne. La validation a lieu une seule fois, à l'envoi.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from jobtool.core.db import open_connection, profile_photos


@dataclass(frozen=True)
class StoredPhoto:
    """Une photo telle qu'elle est rangée : son empreinte et ses octets JPEG."""

    sha256: str
    jpeg: bytes


class PhotoStore:
    """Photo de profil d'UN compte."""

    def __init__(
        self, db: str | Path | Engine, user_id: uuid.UUID, *, profile_key: str
    ) -> None:
        """`db` : moteur partagé OU chemin de la base SQLite locale.

        On passe par `open_connection`, le point d'entrée unique qui tranche entre les deux
        modes : chaque appelant qui referait le test `isinstance(..., Engine)` de son côté
        finirait par écrire dans l'autre base, silencieusement.

        `profile_key` : "" pour le profil principal (table `profile_photos`), sinon la clé
        d'un profil supplémentaire, dont la photo vit dans sa propre ligne de `profiles`.
        Obligatoire, pour la même raison que `user_id`.
        """
        from jobtool.core.experience_base.profiles import ExtraProfileBase, parse_key

        self._db = db
        self._user_id = user_id
        profile_id = parse_key(profile_key)
        self._extra = (
            None if profile_id is None else ExtraProfileBase(db, user_id, profile_id)
        )

    def load(self) -> StoredPhoto | None:
        """La photo du compte, ou None s'il n'en a pas (cas parfaitement normal)."""
        if self._extra is not None:
            found = self._extra.load_photo()
            return None if found is None else StoredPhoto(*found)
        with open_connection(self._db) as conn:
            row = conn.execute(
                select(profile_photos.c.sha256, profile_photos.c.jpeg).where(
                    profile_photos.c.user_id == self._user_id
                )
            ).first()
        return None if row is None else StoredPhoto(row.sha256, bytes(row.jpeg))

    def load_if(self, sha256: str | None) -> StoredPhoto | None:
        """La photo du compte SI son empreinte est bien celle attendue, sinon None.

        Un profil référence une VERSION précise de l'image. Si la photo a été remplacée
        depuis, l'empreinte ne correspond plus : mieux vaut rendre le CV sans photo que
        d'en afficher une autre sans prévenir.
        """
        if not sha256:
            return None
        photo = self.load()
        return photo if photo is not None and photo.sha256 == sha256 else None

    def save(self, photo: StoredPhoto) -> None:
        """Crée ou remplace la photo du compte, en une transaction."""
        if self._extra is not None:
            self._extra.save_photo((photo.sha256, photo.jpeg))
            return
        now = datetime.now(UTC).isoformat()
        with open_connection(self._db) as conn:
            changed = conn.execute(
                update(profile_photos)
                .where(profile_photos.c.user_id == self._user_id)
                .values(sha256=photo.sha256, jpeg=photo.jpeg, updated_at=now)
            ).rowcount
            if not changed:
                conn.execute(
                    insert(profile_photos).values(
                        user_id=self._user_id,
                        sha256=photo.sha256,
                        jpeg=photo.jpeg,
                        updated_at=now,
                    )
                )
            conn.commit()

    def delete(self) -> bool:
        """Supprime la photo ; True si une ligne a été retirée.

        Une photo est une donnée personnelle : sa suppression doit être possible seule,
        sans passer par la suppression du compte.
        """
        if self._extra is not None:
            had = self._extra.load_photo() is not None
            self._extra.save_photo(None)
            return had
        with open_connection(self._db) as conn:
            removed = conn.execute(
                delete(profile_photos).where(profile_photos.c.user_id == self._user_id)
            ).rowcount
            conn.commit()
        return bool(removed)
