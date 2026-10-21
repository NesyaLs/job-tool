"""photo de profil (profile_photos)

Revision ID: e5d9a1c73b28
Revises: c4a71f0b9e32
Create Date: 2026-09-18 16:00:00.000000

Une ligne par compte, au plus. L'image est stockée NORMALISÉE (JPEG carré 600 px, environ
45 Kio) : elle est validée et recompressée à l'envoi, jamais au rendu.

En base plutôt que sur disque : l'état d'un compte vit déjà entièrement dans la base, et
`DATABASE_URL` reste le seul interrupteur du mode. Des fichiers locaux au serveur seraient
invisibles depuis une autre instance en multi-comptes, et survivraient à la suppression du
compte.

Une photo est une DONNÉE PERSONNELLE : la clé étrangère vers `users` la rattache au compte,
et la suppression de ce dernier doit l'emporter avec elle.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5d9a1c73b28"
down_revision: str | Sequence[str] | None = "c4a71f0b9e32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "profile_photos",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("jpeg", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("profile_photos")
