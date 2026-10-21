from datetime import date, datetime
from pydantic import BaseModel


class CompanyItem(BaseModel):
    id: int
    code: str
    name: str
    org_id: str


class CompanyList(BaseModel):
    items: list[CompanyItem]
    total: int
    cached_at: datetime | None
    warning: str | None


class ReportItem(BaseModel):
    id: int
    source: str
    announcement_id: str
    report_year: int
    title: str
    raw_title: str
    version_label: str | None
    published_at: date
    pdf_url: str
    download_status: str


class ReportList(BaseModel):
    items: list[ReportItem]
    cached_at: dict[str, datetime | None]
    warnings: list[str]


class TaskItem(BaseModel):
    id: int
    announcement_id: int
    status: str
    error: str | None
    attempts: int
    updated_at: datetime


from typing import Literal
from pydantic import Field


class IndexRequest(BaseModel):
    force: bool = False


class IndexTaskItem(BaseModel):
    id: int
    announcement_id: int
    generation_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: float
    attempts: int
    updated_at: datetime
    error: str | None
    warnings: list[str]
    active: bool


class RetrievalRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class RetrievedItem(BaseModel):
    id: str
    text: str
    score: float
    page: int
    start: int
    end: int
    report_id: int
    title: str
    company_code: str
    report_year: int
    file_sha256: str
    file_url: str
    pdf_url: str


class RetrievalResult(BaseModel):
    report_id: int
    generation_id: str
    pipeline_key: str
    profile_id: str
    question: str
    items: list[RetrievedItem]
    warnings: list[str]


class CitationItem(BaseModel):
    chunk_id: str
    quote: str
    page: int
    report_id: int
    title: str
    file_url: str
    file_sha256: str


class AnswerResult(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    citations: list[CitationItem]
    retrieval: RetrievalResult
