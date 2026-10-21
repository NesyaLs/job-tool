"""Profils multiples d'un compte : le principal, et des profils SUPPLÉMENTAIRES (administrateurs).

Un profil est une base d'expériences complète. Le profil PRINCIPAL est celui qui existait
avant cette fonctionnalité : son stockage ne change pas (`experience_bases`, ou le YAML en
mono-poste). Les profils supplémentaires sont des lignes de `profiles`, photo comprise.

Une CLÉ de profil désigne l'un ou l'autre partout où c'est nécessaire (documents, entretien,
photo) : `PRINCIPAL_KEY` (chaîne vide) pour le principal, l'id de la ligne sinon. Une chaîne
plutôt qu'un `None` : elle sert de colonne de clé primaire, qui ne peut pas être NULL.

Qui y a droit : les administrateurs, et le poste local (mono-poste : son seul utilisateur est
son propriétaire). Cette décision est prise en UN endroit, `resolve_active_profile`, qui lit
en une requête le drapeau administrateur et le profil actif — un aller-retour par requête,
pas deux. Un compte qui perd ses droits retombe sur son profil principal ; ses autres profils
restent en base, inaccessibles, jamais supprimés en silence.

`user_id` est EXPLICITE et sans défaut, comme dans tous les modules de stockage.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.engine import Engine

from jobtool.core.db import open_connection, profiles, users
from jobtool.core.experience_base.loader import BaseLoadError
from jobtool.core.experience_base.models import ExperienceBase

#: Clé du profil principal.
PRINCIPAL_KEY = ""
#: Nom affiché du profil principal (il n'est pas renommable : il n'a pas de ligne à lui).
PRINCIPAL_NAME = "Profil principal"
#: Nombre maximal de profils SUPPLÉMENTAIRES par compte.
MAX_EXTRA_PROFILES = 20
#: Longueur maximale d'un nom de profil.
MAX_NAME_CHARS = 60


class ProfileNotFound(LookupError):
    """Clé de profil inconnue pour ce compte."""


class ProfileLimitReached(ValueError):
    """Le compte a déjà `MAX_EXTRA_PROFILES` profils supplémentaires."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def parse_key(key: str) -> uuid.UUID | None:
    """Clé -> id de ligne (None pour le principal). Lève `ProfileNotFound` si illisible."""
    if key == PRINCIPAL_KEY:
        return None
    try:
        return uuid.UUID(key)
    except ValueError:
        raise ProfileNotFound(key) from None


def clean_name(name: str) -> str:
    value = " ".join(name.split())
    if not value:
        raise ValueError("le nom du profil est vide")
    if len(value) > MAX_NAME_CHARS:
        raise ValueError(f"nom trop long ({MAX_NAME_CHARS} caractères au maximum)")
    return value


# --- Résolution du profil actif -------------------------------------------------


@dataclass(frozen=True)
class ActiveProfile:
    """Ce qu'une requête doit savoir : a-t-on droit aux profils, et lequel est actif."""

    allowed: bool
    key: str


def resolve_active_profile(
    db: str | Path | Engine, user_id: uuid.UUID
) -> ActiveProfile:
    """Profil actif du compte, en UNE requête (drapeau admin + profil actif vérifié).

    Mono-poste (`db` est un chemin) : autorisé. Base partagée : réservé aux administrateurs.
    Un profil actif qui n'existe plus, ou un compte non autorisé : profil principal.
    """
    local = not isinstance(db, Engine)
    with open_connection(db) as conn:
        row = conn.execute(
            select(users.c.is_admin, profiles.c.id)
            .select_from(
                users.outerjoin(
                    profiles,
                    and_(
                        profiles.c.id == users.c.active_profile_id,
                        profiles.c.user_id == users.c.id,
                    ),
                )
            )
            .where(users.c.id == user_id)
        ).first()
    if row is None:
        return ActiveProfile(allowed=local, key=PRINCIPAL_KEY)
    allowed = local or bool(row.is_admin)
    key = str(row.id) if allowed and row.id is not None else PRINCIPAL_KEY
    return ActiveProfile(allowed=allowed, key=key)


# --- Registre des profils d'un compte ---------------------------------------------


@dataclass(frozen=True)
class ProfileInfo:
    key: str
    name: str
    principal: bool
    created_at: str | None = None


