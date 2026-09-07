"""Reconcile recording schema with ORM models

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index('ix_recordings_stream_id', 'recordings', ['stream_id'])
    op.create_index('ix_recordings_start_time', 'recordings', ['start_time'])
    op.create_index('ix_recordings_end_time', 'recordings', ['end_time'])
    op.create_index('ix_recordings_status', 'recordings', ['status'])
    op.create_index('ix_snapshots_recording_id', 'snapshots', ['recording_id'])
    op.create_index('ix_snapshots_timestamp', 'snapshots', ['timestamp'])
    op.create_index('ix_evidences_recording_id', 'evidences', ['recording_id'])
    op.create_index('ix_event_recording_links_event_id', 'event_recording_links', ['event_id'])
    op.create_index('ix_event_recording_links_recording_id', 'event_recording_links', ['recording_id'])
    with op.batch_alter_table('snapshots') as batch_op:
        batch_op.alter_column('file_path', existing_type=sa.String(), nullable=False)
    with op.batch_alter_table('evidences') as batch_op:
        batch_op.alter_column('file_path', existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('evidences') as batch_op:
        batch_op.alter_column('file_path', existing_type=sa.String(), nullable=True)
    with op.batch_alter_table('snapshots') as batch_op:
        batch_op.alter_column('file_path', existing_type=sa.String(), nullable=True)
    op.drop_index('ix_event_recording_links_recording_id', table_name='event_recording_links')
    op.drop_index('ix_event_recording_links_event_id', table_name='event_recording_links')
    op.drop_index('ix_evidences_recording_id', table_name='evidences')
    op.drop_index('ix_snapshots_timestamp', table_name='snapshots')
    op.drop_index('ix_snapshots_recording_id', table_name='snapshots')
    op.drop_index('ix_recordings_status', table_name='recordings')
    op.drop_index('ix_recordings_end_time', table_name='recordings')
    op.drop_index('ix_recordings_start_time', table_name='recordings')
    op.drop_index('ix_recordings_stream_id', table_name='recordings')
