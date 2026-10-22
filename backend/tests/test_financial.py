import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ashare_insights.config import Settings
from ashare_insights.providers.financial import (
    AkshareProvider,
    FinancialService,
    METRICS,
    amount,
    market_symbol,
)


def raw(source="eastmoney", code="600519", year=2024):
    if source == "eastmoney":
        return {
            "SECURITY_CODE": code,
            "REPORT_DATE": f"{year}-12-31 00:00:00",
            "REPORT_TYPE": "年报",
            **{v[1]: str(i * 100) for i, v in enumerate(METRICS.values(), 1)},
        }
    return {
        "报告日": f"{year}1231",
        "类型": "合并期末",
        **{v[2]: str(i * 100) for i, v in enumerate(METRICS.values(), 1)},
    }


class Provider:
    def __init__(self, rows=None):
        self.rows = rows
        self.calls = []

    def fetch(self, source, code, table):
        self.calls.append((source, code, table))
        if self.rows is not None:
            value = self.rows.get((source, table), [])
            if isinstance(value, Exception):
                raise value
            return value
        return [raw(source, code)]


def service(tmp_path, provider):
    return FinancialService(Settings(data_dir=tmp_path), provider)


def test_nine_fields_cache_and_requested_statements_only(tmp_path):
    provider = Provider()
    financial = service(tmp_path, provider)
    r = financial.query("000333", 2024, list(METRICS))
    assert r["status"] == "ok" and len(r["items"]) == 9
    assert len(provider.calls) == 3
    assert {i["scope"] for i in r["items"]} == {"consolidated"}
    assert {i["unit"] for i in r["items"]} == {"CNY"}
    financial.query("000333", 2024, ["revenue"])
    assert len(provider.calls) == 3
    assert financial.query("000333", 2024, ["revenue"])["items"][0]["cached"]
    # Expire on disk: do not rely on wall-clock sleep.
    for path in financial.cache_dir.glob("*.json"):
        payload = json.loads(path.read_text())
        payload["fetched_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        path.write_text(json.dumps(payload))
    financial.query("000333", 2024, ["revenue"])
    assert len(provider.calls) == 4


def test_fallback_missing_negative_zero_and_metadata(tmp_path):
    em, sina = raw(), raw("sina")
    em["OPERATE_INCOME"] = None
    em["NETPROFIT"] = "-123.50"
    sina["营业收入"] = "0"
    sina["公告日期"] = "20260417"
    sina["更新日期"] = "2025-04-02"
    p = Provider({("eastmoney", "income"): [em], ("sina", "income"): [sina]})
    r = service(tmp_path, p).query("600519", 2024, ["revenue", "net_profit"])
    assert r["status"] == "ok"
    items = {i["metric"]: i for i in r["items"]}
    assert items["revenue"]["value"] == "0" and items["revenue"]["source"] == "sina"
    assert items["net_profit"]["value"] == "-123.50"
    assert (
        items["revenue"]["source_notice_date"] != items["revenue"]["source_update_date"]
    )
    assert [x[0] for x in p.calls] == ["eastmoney", "sina"]


def test_no_data_unsupported_error_and_retry(tmp_path):
    p = Provider(
        {("eastmoney", "income"): TimeoutError("secret"), ("sina", "income"): []}
    )
    f = service(tmp_path, p)
    r = f.query("600519", 1990, ["revenue"])
    assert r["status"] == "error" and not r["items"] and "secret" not in json.dumps(r)
    f.query("600519", 1990, ["revenue"])
    assert len(p.calls) == 4  # no failure/empty cache
    assert f.query("600519", 2024, ["quarterly_revenue"])["status"] == "unsupported"
    assert (
        service(tmp_path, Provider({})).query("600519", 1990, ["revenue"])["status"]
        == "no_data"
    )
    r = service(tmp_path, Provider()).query("600519", 2024, ["revenue", "forecast"])
    assert r["status"] == "partial"


def test_wrong_year_company_scope_and_duplicate_rows(tmp_path):
    row = raw()
    row["SECURITY_CODE"] = "000333"
    sina = raw("sina")
    sina["类型"] = "母公司期末"
    p = Provider({("eastmoney", "income"): [row], ("sina", "income"): [sina]})
    assert (
        service(tmp_path, p).query("600519", 2024, ["revenue"])["status"] == "no_data"
    )
    assert (
        service(tmp_path, Provider()).query("600519", 2023, ["revenue"])["status"]
        == "no_data"
    )
    p = Provider(
        {("eastmoney", "income"): [raw(), raw()], ("sina", "income"): [raw("sina")]}
    )
    r = service(tmp_path / "duplicates", p).query("600519", 2024, ["revenue"])
    assert r["status"] == "ok" and r["items"][0]["source"] == "sina"


def test_input_and_subprocess_timeout(monkeypatch):
    for v in (600519, "333", "000333;echo"):
        with pytest.raises(ValueError):
            market_symbol(v)
    assert market_symbol("920000") == "BJ920000"
    assert market_symbol("430418") == "BJ430418"
    for v in (None, "-", "", "nan", "inf"):
        assert amount(v) is None
    assert amount("1,234.50") == "1234.50"

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 0.01)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(TimeoutError):
        AkshareProvider(0.01).fetch("eastmoney", "600519", "income")


def test_real_frozen_fixtures_15_companies(tmp_path):
    path = Path(__file__).parent / "fixtures/financial-15.json"
    if not path.exists():
        pytest.fail("Missing frozen 15-company source fixtures")
    fixture = json.loads(path.read_text())
    assert {
        m: sum(c["market"] == m for c in fixture["companies"])
        for m in ("SH", "SZ", "BJ")
    } == dict(SH=5, SZ=5, BJ=5)
    for company in fixture["companies"]:
        rows = {(x["source"], x["statement"]): x["rows"] for x in company["tables"]}
        result = service(tmp_path / company["code"], Provider(rows)).query(
            company["code"], 2024, list(METRICS)
        )
        assert result["status"] == company["expected_status"]
        assert {i["metric"]: i["value"] for i in result["items"]} == company[
            "expected_values"
        ]


def test_frozen_sina_fallback_all_nine_fields(tmp_path):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/financial-15.json").read_text()
    )
    company = next(c for c in fixture["companies"] if c["code"] == "600519")
    rows = {
        (x["source"], x["statement"]): x["rows"]
        for x in company["tables"]
        if x["source"] == "sina"
    }
    for table in ("income", "balance", "cashflow"):
        rows[("eastmoney", table)] = TimeoutError()
    result = service(tmp_path, Provider(rows)).query("600519", 2024, list(METRICS))
    assert result["status"] == "ok" and len(result["items"]) == 9
    assert {i["source"] for i in result["items"]} == {"sina"}
    assert {i["metric"]: i["value"] for i in result["items"]} == company[
        "expected_values"
    ]


def test_all_amount_mentions_must_match_not_just_one():
    from ashare_insights.providers.financial import (
        display_amount,
        supported_money_mentions,
    )

    value = "41385234406.72"
    assert display_amount(value) == "约413.85亿元"
    assert supported_money_mentions("41,385,234,406.72元，约413.85亿元", [value])
    assert not supported_money_mentions(
        "41,385,234,406.72元，约413.85亿元，约413,852.34万元", [value]
    )
    assert supported_money_mentions(
        "-16.29亿元，即净流出约16.29亿元", ["-1628962704.56"]
    )
    assert not supported_money_mentions("16.29亿元", ["-1628962704.56"])
