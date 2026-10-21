"""Initial normalized annual-report catalog.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("org_id", sa.String(80), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("directory_fetched_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_companies_code", "companies", ["code"], unique=True)
    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("saved_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("report_year", sa.Integer(), nullable=False),
        sa.Column("raw_title", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("version_label", sa.String(120)),
        sa.Column("published_at", sa.Date(), nullable=False),
        sa.Column("pdf_url", sa.Text(), nullable=False),
        sa.Column("source_payload", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id")),
        sa.UniqueConstraint("source", "external_id"),
    )
    op.create_index("ix_announcements_company_id", "announcements", ["company_id"])
    op.create_table(
        "download_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("announcement_id", sa.Integer(), sa.ForeignKey("announcements.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_download_tasks_status", "download_tasks", ["status"])
    op.create_table(
        "cache_entries",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("cache_entries")
    op.drop_index("ix_download_tasks_status", table_name="download_tasks")
    op.drop_table("download_tasks")
    op.drop_index("ix_announcements_company_id", table_name="announcements")
    op.drop_table("announcements")
    op.drop_table("files")
    op.drop_index("ix_companies_code", table_name="companies")
    op.drop_table("companies")
