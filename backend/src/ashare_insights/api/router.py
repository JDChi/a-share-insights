from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import text
from ashare_insights.api.schemas import CompanyList, ReportList, TaskItem
from ashare_insights.db.models import Announcement
from ashare_insights.providers.cninfo import SHANGHAI, SourceError

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health(request: Request):
    with request.app.state.sessions() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok"}


def source_call(action):
    try:
        return action()
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/companies", response_model=CompanyList)
def companies(request: Request, q: str = Query(min_length=1)):
    return source_call(lambda: request.app.state.catalog.search_companies(q))


@router.post("/companies/refresh", response_model=CompanyList)
def refresh_companies(request: Request):
    return source_call(lambda: request.app.state.catalog.search_companies("", refresh=True))


@router.get("/reports", response_model=ReportList)
def reports(request: Request, company_id: int, years: str | None = None, refresh: bool = False):
    current_year = datetime.now(SHANGHAI).year
    if years is None:
        chosen = list(range(current_year - 3, current_year))
    else:
        try:
            chosen = sorted(set(int(x.strip()) for x in years.split(",")))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="years 应为逗号分隔的年份") from exc
    if not chosen or any(year < 1990 or year >= current_year for year in chosen) or len(chosen) > 20:
        raise HTTPException(status_code=422, detail="请输入 1990 年至上一年的最多 20 个报告年份")
    return source_call(lambda: request.app.state.catalog.get_reports(company_id, chosen, refresh))


@router.post("/reports/{report_id}/download", status_code=202, response_model=TaskItem)
def create_download(request: Request, report_id: int):
    return source_call(lambda: request.app.state.downloads.request(report_id))


@router.get("/downloads/{task_id}", response_model=TaskItem)
def get_download(request: Request, task_id: int):
    return source_call(lambda: request.app.state.downloads.get(task_id))


@router.get("/reports/{report_id}/file")
def report_file(request: Request, report_id: int):
    with request.app.state.sessions() as db:
        report = db.get(Announcement, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="公告不存在")
        if report.file is None or not Path(report.file.path).is_file():
            raise HTTPException(status_code=404, detail="PDF 尚未下载")
        path = report.file.path
        filename = f"{report.company.code}_{report.report_year}_{report.external_id}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=filename)
