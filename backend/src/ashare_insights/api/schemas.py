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
