"""PostgreSQL text RAG generations and vectors."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0002_text_rag"
down_revision = "0001_initial"
branch_labels = depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "embedding_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("descriptor", sa.JSON(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
    )
    op.create_table(
        "index_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id"), nullable=False),
        sa.Column(
            "profile_id",
            sa.String(64),
            sa.ForeignKey("embedding_profiles.id"),
            nullable=False,
        ),
        sa.Column("pipeline_key", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("canonical_text", sa.Text()),
        sa.Column("pages", sa.JSON()),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_index_generations_file_id", "index_generations", ["file_id"])
    op.create_index(
        "ix_index_generations_pipeline_key", "index_generations", ["pipeline_key"]
    )
    op.create_index(
        "uq_active_file_pipeline",
        "index_generations",
        ["file_id", "pipeline_key"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "index_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "announcement_id",
            sa.Integer(),
            sa.ForeignKey("announcements.id"),
            nullable=False,
        ),
        sa.Column(
            "generation_id",
            sa.String(36),
            sa.ForeignKey("index_generations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_index_tasks_status", "index_tasks", ["status"])
    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "generation_id",
            sa.String(36),
            sa.ForeignKey("index_generations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.CheckConstraint(
            "vector_dims(embedding) = dimension", name="ck_chunk_dimension"
        ),
    )
    op.create_index("ix_rag_chunks_generation_id", "rag_chunks", ["generation_id"])


def downgrade():
    op.drop_table("rag_chunks")
    op.drop_table("index_tasks")
    op.drop_table("index_generations")
    op.drop_table("embedding_profiles")
