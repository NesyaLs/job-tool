"""occurrences par source, dédoublonnage entre sources, offres privées

Revision ID: c7e2f9a4d1b6
Revises: b1d4e7a2c9f3
Create Date: 2026-09-19 20:00:00.000000

- `offer_occurrences` remplace `offer_sources` : chaque (source, identifiant natif) garde
  son propre contenu, ses liens et ses champs normalisés ; l'offre canonique (`offers`)
  en est recalculée.
- `occurrence_urls` : les identités d'URL de chaque occurrence (dédoublonnage par lien).
- `offer_aliases` : l'ancien identifiant d'une offre fusionnée, vers celle qui l'a
  absorbée.
- `offers` : clés de comparaison, dernière vue, expiration, propriétaire (offres privées).

REPRISE : la provenance de `offer_sources` est recopiée en occurrences (contenu = celui de
l'offre, seul conservé par l'ancien modèle ; champs normalisés recalculés par la prochaine
collecte). Les offres manuelles deviennent privées à leur compte. `offer_sources` est
ensuite supprimée. Le SQL est celui de `core/db/engine.LEGACY_OCCURRENCES_SQL`, figé ici.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c7e2f9a4d1b6"
down_revision: str | Sequence[str] | None = "b1d4e7a2c9f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_ = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

_COPY = (
    """
INSERT INTO offer_occurrences (
    source, native_id, offer_id, url, url_key, title, company, location, remote,
    contract_type, salary, description, posted_date, raw, place_precision,
    contract_kind, company_key, title_key, norm_version, first_seen_at, last_seen_at,
    match_level
)
SELECT s.source, s.native_id, s.dedup_hash, COALESCE(s.url, o.url, ''), '', o.title,
       o.company, o.location, o.remote, o.contract_type, o.salary, o.description,
       o.posted_date, o.raw, 'inconnu', 'inconnu', '', '', 0, s.fetched_date,
       s.fetched_date,
       CASE WHEN s.source = o.source AND s.native_id = o.native_id
            THEN 'nouvelle' ELSE 'reprise' END
FROM offer_sources s JOIN offers o ON o.dedup_hash = s.dedup_hash
WHERE s.dedup_hash = (
        SELECT MIN(s2.dedup_hash) FROM offer_sources s2
        WHERE s2.source = s.source AND s2.native_id = s.native_id)
  AND NOT EXISTS (
        SELECT 1 FROM offer_occurrences x
        WHERE x.source = s.source AND x.native_id = s.native_id)
""",
    """
INSERT INTO offer_occurrences (
    source, native_id, offer_id, url, url_key, title, company, location, remote,
    contract_type, salary, description, posted_date, raw, place_precision,
    contract_kind, company_key, title_key, norm_version, first_seen_at, last_seen_at,
    match_level
)
SELECT o.source, o.native_id, o.dedup_hash, o.url, '', o.title, o.company, o.location,
       o.remote, o.contract_type, o.salary, o.description, o.posted_date, o.raw,
       'inconnu', 'inconnu', '', '', 0, o.fetched_date, o.fetched_date, 'nouvelle'
FROM offers o
WHERE NOT EXISTS (SELECT 1 FROM offer_occurrences x WHERE x.offer_id = o.dedup_hash)
  AND NOT EXISTS (
        SELECT 1 FROM offer_occurrences x
        WHERE x.source = o.source AND x.native_id = o.native_id)
""",
    """
UPDATE offers SET owner_user_id = (
    SELECT u.user_id FROM user_offers u WHERE u.dedup_hash = offers.dedup_hash
    ORDER BY u.added_at LIMIT 1)
WHERE source = 'manual' AND owner_user_id IS NULL
""",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "offers", sa.Column("company_key", sa.Text(), server_default="", nullable=False)
    )
    op.add_column(
        "offers", sa.Column("title_key", sa.Text(), server_default="", nullable=False)
    )
    op.add_column("offers", sa.Column("last_seen_at", sa.Text(), nullable=True))
    op.add_column("offers", sa.Column("expired_at", sa.Text(), nullable=True))
    op.add_column("offers", sa.Column("owner_user_id", sa.Uuid(), nullable=True))

    op.create_table(
        "offer_occurrences",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("native_id", sa.Text(), nullable=False),
        sa.Column("offer_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), server_default="", nullable=False),
        sa.Column("url_key", sa.Text(), server_default="", nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("company", sa.Text(), server_default="", nullable=False),
        sa.Column("location", sa.Text(), server_default="", nullable=False),
        sa.Column("remote", sa.Text(), server_default="inconnu", nullable=False),
        sa.Column("contract_type", sa.Text(), server_default="", nullable=False),
        sa.Column("salary", JSON_, nullable=True),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("posted_date", sa.Text(), nullable=True),
        sa.Column("raw", JSON_, nullable=False),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column(
            "place_precision", sa.Text(), server_default="inconnu", nullable=False
        ),
        sa.Column("contract_kind", sa.Text(), server_default="inconnu", nullable=False),
        sa.Column("work_time", sa.Text(), nullable=True),
        sa.Column("company_key", sa.Text(), server_default="", nullable=False),
        sa.Column("title_key", sa.Text(), server_default="", nullable=False),
        sa.Column("norm_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("match_level", sa.Text(), server_default="nouvelle", nullable=False),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.dedup_hash"]),
        sa.PrimaryKeyConstraint("source", "native_id"),
    )
    op.create_index("idx_occurrences_offer", "offer_occurrences", ["offer_id"])
    op.create_index("idx_occurrences_url_key", "offer_occurrences", ["url_key"])
    op.create_table(
        "occurrence_urls",
        sa.Column("url_key", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("native_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("url_key", "source", "native_id"),
    )
    op.create_table(
        "offer_aliases",
        sa.Column("old_id", sa.Text(), nullable=False),
        sa.Column("offer_id", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("merged_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("old_id"),
    )
    op.create_index("idx_aliases_offer", "offer_aliases", ["offer_id"])

    for statement in _COPY:
        op.execute(statement)
    op.drop_table("offer_sources")


def downgrade() -> None:
    """Downgrade schema : la provenance redevient une table de traces (sans contenu)."""
    op.create_table(
        "offer_sources",
        sa.Column("dedup_hash", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("native_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("fetched_date", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["dedup_hash"], ["offers.dedup_hash"]),
        sa.PrimaryKeyConstraint("dedup_hash", "source", "native_id"),
    )
    op.execute(
        """
INSERT INTO offer_sources (dedup_hash, source, native_id, url, fetched_date)
SELECT offer_id, source, native_id, url, first_seen_at FROM offer_occurrences
"""
    )
    op.drop_index("idx_aliases_offer", table_name="offer_aliases")
    op.drop_table("offer_aliases")
    op.drop_table("occurrence_urls")
    op.drop_index("idx_occurrences_url_key", table_name="offer_occurrences")
    op.drop_index("idx_occurrences_offer", table_name="offer_occurrences")
    op.drop_table("offer_occurrences")
    for column in (
        "owner_user_id",
        "expired_at",
        "last_seen_at",
        "title_key",
        "company_key",
    ):
        op.drop_column("offers", column)
