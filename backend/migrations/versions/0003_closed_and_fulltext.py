"""Closed postings (gone from their board or expired) and full-text search over saved job descriptions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

FTS_DOC = "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(company, '') || ' ' || coalesce(jd_text, ''))"


def upgrade() -> None:
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_jobs_closed_at", "jobs", ["closed_at"])
    op.execute("UPDATE jobs SET closed_at = valid_through WHERE valid_through IS NOT NULL AND valid_through < CURRENT_TIMESTAMP")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"CREATE INDEX ix_jobs_fts ON jobs USING gin ({FTS_DOC})")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_jobs_fts")
    op.drop_index("ix_jobs_closed_at", table_name="jobs")
    with op.batch_alter_table("jobs") as b:
        b.drop_column("closed_at")
