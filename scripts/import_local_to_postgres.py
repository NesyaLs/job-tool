"""Reprise des données locales (SQLite + moi.yaml) vers la base applicative Postgres.

À jouer UNE fois, après `alembic upgrade head` :

    .venv/bin/python scripts/import_local_to_postgres.py [--dry-run]

Ce que fait le script :
  1. crée le compte « propriétaire » (celui du poste), déduit de la base d'expériences ;
  2. recopie `document_drafts` -> `documents`, attribués au propriétaire ;
  3. recopie `data/moi.yaml` -> `experience_bases`.

Les OFFRES ne sont plus reprises d'ici : l'ancienne table locale `jobs` a été retirée
(sauvegardée par scripts/drop_legacy_tables.py). Le catalogue d'une base Postgres se
remplit par une collecte (`jobtool aggregate`).

IDEMPOTENT : rejouable sans créer de doublon (chaque écriture ignore les lignes déjà
présentes). Ne SUPPRIME jamais rien, ni dans SQLite ni dans Postgres — la source locale
reste intacte tant que vous n'avez pas vérifié le résultat.

L'identifiant du compte est déterministe (uuid5 de l'e-mail) : rejouer le script vise le
même compte, sans qu'aucun identifiant ne soit à retenir dans un fichier de configuration.
Le jour où ce compte s'authentifie vraiment via Supabase, il faudra rattacher ses lignes à
l'identifiant émis par Supabase Auth — c'est une mise à jour de `user_id`, pas une reprise.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from jobtool.core.db import (
    documents,
    experience_bases,
    make_engine,
    users,
)
from jobtool.settings import AggregatorSettings

#: Espace de noms pour dériver un id de compte stable à partir de l'e-mail.
_NAMESPACE = uuid.UUID("6f5c2f2e-3a3b-4c8d-9f10-1a2b3c4d5e6f")


def owner_identity(base_path: Path) -> tuple[uuid.UUID, str, str]:
    """(id, e-mail, nom) du compte propriétaire, lus dans la base d'expériences."""
    data = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
    profile = data.get("profile") or {}
    email = (profile.get("email") or "proprietaire@local").strip().lower()
    name = (profile.get("name") or "Propriétaire").strip()
    return uuid.uuid5(_NAMESPACE, email), email, name


def _sqlite(path: Path) -> sqlite3.Connection | None:
    """Ouvre une base SQLite existante en lecture, ou None si le fichier est absent."""
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _loads(raw: str | None, default):
    return json.loads(raw) if raw else default


def insert_ignore(table: Table, **values):
    """INSERT ... ON CONFLICT DO NOTHING — c'est ce qui rend le script rejouable.

    Postgres-only, assumé : ce script ne vise QUE la base applicative. Le code métier,
    lui, reste neutre pour continuer de tourner sur SQLite en test.
    """
    return pg_insert(table).values(**values).on_conflict_do_nothing()


def _inserted(result) -> int:
    """1 si la ligne a été créée, 0 si le conflit l'a écartée.

    `rowcount` vaut -1 (et non 0) quand ON CONFLICT DO NOTHING n'écrit rien : on ne peut
    donc pas l'additionner tel quel sous peine de compter à l'envers.
    """
    return 1 if result.rowcount == 1 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="compte sans rien écrire"
    )
    args = parser.parse_args()

    settings = AggregatorSettings()
    if not settings.database_url:
        print("DATABASE_URL absente de .env — rien à faire.", file=sys.stderr)
        return 2

    base_path = Path(settings.base_path)
    if not base_path.exists():
        print(f"base d'expériences introuvable : {base_path}", file=sys.stderr)
        return 2

    user_id, email, name = owner_identity(base_path)
    now = datetime.now(UTC).isoformat()
    engine = make_engine(settings.database_url)
    counts = {k: 0 for k in ("documents", "base")}

    with engine.begin() as conn:
        # --- 1. compte propriétaire (admin : c'est votre instance) --------------
        exists = conn.execute(select(users.c.id).where(users.c.id == user_id)).first()
        if not exists and not args.dry_run:
            conn.execute(
                insert_ignore(
                    users,
                    id=user_id,
                    email=email,
                    display_name=name,
                    is_admin=True,
                    created_at=now,
                )
            )
        print(f"compte : {name} <{email}>  id={user_id}")

        # --- 2. documents retouchés -------------------------------------------
        drafts_db = _sqlite(Path(settings.drafts_db_path))
        if drafts_db is not None:
            for row in drafts_db.execute("SELECT * FROM document_drafts"):
                if args.dry_run:
                    counts["documents"] += 1
                else:
                    counts["documents"] += _inserted(
                        conn.execute(
                            insert_ignore(
                                documents,
                                doc_id=row["doc_id"],
                                user_id=user_id,
                                offer_id=row["offer_id"],
                                kind=row["kind"],
                                template=row["template"],
                                render_input=_loads(row["render_input"], {}),
                                original_texts=_loads(row["original_texts"], {}),
                                overrides=_loads(row["overrides"], {}),
                                created_at=row["created_at"],
                                updated_at=row["updated_at"],
                            )
                        )
                    )
            drafts_db.close()

        # --- 3. base d'expériences --------------------------------------------
        has_base = conn.execute(
            select(experience_bases.c.user_id).where(
                experience_bases.c.user_id == user_id
            )
        ).first()
        if not has_base:
            counts["base"] = 1
            if not args.dry_run:
                conn.execute(
                    insert_ignore(
                        experience_bases,
                        user_id=user_id,
                        data=yaml.safe_load(base_path.read_text(encoding="utf-8")),
                        updated_at=now,
                    )
                )

    verb = "à reprendre" if args.dry_run else "repris"
    print(
        f"{verb} : {counts['documents']} documents, {counts['base']} base d'expériences"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
