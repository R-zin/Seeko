"""Baseline: the schema that create_all() produced before migrations existed (jobs, logs).

Revision ID: 0001
Revises:
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("canonical_url", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column("is_easy_apply", sa.Boolean(), nullable=False),
        sa.Column("discovered_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("missing_keywords", sa.Text(), nullable=False),
        sa.Column("tailored_summary", sa.Text(), nullable=False),
        sa.Column("tailored_ok", sa.Boolean(), nullable=False),
        sa.Column("notion_page_id", sa.String(), nullable=False),
        sa.Column("notion_dirty", sa.Boolean(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_url"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("logs")
    op.drop_table("jobs")
