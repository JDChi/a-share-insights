import json
from pathlib import Path
from urllib.parse import parse_qs
import httpx
import pytest
from ashare_insights.providers.cninfo import CninfoProvider, SourceError, annual_title


def test_title_classification():
    assert annual_title("贵州茅台2024年年度报告", 2024) == ("贵州茅台2024年年度报告", None)
    assert annual_title("2024年年度报告（修订版）", 2024) == ("2024年年度报告（修订版）", "（修订版）")
    for title in ("2024年年度报告摘要", "2024年年度报告（英文版）", "2023年年度报告", "2024年年度报告更正公告"):
        assert annual_title(title, 2024) is None


def test_real_response_fixtures_cover_three_markets(fixture_transport):
    with httpx.Client(transport=fixture_transport) as client:
        provider = CninfoProvider(client=client)
        companies = {item["code"]: item for item in provider.companies()}
        assert set(companies) == {"600519", "000333", "430418"}
        for code in companies:
            reports = provider.reports(companies[code], 2024)
            assert len(reports) == 1
            assert reports[0]["report_year"] == 2024
            assert reports[0]["published_at"].year == 2025
            assert reports[0]["pdf_url"].startswith("https://static.cninfo.com.cn/finalpage/")


def test_pagination_failure_never_returns_partial_results():
    def respond(request):
        page = parse_qs(request.content.decode())["pageNum"][0]
        if page == "1":
            payload = json.loads((Path(__file__).parent / "fixtures/reports-600519.json").read_text())
            payload["hasMore"] = True
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"hasMore": True, "announcements": []})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = CninfoProvider(client=client)
        with pytest.raises(SourceError):
            provider.reports({"code": "600519", "org_id": "gssh0600519"}, 2024)


def test_missing_announcements_is_an_error():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"hasMore": False}))) as client:
        provider = CninfoProvider(client=client)
        with pytest.raises(SourceError, match="announcements"):
            provider.reports({"code": "600519", "org_id": "gssh0600519"}, 2024)


def test_upstream_timeout_is_distinct_from_empty_results():
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(SourceError, match="请求或响应失败"):
            CninfoProvider(client=client).companies()
