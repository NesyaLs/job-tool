"""Accorde (ou retire) l'accès à l'administration pour un compte existant.

    .venv/bin/python scripts/set_admin.py <e-mail>          # accorde
    .venv/bin/python scripts/set_admin.py <e-mail> --retirer # retire
    .venv/bin/python scripts/set_admin.py --lister           # état des comptes

Pourquoi un script et pas une route : accorder l'administration est le seul geste qui ne
PEUT pas venir de l'application elle-même. Une route « me promouvoir » serait ouverte à
tous ; une route « promouvoir quelqu'un » suppose un premier administrateur, qui n'existe
pas encore. Ce privilège s'accorde donc depuis la machine qui détient l'accès à la base —
c'est-à-dire vous.

Le compte doit DÉJÀ exister : il est créé à la première connexion Supabase (voir
core/db/identity.resolve_account). On ne fabrique pas ici un compte sans identité, qui
resterait inaccessible faute d'un moyen de s'y connecter.

MONO-POSTE (DATABASE_URL absente) : la base est le fichier SQLite local et le seul compte
est « local@jobtool », créé à la première ouverture. Le script s'y applique de la même
façon — sans quoi les écrans réservés à l'administration (dont le journal des échanges
avec le modèle) resteraient inaccessibles sur le poste de leur propriétaire.

`is_admin` est relu en base à CHAQUE requête (voir api/routers/admin.require_admin), et
n'est pas inscrit dans le jeton : le changement prend effet immédiatement, sans
reconnexion — et un retrait est effectif tout de suite, ce qui est le comportement voulu
pour un privilège.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select, update

from jobtool.api.deps import get_offers_db
from jobtool.core.db import engine_for_sqlite_file, users


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email", nargs="?", help="e-mail du compte visé")
    parser.add_argument(
        "--retirer",
        action="store_true",
        help="retire l'administration au lieu de l'accorder",
    )
    parser.add_argument(
        "--lister", action="store_true", help="affiche les comptes et sort"
    )
    args = parser.parse_args()

    db = get_offers_db()
    # Postgres : un moteur. Mono-poste : un CHEMIN de fichier SQLite, dont on ouvre le
    # moteur ici. Le fichier doit exister — il est créé au premier lancement de l'outil.
    if hasattr(db, "connect"):
        engine = db
    else:
        path = Path(str(db))
        if not path.is_file():
            print(
                f"Base locale introuvable : {path}. Lancez d'abord jobtool (api, "
                "aggregate…) pour la créer.",
                file=sys.stderr,
            )
            return 1
        engine = engine_for_sqlite_file(path)

    with engine.connect() as conn:
        if args.lister or not args.email:
            print(f"Base : {db if not hasattr(db, 'connect') else db.url}")
            rows = conn.execute(
                select(users.c.email, users.c.is_admin, users.c.auth_id).order_by(
                    users.c.is_admin.desc(), users.c.created_at
                )
            ).all()
            for row in rows:
                marque = "admin" if row.is_admin else "     "
                lien = "connecté" if row.auth_id else "jamais connecté"
                print(f"  {marque}  {row.email:45} {lien}")
            return 0 if args.lister else 1

        cible = conn.execute(
            select(users.c.id, users.c.is_admin).where(users.c.email == args.email)
        ).first()
        if cible is None:
            print(
                f"Aucun compte pour {args.email}. Les comptes naissent à la première "
                "connexion : connectez-vous d'abord avec cette adresse.",
                file=sys.stderr,
            )
            return 1

        voulu = not args.retirer
        if bool(cible.is_admin) == voulu:
            print(
                f"{args.email} est déjà {'administrateur' if voulu else 'sans administration'}."
            )
            return 0

        conn.execute(update(users).where(users.c.id == cible.id).values(is_admin=voulu))
        conn.commit()
        verbe = "accordée à" if voulu else "retirée à"
        print(f"Administration {verbe} {args.email}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
