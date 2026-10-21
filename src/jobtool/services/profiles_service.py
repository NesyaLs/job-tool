"""Profils multiples d'un compte (administrateurs) : lister, créer, renommer, supprimer, activer.

Couche SERVICE (printless). Le choix du profil actif, lui, n'est pas appliqué ici : il est
résolu une fois par requête (`core/experience_base/profiles.resolve_active_profile`) et
injecté dans tous les dépôts (base d'expériences, photo, documents, entretien). Ce service ne
fait que GÉRER les profils.

Supprimer un profil emporte ce qui n'a de sens que pour lui : sa photo (dans sa ligne), son
entretien et ses documents générés — y compris leurs dossiers de travail sur disque. Les
offres, favoris et candidatures appartiennent au COMPTE et ne sont pas touchés.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from jobtool.core.db import open_connection
from jobtool.core.experience_base.models import ExperienceBase, Profile
from jobtool.core.experience_base.photo_store import PhotoStore
from jobtool.core.experience_base.profiles import (
    ActiveProfile,
    ProfileInfo,
    ProfileRegistry,
)
from jobtool.core.experience_base.store import (
    ExperienceBaseMissing,
    experience_base_repo,
)
from jobtool.documents.drafts import store as drafts_store
from jobtool.interview.store import InterviewSessionStore


class ProfilesForbidden(PermissionError):
    """Le compte n'a pas accès aux profils multiples (réservés à l'administration)."""


@dataclass(frozen=True)
class ProfilesOverview:
    allowed: bool
    active: str
    profiles: list[ProfileInfo]


class ProfilesService:
    """Profils d'UN compte."""

    def __init__(
        self,
        db: str | Path | Engine,
        user_id: uuid.UUID,
        active: ActiveProfile,
        *,
        yaml_path: str | Path,
        drafts_db: str | Path | Engine,
        work_root: Path,
    ) -> None:
        """`active` : résultat de la résolution de la requête (droits + profil actif).
        `yaml_path` : base du profil principal en mono-poste (pour la copier).
        `drafts_db` : base des documents — un fichier À PART en mono-poste.
        `work_root` : dossiers de travail des documents (effacés avec le profil)."""
        self._db = db
        self._user_id = user_id
        self._active = active
        self._yaml_path = yaml_path
        self._drafts_db = drafts_db
        self._work_root = Path(work_root)
        self._registry = ProfileRegistry(db, user_id)

    def overview(self) -> ProfilesOverview:
        """Toujours disponible : un compte sans droits voit son seul profil principal."""
        if not self._active.allowed:
            return ProfilesOverview(False, self._active.key, self._registry.list()[:1])
        return ProfilesOverview(True, self._active.key, self._registry.list())

    def create(self, name: str, copy_from: str | None = None) -> ProfilesOverview:
        """Nouveau profil, vide ou copie d'un profil existant (photo comprise)."""
        self._require()
        base, photo = self._empty(), None
        if copy_from is not None:
            base, photo = self._snapshot(copy_from)
        self._registry.create(name, base, photo)
        return self.overview()

    def rename(self, key: str, name: str) -> ProfilesOverview:
        self._require()
        self._registry.rename(key, name)
        return self.overview()

    def delete(self, key: str) -> ProfilesOverview:
        """Supprime le profil, son entretien, ses documents. Le principal redevient actif
        si c'était celui-ci."""
        self._require()
        self._registry.delete(key)
        InterviewSessionStore(self._db, self._user_id, profile_key=key).delete()
        conn = open_connection(self._drafts_db)
        try:
            doc_ids = drafts_store.delete_for_profile(conn, self._user_id, key)
        finally:
            conn.close()
        for doc_id in doc_ids:
            shutil.rmtree(self._work_root / doc_id, ignore_errors=True)
        return self._after_change(key)

    def activate(self, key: str) -> ProfilesOverview:
        self._require()
        self._registry.set_active(key)
        return ProfilesOverview(True, key, self._registry.list())

    # --- interne ----------------------------------------------------------------

    def _require(self) -> None:
        if not self._active.allowed:
            raise ProfilesForbidden(
                "les profils multiples sont réservés à l'administration"
            )

    def _after_change(self, removed: str) -> ProfilesOverview:
        active = "" if self._active.key == removed else self._active.key
        return ProfilesOverview(True, active, self._registry.list())

    def _snapshot(self, key: str) -> tuple[ExperienceBase, tuple[str, bytes] | None]:
        """Base et photo d'un profil du compte, pour en faire une copie."""
        repo = experience_base_repo(
            self._db, self._user_id, yaml_path=self._yaml_path, profile_key=key
        )
        try:
            base = repo.load()
        except ExperienceBaseMissing:
            base = self._empty()
        stored = PhotoStore(self._db, self._user_id, profile_key=key).load_if(
            base.profile.photo
        )
        if stored is None:
            # Sans l'image, l'empreinte annoncerait une photo introuvable.
            base = base.model_copy(
                update={"profile": base.profile.model_copy(update={"photo": None})}
            )
            return base, None
        return base, (stored.sha256, stored.jpeg)

    @staticmethod
    def _empty() -> ExperienceBase:
        return ExperienceBase(profile=Profile(name="", email=""), experiences=[])


__all__ = ["ProfilesForbidden", "ProfilesOverview", "ProfilesService"]
