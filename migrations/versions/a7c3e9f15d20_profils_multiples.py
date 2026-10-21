"""profils multiples (administrateurs)

Revision ID: a7c3e9f15d20
Revises: f2b8c6d41e57
Create Date: 2026-09-19 18:00:00.000000

Un administrateur peut créer des profils SUPPLÉMENTAIRES : chacun est une base
d'expériences complète, avec sa photo, rangée dans `profiles`. Le profil principal ne bouge
pas (`experience_bases`), et aucune clé primaire existante n'est modifiée.

- `users.active_profile_id` : le profil actif du compte (NULL = principal) ;
- `documents.profile_key` : le profil qui a généré le document ("" = principal). Les
  documents existants sont donc rattachés au profil principal, ce qu'ils sont.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7c3e9f15d20"
down_revision: str | Sequence[str] | None = "f2b8c6d41e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "data",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("photo_sha256", sa.Text(), nullable=True),
        sa.Column("photo_jpeg", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_profiles_user", "profiles", ["user_id", "created_at"])
    op.add_column("users", sa.Column("active_profile_id", sa.Uuid(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("profile_key", sa.Text(), server_default="", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "profile_key")
    op.drop_column("users", "active_profile_id")
    op.drop_index("idx_profiles_user", table_name="profiles")
    op.drop_table("profiles")
