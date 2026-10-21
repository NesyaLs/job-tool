"""Retire les tables HÉRITÉES d'une base SQLite locale, après les avoir sauvegardées.

Tables concernées (plus lues par aucun code) :
  - `jobs`, `job_sources` : l'ancien modèle mono-compte, remplacé par `offers` +
    `user_offers` lors du passage multi-comptes ;
  - `offer_sources` : l'ancienne provenance, reprise en `offer_occurrences` (la reprise
    se fait à l'ouverture de la base ; le script vérifie qu'elle est complète).
Et la colonne `users.last_cleanup_at`, qui datait le nettoyage des offres masquées d'un
compte : la purge porte désormais sur le catalogue commun, en fin de collecte.

Sur Postgres, c'est la migration Alembic qui s'en charge (e3a9c4f7b2d8) : ce script ne
vise que le fichier local, qu'Alembic ne gère pas.

SÛRETÉ, dans cet ordre :
  1. sauvegarde de chaque table en JSON (colonnes + lignes) dans
     data/aggregator/legacy_backup_<horodatage>.json — gitignoré ;
  2. RELECTURE de la sauvegarde : même nombre de lignes que la table, sinon arrêt ;
  3. pour `offer_sources` : chaque (source, native_id) doit exister en occurrence,
     sinon arrêt (on ne supprime pas une provenance non reprise) ;
  4. seulement alors, DROP TABLE.
Idempotent : une table absente est ignorée. `--dry-run` : sauvegarde et vérifie, sans
rien supprimer.

Usage : .venv/bin/python scripts/drop_legacy_tables.py [--db data/aggregator/jobs.db]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

LEGACY_TABLES = ("jobs", "job_sources", "offer_sources")
#: Colonnes héritées : (table, colonne). `users.last_cleanup_at` datait le nettoyage des
#: offres masquées d'un compte, déclenché à la connexion ; la purge porte désormais sur le
#: catalogue commun, en fin de collecte. Plus personne ne lit cette colonne.
LEGACY_COLUMNS = (("users", "last_cleanup_at"),)


def _tables(db: sqlite3.Connection) -> set[str]:
    rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def _dump(db: sqlite3.Connection, table: str) -> dict:
    cursor = db.execute(f"SELECT * FROM {table}")  # noqa: S608 — nom en liste blanche
    columns = [c[0] for c in cursor.description]
    return {"columns": columns, "rows": [list(row) for row in cursor.fetchall()]}


def _unreplicated_sources(db: sqlite3.Connection) -> int:
    """Provenances de `offer_sources` absentes des occurrences."""
    if "offer_occurrences" not in _tables(db):
        return -1
    return db.execute(
        """
        SELECT COUNT(*) FROM offer_sources s
        WHERE NOT EXISTS (
            SELECT 1 FROM offer_occurrences o
            WHERE o.source = s.source AND o.native_id = s.native_id)
        """
    ).fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=None, help="fichier SQLite (défaut : settings)")
    parser.add_argument(
        "--dry-run", action="store_true", help="sauvegarde sans supprimer"
    )
    args = parser.parse_args()

    if args.db:
        path = Path(args.db)
    else:
        from jobtool.settings import AggregatorSettings

        path = Path(AggregatorSettings().db_path)
    if not path.exists():
        print(f"base introuvable : {path}", file=sys.stderr)
        return 2

    db = sqlite3.connect(path)
    tables = _tables(db)
    present = [t for t in LEGACY_TABLES if t in tables]
    columns = [
        (t, c) for t, c in LEGACY_COLUMNS if t in tables and c in _columns(db, t)
    ]
    if not present and not columns:
        print("aucune table ni colonne héritée : rien à faire.")
        return 0

    if "offer_sources" in present:
        missing = _unreplicated_sources(db)
        if missing != 0:
            print(
                "offer_sources n'est pas entièrement reprise en occurrences "
                f"({'table absente' if missing < 0 else f'{missing} manquante(s)'}) : "
                "ouvre d'abord la base avec l'application (reprise automatique).",
                file=sys.stderr,
            )
            return 1

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.parent / f"legacy_backup_{stamp}.json"
    payload = {
        "_origine": str(path),
        "_date": stamp,
        "tables": {t: _dump(db, t) for t in present},
        # Colonnes : la clé de la ligne ET la valeur, pour pouvoir les remettre.
        "colonnes": {
            f"{t}.{c}": db.execute(
                f"SELECT rowid, {c} FROM {t} WHERE {c} IS NOT NULL"  # noqa: S608
            ).fetchall()
            for t, c in columns
        },
    }
    backup.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # Relecture : la sauvegarde doit contenir TOUTES les lignes.
    reread = json.loads(backup.read_text(encoding="utf-8"))
    for table in present:
        expected = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        if len(reread["tables"][table]["rows"]) != expected:
            print(f"sauvegarde incomplète pour {table} : arrêt", file=sys.stderr)
            return 1
        print(f"  {table:<14} {expected:>6} ligne(s) sauvegardée(s)")
    print(f"sauvegarde : {backup}")

    for table, column in columns:
        kept = len(reread["colonnes"][f"{table}.{column}"])
        print(f"  {table}.{column:<10} {kept:>6} valeur(s) non nulle(s) sauvegardée(s)")

    if args.dry_run:
        print("--dry-run : rien n'a été supprimé.")
        return 0
    with db:
        for table in present:
            db.execute(f"DROP TABLE {table}")  # noqa: S608
        for table, column in columns:
            db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")  # noqa: S608
    db.execute("VACUUM")
    db.close()
    retire = [*present, *(f"{t}.{c}" for t, c in columns)]
    print(f"supprimé(s) : {', '.join(retire)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
