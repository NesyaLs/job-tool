"""journal des échanges avec le modèle (llm_runs, llm_exchanges)

Revision ID: c4a71f0b9e32
Revises: 7b3e2c91d4a0
Create Date: 2026-09-18 10:00:00.000000

Deux tables plutôt qu'une : la liste de l'onglet d'administration ne lit que `llm_runs`
(aucun prompt transporté), le détail ne charge les `llm_exchanges` que d'une génération.
La rétention est applicative (10 générations par type) : rien à planifier côté base.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c4a71f0b9e32'
down_revision: Union[str, Sequence[str], None] = '7b3e2c91d4a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_ = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'llm_runs',
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('offer_id', sa.Text(), nullable=True),
        sa.Column('template', sa.Text(), server_default='', nullable=False),
        sa.Column('model', sa.Text(), server_default='', nullable=False),
        sa.Column('status', sa.Text(), server_default='running', nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('calls', sa.Integer(), server_default='0', nullable=False),
        sa.Column('started_at', sa.Text(), nullable=False),
        sa.Column('ended_at', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('run_id'),
    )
    op.create_index('idx_llm_runs_kind', 'llm_runs', ['kind', 'started_at'])

    op.create_table(
        'llm_exchanges',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.Text(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('step', sa.Text(), server_default='', nullable=False),
        sa.Column('attempt', sa.Integer(), server_default='1', nullable=False),
        sa.Column('retries_429', sa.Integer(), server_default='0', nullable=False),
        sa.Column('messages', JSON_, nullable=False),
        sa.Column('response', JSON_, nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['llm_runs.run_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_llm_exchanges_run', 'llm_exchanges', ['run_id', 'seq'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_llm_exchanges_run', table_name='llm_exchanges')
    op.drop_table('llm_exchanges')
    op.drop_index('idx_llm_runs_kind', table_name='llm_runs')
    op.drop_table('llm_runs')
