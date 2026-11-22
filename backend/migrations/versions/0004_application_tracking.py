"""Application tracking (status after applying, next step, notes), live Notion sync bookkeeping, and change
timestamps that the backup mirror copies by.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("application_status", sa.String(), server_default="not_applied", nullable=False))
        b.add_column(sa.Column("next_step", sa.String(), server_default="", nullable=False))
        b.add_column(sa.Column("next_step_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("notes", sa.Text(), server_default="", nullable=False))
        b.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("notion_hash", sa.String(), server_default="", nullable=False))
        b.add_column(sa.Column("notion_base", JSONType, nullable=True))
        b.add_column(sa.Column("notion_synced_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("notion_error", sa.Text(), server_default="", nullable=False))
        b.add_column(sa.Column("notion_attempts", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("notion_retry_at", sa.DateTime(), nullable=True))
    op.create_index("ix_jobs_updated_at", "jobs", ["updated_at"])
    op.create_index("ix_jobs_notion_dirty", "jobs", ["notion_dirty"])
    op.execute("UPDATE jobs SET application_status = 'applied' WHERE status = 'applied'")
    op.execute("UPDATE jobs SET updated_at = COALESCE(applied_at, last_seen_at, discovered_at)")
    op.execute("UPDATE jobs SET notion_dirty = true")   # push the new properties (and descriptions) to Notion once

    with op.batch_alter_table("search_runs") as b:
        b.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE search_runs SET updated_at = COALESCE(finished_at, started_at)")


def downgrade() -> None:
    with op.batch_alter_table("search_runs") as b:
        b.drop_column("updated_at")
    op.drop_index("ix_jobs_notion_dirty", table_name="jobs")
    op.drop_index("ix_jobs_updated_at", table_name="jobs")
    with op.batch_alter_table("jobs") as b:
        for c in ("notion_retry_at", "notion_attempts", "notion_error", "notion_synced_at", "notion_base", "notion_hash",
                  "updated_at", "notes", "next_step_date", "next_step", "application_status"):
            b.drop_column(c)
