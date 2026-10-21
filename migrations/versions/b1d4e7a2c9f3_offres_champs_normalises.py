"""offres : champs normalisés (lieu, contrat, temps de travail)

Revision ID: b1d4e7a2c9f3
Revises: a7c3e9f15d20
Create Date: 2026-09-19 19:00:00.000000

Pays, région, département, ville, précision du lieu, famille de contrat et temps de
travail, calculés par le code (aggregator/geo.py, aggregator/contracts.py). La recherche
filtre sur ces colonnes.

Les lignes existantes reçoivent `norm_version = 0` : la prochaine collecte les recalcule
(storage.renormalize_catalogue). Le calcul reste dans le code, pas dans la migration — il
dépend d'un référentiel des communes que le SQL n'a pas.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1d4e7a2c9f3"
down_revision: str | Sequence[str] | None = "a7c3e9f15d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("offers", sa.Column("country", sa.Text(), nullable=True))
    op.add_column("offers", sa.Column("region", sa.Text(), nullable=True))
    op.add_column("offers", sa.Column("department", sa.Text(), nullable=True))
    op.add_column("offers", sa.Column("city", sa.Text(), nullable=True))
    op.add_column(
        "offers",
        sa.Column(
            "place_precision", sa.Text(), server_default="inconnu", nullable=False
        ),
    )
    op.add_column(
        "offers",
        sa.Column("contract_kind", sa.Text(), server_default="inconnu", nullable=False),
    )
    op.add_column("offers", sa.Column("work_time", sa.Text(), nullable=True))
    op.add_column(
        "offers",
        sa.Column("norm_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("idx_offers_department", "offers", ["department"], unique=False)
    op.create_index(
        "idx_offers_contract_kind", "offers", ["contract_kind"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_offers_contract_kind", table_name="offers")
    op.drop_index("idx_offers_department", table_name="offers")
    for column in (
        "norm_version",
        "work_time",
        "contract_kind",
        "place_precision",
        "city",
        "department",
        "region",
        "country",
    ):
        op.drop_column("offers", column)
