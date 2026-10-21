"""entretien guidé par LLM (interview_sessions)

Revision ID: f2b8c6d41e57
Revises: e5d9a1c73b28
Create Date: 2026-09-19 12:00:00.000000

Une session d'entretien par compte et par profil ("" = profil principal), au plus : messages, propositions, plan, résumé et
provenance, en JSON. L'état est lu et réécrit en entier à chaque tour — une seule écriture
par tour — et jamais requêté de l'intérieur, d'où une colonne JSON plutôt que des tables.

Contenu PERSONNEL (ce que la personne raconte de son parcours) : la clé étrangère vers
`users` le rattache au compte.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f2b8c6d41e57"
down_revision: str | Sequence[str] | None = "e5d9a1c73b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "interview_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("profile_key", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "data",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "profile_key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("interview_sessions")
