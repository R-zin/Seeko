"""Job search tracking: job/internship type, posting metadata, sightings per run, search runs, rejected pages.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""
import re
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("job_type", sa.String(), server_default="job", nullable=False))
        b.add_column(sa.Column("employment_type", sa.String(), server_default="", nullable=False))
        b.add_column(sa.Column("is_remote", sa.Boolean(), server_default=sa.false(), nullable=False))
        b.add_column(sa.Column("posted_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("valid_through", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("jd_source", sa.String(), server_default="", nullable=False))
        b.add_column(sa.Column("relevance", sa.Float(), nullable=True))
        b.add_column(sa.Column("last_seen_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("times_seen", sa.Integer(), server_default="1", nullable=False))
        b.add_column(sa.Column("first_run_id", sa.String(), server_default="", nullable=False))
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_discovered_at", "jobs", ["discovered_at"])
    op.execute("UPDATE jobs SET last_seen_at = discovered_at WHERE last_seen_at IS NULL")
    # Classify existing rows by title (self-contained copy of the rule; migrations must not import app code).
    conn = op.get_bind()
    intern = re.compile(r"\b(intern|interns|internship|internships|trainee|apprentice|apprenticeship|co-?op)\b", re.I)
    ids = [r[0] for r in conn.execute(sa.text("SELECT id, title FROM jobs")) if intern.search(r[1] or "")]
    upd = sa.text("UPDATE jobs SET job_type = 'internship' WHERE id IN :ids").bindparams(sa.bindparam("ids", expanding=True))
    for i in range(0, len(ids), 500):
        conn.execute(upd, {"ids": ids[i:i + 500]})

    op.create_table(
        "search_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("spec", JSONType, nullable=False),
        sa.Column("stats", JSONType, nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_runs_started_at", "search_runs", ["started_at"])
    op.create_table(
        "job_sightings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("is_new", sa.Boolean(), nullable=False),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["search_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "job_id"),
    )
    op.create_index("ix_job_sightings_job_id", "job_sightings", ["job_id"])
    op.create_table(
        "seen_urls",
        sa.Column("canonical_url", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("canonical_url"),
    )


def downgrade() -> None:
    op.drop_table("seen_urls")
    op.drop_index("ix_job_sightings_job_id", table_name="job_sightings")
    op.drop_table("job_sightings")
    op.drop_index("ix_search_runs_started_at", table_name="search_runs")
    op.drop_table("search_runs")
    op.drop_index("ix_jobs_discovered_at", table_name="jobs")
    op.drop_index("ix_jobs_job_type", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    with op.batch_alter_table("jobs") as b:
        for c in ("first_run_id", "times_seen", "last_seen_at", "relevance", "jd_source", "valid_through", "posted_at",
                  "is_remote", "employment_type", "job_type"):
            b.drop_column(c)
