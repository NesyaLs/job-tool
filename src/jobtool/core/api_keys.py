"""Clés d'API PROPRES à un compte : saisies dans l'interface, rangées CHIFFRÉES en base.

Chaque compte peut fournir ses propres clés (Mistral, France Travail, Adzuna). Une clé du
compte l'emporte sur celle du serveur (`.env`) ; sans clé de compte, le serveur fournit la
sienne s'il en a une — c'est le comportement historique, inchangé.

CHIFFREMENT : Fernet (AES-128-CBC + HMAC-SHA256, bibliothèque `cryptography`). La base ne
contient jamais une clé en clair : une fuite de la base seule ne livre rien. La clé de
chiffrement vient de `JOBTOOL_SECRET_KEY` ; à défaut, et en mono-poste seulement, elle est
générée une fois dans `data/.secret_key` (droits 600, ignoré par git). En multi-comptes,
`JOBTOOL_SECRET_KEY` est OBLIGATOIRE : une clé générée sur le disque d'un serveur serait
perdue au prochain déploiement, et toutes les clés des comptes avec elle.

Une valeur indéchiffrable (clé de chiffrement changée) est traitée comme ABSENTE : le compte
la ressaisit, rien ne casse. Aucune valeur n'est jamais renvoyée au navigateur ni journalisée :
seule leur PRÉSENCE sort de ce module.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from jobtool.core.db import api_keys, open_connection
from jobtool.settings import AggregatorSettings

#: Clés qu'un compte peut fournir : MÊMES noms que les champs du settings, pour que la
#: fusion compte > serveur soit une simple surcharge de champs (cf. `with_account_keys`).
API_KEY_NAMES = (
    "mistral_api_key",
    "france_travail_client_id",
    "france_travail_client_secret",
    "adzuna_app_id",
    "adzuna_app_key",
)

#: Longueur maximale acceptée : les vraies clés font moins de 100 caractères.
MAX_KEY_LENGTH = 512


class SecretKeyMissing(RuntimeError):
    """Multi-comptes sans `JOBTOOL_SECRET_KEY` : impossible de ranger une clé chiffrée."""


def load_cipher(settings: AggregatorSettings) -> Fernet:
    """Chiffreur des clés de compte. Lève `SecretKeyMissing` si aucun n'est utilisable.

    `JOBTOOL_SECRET_KEY` d'abord ; sinon, en mono-poste, le fichier local (créé au besoin).
    """
    if settings.secret_key:
        return Fernet(settings.secret_key.encode())
    if settings.database_url:
        raise SecretKeyMissing(
            "JOBTOOL_SECRET_KEY absente : en multi-comptes, elle est obligatoire pour "
            "enregistrer des clés d'API (voir .env.example)."
        )
    path = Path(settings.secret_key_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Création EXCLUSIVE avec les droits finaux : le fichier n'existe à aucun moment
        # lisible par d'autres, et deux processus ne peuvent pas l'écrire tous les deux.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


class ApiKeyStore:
    """Clés d'API d'UN compte. `user_id` explicite, sans défaut (comme les autres dépôts).

    Le chiffreur n'est obtenu qu'au besoin (une clé à lire ou à écrire) : un compte sans
    clé ne crée aucun fichier et n'exige aucune `JOBTOOL_SECRET_KEY`.
    """

    def __init__(
        self, db: str | Path | Engine, user_id: uuid.UUID, settings: AggregatorSettings
    ) -> None:
        self._db = db
        self._user_id = user_id
        self._settings = settings
        self._cipher: Fernet | None = None

    def _fernet(self) -> Fernet:
        if self._cipher is None:
            self._cipher = load_cipher(self._settings)
        return self._cipher

    def names(self) -> set[str]:
        """Noms des clés que le compte a enregistrées (sans rien déchiffrer)."""
        with open_connection(self._db) as conn:
            rows = conn.execute(
                select(api_keys.c.name).where(api_keys.c.user_id == self._user_id)
            ).all()
        return {row.name for row in rows}

    def load(self) -> dict[str, str]:
        """Clés du compte, déchiffrées. Une valeur indéchiffrable est ignorée."""
        with open_connection(self._db) as conn:
            rows = conn.execute(
                select(api_keys.c.name, api_keys.c.ciphertext).where(
                    api_keys.c.user_id == self._user_id
                )
            ).all()
        if not rows:
            return {}
        try:
            cipher = self._fernet()
        except SecretKeyMissing:
            return {}  # clé de chiffrement retirée depuis : les valeurs sont illisibles
        found: dict[str, str] = {}
        for row in rows:
            try:
                found[row.name] = cipher.decrypt(row.ciphertext.encode()).decode()
            except InvalidToken:
                continue
        return found

    def save(self, name: str, value: str) -> None:
        """Crée ou remplace une clé du compte (chiffrée), en une transaction."""
        if name not in API_KEY_NAMES:
            raise ValueError(f"clé inconnue : {name}")
        ciphertext = self._fernet().encrypt(value.encode()).decode()
        now = datetime.now(UTC).isoformat()
        where = (api_keys.c.user_id == self._user_id) & (api_keys.c.name == name)
        with open_connection(self._db) as conn:
            changed = conn.execute(
                update(api_keys)
                .where(where)
                .values(ciphertext=ciphertext, updated_at=now)
            ).rowcount
            if not changed:
                conn.execute(
                    insert(api_keys).values(
                        user_id=self._user_id,
                        name=name,
                        ciphertext=ciphertext,
                        updated_at=now,
                    )
                )
            conn.commit()

    def delete(self, name: str) -> bool:
        """Retire une clé du compte ; True si elle existait."""
        with open_connection(self._db) as conn:
            removed = conn.execute(
                delete(api_keys).where(
                    (api_keys.c.user_id == self._user_id) & (api_keys.c.name == name)
                )
            ).rowcount
            conn.commit()
        return bool(removed)


def with_account_keys(
    settings: AggregatorSettings, account: dict[str, str]
) -> AggregatorSettings:
    """Settings où chaque clé fournie par le compte remplace celle du serveur."""
    overrides = {
        name: value for name, value in account.items() if name in API_KEY_NAMES
    }
    return settings.model_copy(update=overrides) if overrides else settings
