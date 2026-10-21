"""clés d'API par compte (api_keys)

Revision ID: f9c1d3e8a2b4
Revises: e3a9c4f7b2d8
Create Date: 2026-10-20 12:00:00.000000

Chaque compte peut saisir ses propres clés (Mistral, France Travail, Adzuna) dans
l'interface ; elles l'emportent sur celles du serveur. La valeur est CHIFFRÉE (Fernet, clé
`JOBTOOL_SECRET_KEY`) : la table seule ne livre aucune clé. Voir core/api_keys.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9c1d3e8a2b4"
down_revision: str | Sequence[str] | None = "e3a9c4f7b2d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "api_keys",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "name"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("api_keys")
