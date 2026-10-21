from datetime import date
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from ashare_insights.config import Settings
from ashare_insights.db.models import Announcement, Company, DownloadTask, File, utc_now
from ashare_insights.main import create_app
from ashare_insights.providers.cninfo import CninfoProvider
from ashare_insights.providers.cninfo import SourceError


def test_search_reports_download_and_file(api):
    client, app = api
    last_report_id = None
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.post("/api/v1/companies/refresh").status_code == 200
    for code in ("600519", "000333", "430418"):
        company = client.get("/api/v1/companies", params={"q": code}).json()["items"][0]
        assert company["code"] == code
        report_response = client.get("/api/v1/reports", params={"company_id": company["id"], "years": "2024"})
        assert report_response.status_code == 200
        reports = report_response.json()["items"]
        last_report_id = reports[0]["id"]
        assert len(reports) == 1
        assert reports[0]["published_at"].startswith("2025-")
        task = client.post(f'/api/v1/reports/{reports[0]["id"]}/download').json()
        app.state.downloads.queue.join()
        assert client.get(f'/api/v1/downloads/{task["id"]}').json()["status"] == "completed"
        file_response = client.get(f'/api/v1/reports/{reports[0]["id"]}/file')
        assert file_response.status_code == 200
        assert file_response.content.startswith(b"%PDF-")
        again = client.post(f'/api/v1/reports/{reports[0]["id"]}/download').json()
        assert again["id"] == task["id"]
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Company)) == 3
        assert db.scalar(select(func.count()).select_from(Announcement)) == 3
        assert db.scalar(select(func.count()).select_from(File)) == 1
        assert db.scalar(select(func.count()).select_from(DownloadTask)) == 3
    offline_client = httpx.Client(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(AssertionError("network used"))))
    offline_app = create_app(Settings(data_dir=app.state.downloads.data_dir), CninfoProvider(client=offline_client))
    with TestClient(offline_app) as restarted:
        assert restarted.get(f"/api/v1/reports/{last_report_id}/file").status_code == 200
    offline_client.close()


def test_bad_input_and_missing_file(api):
    client, _ = api
    assert client.get("/api/v1/reports", params={"company_id": 999, "years": "2024"}).status_code == 404
    assert client.get("/api/v1/reports", params={"company_id": 1, "years": "2024,wrong"}).status_code == 422
    assert client.get("/api/v1/reports/999/file").status_code == 404


def test_restart_marks_running_failed_and_keeps_queued(api):
    client, app = api
    client.post("/api/v1/companies/refresh")
    company_id = client.get("/api/v1/companies", params={"q": "600519"}).json()["items"][0]["id"]
    report_id = client.get("/api/v1/reports", params={"company_id": company_id, "years": "2024"}).json()["items"][0]["id"]
    with app.state.sessions.begin() as db:
        db.add(DownloadTask(announcement_id=report_id, status="running"))
    app.state.downloads.stop()
    app.state.downloads.start()
    with app.state.sessions() as db:
        task = db.scalar(select(DownloadTask))
        assert task.status == "failed"
        assert "中断" in task.error
    assert client.get("/api/v1/reports", params={"company_id": company_id, "years": "2024"}).json()["items"][0]["download_status"] == "failed"


def test_refresh_failure_uses_stale_directory(api, monkeypatch):
    client, app = api
    client.post("/api/v1/companies/refresh")

    def fail():
        raise SourceError("上游超时")

    monkeypatch.setattr(app.state.catalog.provider, "companies", fail)
    response = client.post("/api/v1/companies/refresh")
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert "上游超时" in response.json()["warning"]


def test_report_refresh_is_atomic_and_ids_are_unique(api, monkeypatch):
    client, app = api
    client.post("/api/v1/companies/refresh")
    company_id = client.get("/api/v1/companies", params={"q": "600519"}).json()["items"][0]["id"]

    def fail(_, __):
        raise SourceError("第二页异常")

    original = app.state.catalog.provider.reports
    monkeypatch.setattr(app.state.catalog.provider, "reports", fail)
    response = client.get("/api/v1/reports", params={"company_id": company_id, "years": "2024"})
    assert response.status_code == 502
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Announcement)) == 0

    monkeypatch.setattr(app.state.catalog.provider, "reports", original)
    report = client.get("/api/v1/reports", params={"company_id": company_id, "years": "2024"}).json()["items"][0]
    with pytest.raises(IntegrityError):
        with app.state.sessions.begin() as db:
            db.add(Announcement(
                company_id=company_id, source="cninfo", external_id=report["announcement_id"],
                report_year=2024, raw_title="重复", title="重复", published_at=date(2025, 1, 1),
                pdf_url=report["pdf_url"], source_payload="{}", fetched_at=utc_now(),
            ))


def test_multiple_versions_are_preserved(api, monkeypatch):
    client, app = api
    client.post("/api/v1/companies/refresh")
    company_id = client.get("/api/v1/companies", params={"q": "600519"}).json()["items"][0]["id"]
    original = app.state.catalog.provider.reports

    def two_versions(company, year):
        first = original(company, year)[0]
        revised = dict(first, external_id="1222993999", raw_title="贵州茅台2024年年度报告（修订版）", title="贵州茅台2024年年度报告（修订版）", version_label="（修订版）")
        return [first, revised]

    monkeypatch.setattr(app.state.catalog.provider, "reports", two_versions)
    response = client.get("/api/v1/reports", params={"company_id": company_id, "years": "2024"})
    assert response.status_code == 200
    assert {item["announcement_id"] for item in response.json()["items"]} == {"1222993920", "1222993999"}
    assert any(item["version_label"] == "（修订版）" for item in response.json()["items"])


def test_failed_download_can_be_retried(api):
    client, app = api
    client.post("/api/v1/companies/refresh")
    company_id = client.get("/api/v1/companies", params={"q": "600519"}).json()["items"][0]["id"]
    report_id = client.get("/api/v1/reports", params={"company_id": company_id, "years": "2024"}).json()["items"][0]["id"]
    bad = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"not a PDF")))
    good = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"%PDF-1.4\n%%EOF\n")))
    try:
        app.state.downloads.client = bad
        task = client.post(f"/api/v1/reports/{report_id}/download").json()
        app.state.downloads.queue.join()
        assert client.get(f'/api/v1/downloads/{task["id"]}').json()["status"] == "failed"
        app.state.downloads.client = good
        retry = client.post(f"/api/v1/reports/{report_id}/download").json()
        assert retry["id"] == task["id"]
        app.state.downloads.queue.join()
        assert client.get(f'/api/v1/downloads/{task["id"]}').json()["status"] == "completed"
    finally:
        bad.close()
        good.close()
