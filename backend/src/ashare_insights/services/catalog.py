from datetime import timedelta
import json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from ashare_insights.db.models import Announcement, CacheEntry, Company, DownloadTask, utc_now
from ashare_insights.providers.cninfo import SourceError


def cache_time(db, key: str):
    entry = db.get(CacheEntry, key)
    return entry.fetched_at if entry else None


def set_cache_time(db, key: str, when):
    entry = db.get(CacheEntry, key)
    if entry:
        entry.fetched_at = when
    else:
        db.add(CacheEntry(key=key, fetched_at=when))


def fresh(when, hours: float) -> bool:
    return when is not None and utc_now() - when < timedelta(hours=hours)


class CatalogService:
    def __init__(self, sessions: sessionmaker, provider):
        self.sessions = sessions
        self.provider = provider

    def search_companies(self, query: str, refresh: bool = False) -> dict:
        key = "companies"
        warning = None
        with self.sessions() as db:
            fetched = cache_time(db, key)
        if refresh or not fresh(fetched, 24):
            try:
                rows = self.provider.companies()
                now = utc_now()
                with self.sessions.begin() as db:
                    existing = {c.code: c for c in db.scalars(select(Company)).all()}
                    for row in rows:
                        company = existing.get(row["code"])
                        if company is None:
                            company = Company(code=row["code"])
                            db.add(company)
                        company.name = row["name"]
                        company.org_id = row["org_id"]
                        company.category = row["category"]
                        company.directory_fetched_at = now
                    set_cache_time(db, key, now)
                fetched = now
            except SourceError as exc:
                if fetched is None:
                    raise
                warning = str(exc)
        term = query.strip().lower()
        with self.sessions() as db:
            matches = db.scalars(select(Company).order_by(Company.code)).all()
            items = [{"id": c.id, "code": c.code, "name": c.name, "org_id": c.org_id} for c in matches if not term or term in c.code or term in c.name.lower()]
        return {"items": items[:50] if term else [], "total": len(items), "cached_at": fetched, "warning": warning}

    def get_reports(self, company_id: int, years: list[int], refresh: bool = False) -> dict:
        with self.sessions() as db:
            company = db.get(Company, company_id)
            if company is None:
                raise LookupError("公司不存在")
            identity = {"code": company.code, "org_id": company.org_id}
        warnings = []
        cached_at = {}
        for year in years:
            key = f"reports:{company_id}:{year}"
            with self.sessions() as db:
                fetched = cache_time(db, key)
            if refresh or not fresh(fetched, 0.25):
                try:
                    rows = self.provider.reports(identity, year)
                    now = utc_now()
                    with self.sessions.begin() as db:
                        existing = {a.external_id: a for a in db.scalars(select(Announcement).where(Announcement.company_id == company_id, Announcement.report_year == year)).all()}
                        for row in rows:
                            record = existing.get(row["external_id"])
                            if record is None:
                                record = Announcement(company_id=company_id)
                                db.add(record)
                            for field in ("source", "external_id", "report_year", "raw_title", "title", "version_label", "published_at", "pdf_url"):
                                setattr(record, field, row[field])
                            record.source_payload = json.dumps(row["source_payload"], ensure_ascii=False)
                            record.fetched_at = now
                        set_cache_time(db, key, now)
                    fetched = now
                except SourceError as exc:
                    if fetched is None:
                        raise
                    warnings.append(f"{year}: {exc}")
            cached_at[str(year)] = fetched
        with self.sessions() as db:
            records = db.scalars(select(Announcement).where(Announcement.company_id == company_id, Announcement.report_year.in_(years)).order_by(Announcement.published_at.desc(), Announcement.external_id.desc())).all()
            tasks = {task.announcement_id: task.status for task in db.scalars(select(DownloadTask).where(DownloadTask.announcement_id.in_([a.id for a in records])))} if records else {}
            items = []
            for a in records:
                status = "completed" if a.file and Path(a.file.path).is_file() else tasks.get(a.id, "not_downloaded")
                if status == "completed" and (not a.file or not Path(a.file.path).is_file()):
                    status = "failed"
                items.append({"id": a.id, "source": a.source, "announcement_id": a.external_id, "report_year": a.report_year, "title": a.title, "raw_title": a.raw_title, "version_label": a.version_label, "published_at": a.published_at, "pdf_url": a.pdf_url, "download_status": status})
        return {"items": items, "cached_at": cached_at, "warnings": warnings}
