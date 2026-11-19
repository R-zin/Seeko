"""Answer bank (questions the filler could not answer, with your answers), jobs added by you, automatic retries of
transient failures, and small app state (digest / auto-close bookkeeping).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("origin", sa.String(), server_default="search", nullable=False))   # search | user
        b.add_column(sa.Column("waiting_on", JSONType, nullable=True))       # answer_bank keys this form needs
        b.add_column(sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("retry_at", sa.DateTime(), nullable=True))

    op.create_table(
        "answer_bank",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(), server_default="text", nullable=False),
        sa.Column("options", JSONType, nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), server_default="", nullable=False),
        sa.Column("times_seen", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "app_state",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", JSONType, nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_state")
    op.drop_table("answer_bank")
    with op.batch_alter_table("jobs") as b:
        for c in ("retry_at", "attempts", "waiting_on", "origin"):
            b.drop_column(c)
