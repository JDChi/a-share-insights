from datetime import date, datetime, timezone
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    announcement: Mapped[Announcement] = relationship()


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