class ProfileRegistry:
    """Profils supplémentaires d'UN compte, et son profil actif."""

    def __init__(self, db: str | Path | Engine, user_id: uuid.UUID) -> None:
        self._db = db
        self._user_id = user_id

    def list(self) -> list[ProfileInfo]:
        """Le principal en tête, puis les supplémentaires par date de création."""
        with open_connection(self._db) as conn:
            rows = conn.execute(
                select(profiles.c.id, profiles.c.name, profiles.c.created_at)
                .where(profiles.c.user_id == self._user_id)
                .order_by(profiles.c.created_at)
            ).all()
        return [ProfileInfo(PRINCIPAL_KEY, PRINCIPAL_NAME, True)] + [
            ProfileInfo(str(r.id), r.name, False, r.created_at) for r in rows
        ]

    def create(
        self,
        name: str,
        base: ExperienceBase,
        photo: tuple[str, bytes] | None = None,
    ) -> str:
        """Crée un profil supplémentaire et renvoie sa clé."""
        name = clean_name(name)
        profile_id = uuid.uuid4()
        now = _now()
        with open_connection(self._db) as conn:
            count = len(
                conn.execute(
                    select(profiles.c.id).where(profiles.c.user_id == self._user_id)
                ).all()
            )
            if count >= MAX_EXTRA_PROFILES:
                raise ProfileLimitReached(
                    f"{MAX_EXTRA_PROFILES} profils supplémentaires au maximum"
                )
            conn.execute(
                insert(profiles).values(
                    id=profile_id,
                    user_id=self._user_id,
                    name=name,
                    data=base.model_dump(mode="json", exclude_none=True),
                    photo_sha256=photo[0] if photo else None,
                    photo_jpeg=photo[1] if photo else None,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.commit()
        return str(profile_id)

    def rename(self, key: str, name: str) -> None:
        profile_id = self._extra(key)
        name = clean_name(name)
        with open_connection(self._db) as conn:
            changed = conn.execute(
                update(profiles)
                .where(profiles.c.id == profile_id, profiles.c.user_id == self._user_id)
                .values(name=name, updated_at=_now())
            ).rowcount
            conn.commit()
        if not changed:
            raise ProfileNotFound(key)

    def delete(self, key: str) -> None:
        """Supprime un profil supplémentaire (et sa photo). S'il était actif, le principal
        le redevient. Les données qui s'y rattachent ailleurs (documents, entretien) sont
        supprimées par l'appelant, qui connaît leurs propriétaires."""
        profile_id = self._extra(key)
        with open_connection(self._db) as conn:
            conn.execute(
                update(users)
                .where(
                    users.c.id == self._user_id, users.c.active_profile_id == profile_id
                )
                .values(active_profile_id=None)
            )
            removed = conn.execute(
                delete(profiles).where(
                    profiles.c.id == profile_id, profiles.c.user_id == self._user_id
                )
            ).rowcount
            conn.commit()
        if not removed:
            raise ProfileNotFound(key)

    def set_active(self, key: str) -> None:
        profile_id = parse_key(key)
        with open_connection(self._db) as conn:
            if profile_id is not None and not self._exists(conn, profile_id):
                raise ProfileNotFound(key)
            conn.execute(
                update(users)
                .where(users.c.id == self._user_id)
                .values(active_profile_id=profile_id)
            )
            conn.commit()

    def _extra(self, key: str) -> uuid.UUID:
        profile_id = parse_key(key)
        if profile_id is None:
            raise ProfileNotFound(
                "le profil principal ne peut être ni renommé ni supprimé"
            )
        return profile_id

    def _exists(self, conn, profile_id: uuid.UUID) -> bool:
        return (
            conn.execute(
                select(profiles.c.id).where(
                    profiles.c.id == profile_id, profiles.c.user_id == self._user_id
                )
            ).first()
            is not None
        )


# --- Base d'expériences d'un profil supplémentaire ----------------------------------


class ExtraProfileBase:
    """Base d'expériences d'un profil SUPPLÉMENTAIRE (même protocole que `ExperienceBaseRepo`).

    Cloisonnée par compte ET par profil : un id de profil d'un autre compte ne lit rien.
    """

    def __init__(
        self, db: str | Path | Engine, user_id: uuid.UUID, profile_id: uuid.UUID
    ) -> None:
        self._db = db
        self._user_id = user_id
        self._profile_id = profile_id

    def _where(self):
        return and_(
            profiles.c.id == self._profile_id, profiles.c.user_id == self._user_id
        )

    def load(self) -> ExperienceBase:
        # Import local : `store` importe ce module pour sa fabrique.
        from jobtool.core.experience_base.store import ExperienceBaseMissing

        with open_connection(self._db) as conn:
            row = conn.execute(select(profiles.c.data).where(self._where())).first()
        if row is None:
            raise ExperienceBaseMissing("profil introuvable pour ce compte")
        try:
            return ExperienceBase.model_validate(row.data)
        except ValidationError as exc:
            raise BaseLoadError(
                f"base d'expériences invalide ({exc.error_count()} problème(s))"
            ) from exc

    def save(self, base: ExperienceBase) -> None:
        with open_connection(self._db) as conn:
            changed = conn.execute(
                update(profiles)
                .where(self._where())
                .values(
                    data=base.model_dump(mode="json", exclude_none=True),
                    updated_at=_now(),
                )
            ).rowcount
            conn.commit()
        if not changed:
            raise ProfileNotFound(str(self._profile_id))

    # --- photo (dans la même ligne) ---------------------------------------------

    def load_photo(self) -> tuple[str, bytes] | None:
        with open_connection(self._db) as conn:
            row = conn.execute(
                select(profiles.c.photo_sha256, profiles.c.photo_jpeg).where(
                    self._where()
                )
            ).first()
        if row is None or row.photo_sha256 is None or row.photo_jpeg is None:
            return None
        return row.photo_sha256, bytes(row.photo_jpeg)

    def save_photo(self, photo: tuple[str, bytes] | None) -> bool:
        with open_connection(self._db) as conn:
            changed = conn.execute(
                update(profiles)
                .where(self._where())
                .values(
                    photo_sha256=photo[0] if photo else None,
                    photo_jpeg=photo[1] if photo else None,
                    updated_at=_now(),
                )
            ).rowcount
            conn.commit()
        return bool(changed)


__all__ = [
    "MAX_EXTRA_PROFILES",
    "PRINCIPAL_KEY",
    "PRINCIPAL_NAME",
    "ActiveProfile",
    "ExtraProfileBase",
    "ProfileInfo",
    "ProfileLimitReached",
    "ProfileNotFound",
    "ProfileRegistry",
    "clean_name",
    "parse_key",
    "resolve_active_profile",
]
