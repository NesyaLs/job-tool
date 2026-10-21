"""Où vit la base d'expériences d'un compte : un fichier YAML, ou une ligne en base.

Pourquoi ce module existe. La base d'expériences était lue et écrite DIRECTEMENT depuis un
chemin de fichier (`settings.base_path`, c'est-à-dire `data/moi.yaml`) par trois appelants :
le service de profil, la génération CV/lettre de l'API, et l'édition de documents. Sur une
base partagée, ce fichier est unique pour le serveur entier : tout compte authentifié lisait
et ÉCRASAIT le profil de tous les autres. La table `experience_bases` existait déjà — créée
par migration, remplie par le script de reprise — et n'était lue par personne.

Deux implémentations derrière un même protocole, et le MÊME interrupteur unique que pour le
reste de l'application (`DATABASE_URL`) :
  - `YamlExperienceBase` : un fichier. Mode MONO-POSTE et CLI — `jobtool cv` doit continuer
    de fonctionner sans base de données, et le YAML reste modifiable à la main.
  - `DbExperienceBase`   : une ligne de `experience_bases`, cloisonnée par `user_id`.

Ce module vit dans `core/` et non dans un domaine : la base d'expériences sert à la fois aux
documents (génération) et au service de profil, et un domaine n'importe jamais un autre
domaine.

`user_id` est ici un paramètre EXPLICITE de construction, comme dans les modules de stockage
et pour la même raison : un oubli doit être visible à la construction, pas se traduire en
lecture du profil d'autrui.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import ValidationError
from sqlalchemy import Engine, insert, select, update

from jobtool.core.db import experience_bases
from jobtool.core.experience_base.loader import BaseLoadError, load_base
from jobtool.core.experience_base.models import ExperienceBase


class ExperienceBaseMissing(BaseLoadError, FileNotFoundError):
    """Aucune base d'expériences pour ce compte (fichier absent, ou ligne inexistante).

    Hérite de `FileNotFoundError` À DESSEIN : c'est ce que levait la lecture du fichier, et
    ce que les appelants — dont le routeur `/profile`, qui le traduit en 404 — savent déjà
    traiter. Un compte fraîchement inscrit, qui n'a pas encore de ligne, emprunte donc le
    chemin déjà éprouvé du fichier absent.
    """


class ExperienceBaseRepo(Protocol):
    """Ce qu'un appelant a le droit de faire de la base d'expériences d'un compte."""

    def load(self) -> ExperienceBase:
        """Renvoie la base. Lève `ExperienceBaseMissing` s'il n'y en a pas."""
        ...

    def save(self, base: ExperienceBase) -> None:
        """Remplace la base (création comprise)."""
        ...


def _to_data(base: ExperienceBase) -> dict:
    """Forme sérialisable de la base — la même dans le YAML et dans la colonne JSON."""
    return base.model_dump(mode="json", exclude_none=True)


class YamlExperienceBase:
    """Base d'expériences dans un fichier YAML (mono-poste, CLI, tests)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> ExperienceBase:
        try:
            return load_base(self._path)
        except BaseLoadError as exc:
            if isinstance(exc, FileNotFoundError) or not self._path.exists():
                raise ExperienceBaseMissing(str(exc)) from exc
            raise

    def save(self, base: ExperienceBase) -> None:
        """Écrit le YAML de façon ATOMIQUE (tmp + os.replace) pour ne pas corrompre la base.

        Une écriture interrompue en plein milieu laisserait un fichier tronqué là où vivent
        toutes les expériences de l'utilisateur ; `os.replace` est atomique sur un même
        système de fichiers.
        """
        text = yaml.safe_dump(_to_data(base), allow_unicode=True, sort_keys=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self._path)


class DbExperienceBase:
    """Base d'expériences d'UN compte, dans la table `experience_bases`.

    Le contenu est stocké en JSON plutôt qu'éclaté en tables : `ExperienceBase` est déjà un
    modèle Pydantic validé, porteur des ids stables sur lesquels repose la traçabilité.
    L'éclater ferait réécrire cette validation sans rien apporter tant qu'on ne requête pas
    À L'INTÉRIEUR d'une base (voir core/db/schema.py).
    """

    def __init__(self, engine: Engine, user_id: uuid.UUID) -> None:
        self._engine = engine
        self._user_id = user_id

    def load(self) -> ExperienceBase:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(experience_bases.c.data).where(
                    experience_bases.c.user_id == self._user_id
                )
            ).first()
        if row is None:
            raise ExperienceBaseMissing(
                "aucune base d'expériences pour ce compte : renseignez votre profil"
            )
        try:
            return ExperienceBase.model_validate(row.data)
        except ValidationError as exc:
            # Même traitement que pour un YAML invalide : un message lisible, jamais une
            # trace brute. La donnée est en base, donc corrigeable — pas irrécupérable.
            raise BaseLoadError(
                f"base d'expériences invalide en base ({exc.error_count()} problème(s))"
            ) from exc

    def save(self, base: ExperienceBase) -> None:
        """Crée ou remplace la ligne du compte, en une transaction."""
        data = _to_data(base)
        now = datetime.now(UTC).isoformat()
        with self._engine.connect() as conn:
            changed = conn.execute(
                update(experience_bases)
                .where(experience_bases.c.user_id == self._user_id)
                .values(data=data, updated_at=now)
            ).rowcount
            if not changed:
                conn.execute(
                    insert(experience_bases).values(
                        user_id=self._user_id, data=data, updated_at=now
                    )
                )
            conn.commit()


def experience_base_repo(
    db: str | Path | Engine,
    user_id: uuid.UUID,
    *,
    yaml_path: str | Path,
    profile_key: str,
) -> ExperienceBaseRepo:
    """Le dépôt correspondant au mode d'exécution ET au profil — l'unique endroit qui tranche.

    Profil supplémentaire -> sa ligne de `profiles` (dans les deux modes). Profil principal :
    `db` est un moteur (base partagée) -> la ligne du compte ; sinon (mode mono-poste) -> le
    fichier YAML. Aucun appelant n'a à savoir lequel il a reçu.

    `profile_key` est OBLIGATOIRE, comme `user_id` : un oubli de câblage doit être une
    TypeError, pas la génération d'un CV avec le mauvais profil.
    """
    from jobtool.core.experience_base.profiles import ExtraProfileBase, parse_key

    profile_id = parse_key(profile_key)
    if profile_id is not None:
        return ExtraProfileBase(db, user_id, profile_id)
    if isinstance(db, Engine):
        return DbExperienceBase(db, user_id)
    return YamlExperienceBase(yaml_path)


__all__ = [
    "DbExperienceBase",
    "ExperienceBaseMissing",
    "ExperienceBaseRepo",
    "YamlExperienceBase",
    "experience_base_repo",
]
