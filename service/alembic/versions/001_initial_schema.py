"""Initial schema: jobs, api_keys, api_usage, scrape_runs.

Revision ID: 001
Revises:
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("object_id", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False, server_default=""),
        sa.Column("location_formatted", sa.Text(), nullable=False, server_default=""),
        sa.Column("country", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.Text(), nullable=False, server_default=""),
        sa.Column("city", sa.Text(), nullable=False, server_default=""),
        sa.Column("workplace_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("seniority", sa.Text(), nullable=False, server_default=""),
        sa.Column("commitment", JSONB(), nullable=False, server_default="[]"),
        sa.Column("category", sa.Text(), nullable=False, server_default=""),
        sa.Column("role_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("salary_min_yearly", sa.Float(), nullable=True),
        sa.Column("salary_max_yearly", sa.Float(), nullable=True),
        sa.Column("salary_currency", sa.Text(), nullable=False, server_default=""),
        sa.Column("salary_frequency", sa.Text(), nullable=False, server_default=""),
        sa.Column("min_yoe", sa.Integer(), nullable=True),
        sa.Column("technical_tools", JSONB(), nullable=False, server_default="[]"),
        sa.Column("requirements_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("apply_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_ats", sa.Text(), nullable=False, server_default=""),
        sa.Column("board_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_jobs_published_at_id",
        "jobs",
        [sa.text("published_at DESC"), "id"],
    )
    op.create_index("ix_jobs_workplace_type", "jobs", ["workplace_type"])
    op.create_index("ix_jobs_seniority", "jobs", ["seniority"])
    op.create_index("ix_jobs_category", "jobs", ["category"])
    op.create_index("ix_jobs_country", "jobs", ["country"])
    op.create_index("ix_jobs_is_active", "jobs", ["is_active"])
    op.create_index("ix_jobs_last_seen_at", "jobs", ["last_seen_at"])
    op.execute(
        "CREATE INDEX ix_jobs_fts ON jobs USING gin "
        "(to_tsvector('english', coalesce(title,'') || ' ' || coalesce(company,'')))"
    )

    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("last_four", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "rate_limit_per_minute", sa.Integer(), nullable=False, server_default="120"
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "api_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "key_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_api_usage_key_id_requested_at", "api_usage", ["key_id", "requested_at"]
    )

    op.create_table(
        "scrape_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("slice_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_scrape_runs_started_at", "scrape_runs", [sa.text("started_at DESC")]
    )


def downgrade() -> None:
    op.drop_table("api_usage")
    op.drop_table("scrape_runs")
    op.drop_table("api_keys")
    op.drop_table("jobs")
