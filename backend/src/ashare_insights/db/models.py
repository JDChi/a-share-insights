from datetime import date, datetime, timezone
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Boolean,
    CheckConstraint,
    Float,
    Index,
    JSON,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(6), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    org_id: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40))
    directory_fetched_at: Mapped[datetime] = mapped_column(DateTime)


class File(Base):
    __tablename__ = "files"
    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(Text)
    saved_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Announcement(Base):
    __tablename__ = "announcements"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    source: Mapped[str] = mapped_column(String(40), default="cninfo")
    external_id: Mapped[str] = mapped_column(String(80))
    report_year: Mapped[int] = mapped_column(Integer)
    raw_title: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    version_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    published_at: Mapped[date] = mapped_column(Date)
    pdf_url: Mapped[str] = mapped_column(Text)
    source_payload: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), nullable=True)
    company: Mapped[Company] = relationship()
    file: Mapped[File | None] = relationship()


class DownloadTask(Base):
    __tablename__ = "download_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    announcement: Mapped[Announcement] = relationship()


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


# Variable dimensions are deliberately stored without an ANN index. Every distance
# query is scoped to one generation/profile before exact cosine ranking.
from pgvector.sqlalchemy import Vector


class EmbeddingProfile(Base):
    __tablename__ = "embedding_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    descriptor: Mapped[dict] = mapped_column(JSON)
    dimension: Mapped[int] = mapped_column(Integer)


class IndexGeneration(Base):
    __tablename__ = "index_generations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("embedding_profiles.id"))
    pipeline_key: Mapped[str] = mapped_column(String(64), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="building")
    canonical_text: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[list | None] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (
        Index(
            "uq_active_file_pipeline",
            "file_id",
            "pipeline_key",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class IndexTask(Base):
    __tablename__ = "index_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"))
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("index_generations.id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class RagChunk(Base):
    __tablename__ = "rag_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("index_generations.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    page: Mapped[int] = mapped_column(Integer)
    dimension: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    __table_args__ = (
        CheckConstraint(
            "vector_dims(embedding) = dimension", name="ck_chunk_dimension"
        ),
    )
