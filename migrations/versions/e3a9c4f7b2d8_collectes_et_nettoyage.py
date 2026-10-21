"""collectes en arrière-plan ; nettoyage des tables et colonnes héritées

Revision ID: e3a9c4f7b2d8
Revises: c7e2f9a4d1b6
Create Date: 2026-09-19 21:00:00.000000

- `collection_runs` : verrou, avancement par source et historique des collectes (la
  collecte tourne en arrière-plan ; l'interface en suit la progression).
- `users.last_cleanup_at` : supprimée. La purge porte désormais sur le catalogue commun,
  en fin de collecte, et non plus sur les offres masquées de chaque compte à la connexion.
- `jobs`, `job_sources` : tables de l'ancien modèle mono-compte, plus lues par aucun code.
  Elles n'existent normalement pas sur Postgres (le schéma y est né multi-comptes) :
  supprimées SI elles existent. Côté SQLite local, c'est scripts/drop_legacy_tables.py
  qui les sauvegarde puis les retire.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e3a9c4f7b2d8"
down_revision: str | Sequence[str] | None = "c7e2f9a4d1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_ = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "collection_runs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("sources", JSON_, nullable=False),
        sa.Column("summary", JSON_, nullable=True),
        sa.Column("errors", JSON_, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_collection_runs_started", "collection_runs", ["started_at"])
    op.drop_column("users", "last_cleanup_at")
    op.execute("DROP TABLE IF EXISTS job_sources")
    op.execute("DROP TABLE IF EXISTS jobs")


def downgrade() -> None:
    """Downgrade schema (les tables héritées ne sont PAS recréées : rien ne les lit)."""
    op.add_column("users", sa.Column("last_cleanup_at", sa.Text(), nullable=True))
    op.drop_index("idx_collection_runs_started", table_name="collection_runs")
    op.drop_table("collection_runs")
