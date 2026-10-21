"""Écritures GROUPÉES, neutres vis-à-vis du dialecte.

Aucune requête métier ici (même règle que le reste de core/db) : uniquement de quoi écrire
beaucoup de lignes en peu d'allers-retours.

Pourquoi ce module existe : sur une base distante, le coût dominant n'est pas le calcul
mais la LATENCE. Un aller-retour vers un Postgres hébergé coûte de l'ordre de 100 ms ;
un millier de requêtes unitaires, aussi simples soient-elles, dépasse alors la minute là
où un fichier SQLite local répondait en quelques millisecondes. Le nombre de requêtes
devient donc la grandeur à minimiser — pas leur complexité.

`ON CONFLICT DO NOTHING` remplace ici le couple `SAVEPOINT` + rattrapage d'`IntegrityError`
du chemin unitaire : dans un lot, une exception ferait échouer TOUTES les lignes, y compris
celles qui n'avaient aucun conflit.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, TypeVar

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

T = TypeVar("T")

#: Taille des tranches pour les clauses `IN` et les insertions multi-lignes.
#: SQLite refuse au-delà de 999 paramètres liés par défaut, et une ligne compte pour
#: autant de paramètres qu'elle a de colonnes : 200 lignes restent sous la limite pour
#: nos tables les plus larges, tout en tenant une run entière en une poignée de requêtes.
CHUNK = 200


def chunked(items: Sequence[T], size: int = CHUNK) -> Iterable[Sequence[T]]:
    """Découpe en tranches — une clause `IN` ne peut pas grandir indéfiniment."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def insert_ignore(
    conn: Connection, table: Table, rows: Sequence[dict[str, Any]]
) -> None:
    """Insère des lignes en ignorant celles qui existent déjà. Sans effet si `rows` est vide.

    Le conflit est un cas NORMAL, pas une erreur : le catalogue d'offres est mutualisé, donc
    deux comptes qui collectent la même annonce au même moment s'insèrent mutuellement dans
    les pattes. Le résultat voulu — la ligne existe — est atteint dans les deux cas.
    """
    if not rows:
        return
    maker = pg_insert if conn.dialect.name == "postgresql" else sqlite_insert
    for part in chunked(rows):
        conn.execute(maker(table).values(list(part)).on_conflict_do_nothing())


__all__ = ["CHUNK", "chunked", "insert_ignore"]
