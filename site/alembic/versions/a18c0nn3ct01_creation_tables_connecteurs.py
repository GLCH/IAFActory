"""creation tables connectors et connector_events (EPIC-IAF-E18 US18.2)

Revision ID: a18c0nn3ct01
Revises: 6654f7d1b8c2
Create Date: 2026-10-03 16:20:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a18c0nn3ct01'
down_revision: Union[str, None] = '6654f7d1b8c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('connectors',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('status', sa.Enum('brouillon', 'en_attente', 'approuve', 'rejete', name='connector_status'), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('secret_encrypted', sa.LargeBinary(), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_reason', sa.Text(), nullable=True),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_sync_summary', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_connectors_owner_id'), 'connectors', ['owner_id'], unique=False)
    op.create_table('connector_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('connector_id', sa.Uuid(), nullable=False),
    sa.Column('connector_name', sa.String(length=120), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('detail', sa.String(length=500), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_connector_events_connector_id'), 'connector_events', ['connector_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_connector_events_connector_id'), table_name='connector_events')
    op.drop_table('connector_events')
    op.drop_index(op.f('ix_connectors_owner_id'), table_name='connectors')
    op.drop_table('connectors')
    sa.Enum(name='connector_status').drop(op.get_bind(), checkfirst=True)
