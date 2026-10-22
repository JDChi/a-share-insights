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


class AgentToolCall(BaseModel):
    executed: bool
    id: str
    tool: str
    arguments: dict
    status: str
    started_at: str
    finished_at: str
    seconds: float
    result: dict | None = None
    error: str | None = None


class AgentPdfSource(CitationItem):
    type: Literal["pdf"]
    source_id: str
    quotes: list[str]


class AgentApiSource(BaseModel):
    type: Literal["api"]
    source_id: str
    metric: Literal[
        "revenue",
        "net_profit",
        "net_profit_parent",
        "total_assets",
        "total_liabilities",
        "total_equity",
        "operating_cash_flow",
        "investing_cash_flow",
        "financing_cash_flow",
    ]
    name: str
    value: str
    display_amount: str
    unit: Literal["CNY"]
    company_code: str = Field(pattern=r"^\d{6}$")
    year: int
    report_date: str
    scope: Literal["consolidated"]
    provider: Literal["akshare"]
    source: Literal["eastmoney", "sina"]
    interface: str
    source_url: str
    source_field: str
    raw_value: str
    fetched_at: str
    cached: bool
    source_notice_date: str | None
    source_update_date: str | None


class AgentAnswerResult(BaseModel):
    report_id: int
    question: str
    status: Literal["answered", "partial", "insufficient_evidence", "error"]
    answer: str
    sources: list[AgentPdfSource | AgentApiSource]
    warnings: list[str]
    tool_calls: list[AgentToolCall]
