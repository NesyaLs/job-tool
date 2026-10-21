"""documents: source réécrite du mode pro

Revision ID: 7b3e2c91d4a0
Revises: 20e65fffe14e
Create Date: 2026-09-17 05:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7b3e2c91d4a0'
down_revision: Union[str, Sequence[str], None] = '20e65fffe14e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, sans valeur par défaut : NULL = mode normal pour tous les documents existants.
    op.add_column(
        'documents',
        sa.Column(
            'edited_input',
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('documents', 'edited_input')
