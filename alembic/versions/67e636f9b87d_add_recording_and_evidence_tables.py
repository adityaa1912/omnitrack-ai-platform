"""Add recording and evidence tables

Revision ID: 67e636f9b87d
Revises: a7efbfa5892e
Create Date: 2026-08-22 17:44:44.661407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '67e636f9b87d'
down_revision: Union[str, Sequence[str], None] = 'a7efbfa5892e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'recordings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('stream_id', sa.String(), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=True),
        sa.Column('end_time', sa.DateTime(), nullable=True),
        sa.Column('file_path', sa.String(), nullable=True, default=""),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recording_id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('file_path', sa.String(), nullable=True, default=""),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'evidences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recording_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('file_path', sa.String(), nullable=True, default=""),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'event_recording_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('recording_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('event_recording_links')
    op.drop_table('evidences')
    op.drop_table('snapshots')
    op.drop_table('recordings')
