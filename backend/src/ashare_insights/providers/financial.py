"""Annual consolidated financial data, independent of the agent and RAG."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

# metric: statement, Eastmoney field, Sina field, Chinese name
METRICS = {
    "revenue": ("income", "OPERATE_INCOME", "营业收入", "营业收入"),
    "net_profit": ("income", "NETPROFIT", "净利润", "净利润"),
    "net_profit_parent": (
        "income",
        "PARENT_NETPROFIT",
        "归属于母公司所有者的净利润",
        "归母净利润",
    ),
    "total_assets": ("balance", "TOTAL_ASSETS", "资产总计", "总资产"),
    "total_liabilities": ("balance", "TOTAL_LIABILITIES", "负债合计", "总负债"),
    "total_equity": (
        "balance",
        "TOTAL_EQUITY",
        "所有者权益(或股东权益)合计",
        "所有者权益",
    ),
    "operating_cash_flow": (
        "cashflow",
        "NETCASH_OPERATE",
        "经营活动产生的现金流量净额",
        "经营活动现金流量净额",
    ),
    "investing_cash_flow": (
        "cashflow",
        "NETCASH_INVEST",
        "投资活动产生的现金流量净额",
        "投资活动现金流量净额",
    ),
    "financing_cash_flow": (
        "cashflow",
        "NETCASH_FINANCE",
        "筹资活动产生的现金流量净额",
        "筹资活动现金流量净额",
    ),
}
EM_FUNCTIONS = {
    "income": "stock_profit_sheet_by_yearly_em",
    "balance": "stock_balance_sheet_by_yearly_em",
    "cashflow": "stock_cash_flow_sheet_by_yearly_em",
}
SINA_TABLES = {"income": "利润表", "balance": "资产负债表", "cashflow": "现金流量表"}


def market_symbol(code):
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        raise ValueError("股票代码必须为六位字符串")
    return ("SH" if code.startswith("6") else "BJ" if code[0] in "489" else "SZ") + code


class FinancialProvider(Protocol):
    def fetch(self, source: str, code: str, statement: str) -> list[dict]: ...


class AkshareProvider:
    """An isolated worker bounds the *whole* AKShare call, including its pagination."""

    def __init__(self, timeout=30):
        self.timeout = timeout

    def fetch(self, source, code, statement):
        market_symbol(code)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "ashare_insights.providers.financial_worker"],
                input=json.dumps(dict(source=source, code=code, statement=statement)),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=True,
            )
            payload = json.loads(completed.stdout)
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload["rows"]
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("财务上游请求超时") from exc
        except (subprocess.SubprocessError, json.JSONDecodeError, KeyError) as exc:
            raise RuntimeError("财务上游调用或返回格式异常") from exc


def amount(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
        return format(number, "f") if number.is_finite() else None
    except InvalidOperation:
        return None


def annual_row(rows, source, year, code):
    selected = []
    for row in rows:
        date = str(row.get("REPORT_DATE" if source == "eastmoney" else "报告日", ""))
        digits = re.sub(r"\D", "", date)[:8]
        if digits != f"{year}1231":
            continue
        if source == "sina" and row.get("类型") != "合并期末":
            continue
        if source == "eastmoney":
            if (
                row.get("REPORT_TYPE") != "年报"
                or str(row.get("SECURITY_CODE", "")) != code
            ):
                continue
        selected.append(row)
    # Never silently choose between duplicate versions of the same annual row.
    if len(selected) > 1:
        raise ValueError("上游同年度存在多个记录，无法确认版本")
    return selected[0] if selected else None


class FinancialService:
    def __init__(self, settings, provider: FinancialProvider | None = None):
        self.provider = provider or AkshareProvider(settings.financial_timeout)
        self.cache_dir = settings.data_dir / "financial-cache"
        self.ttl = settings.financial_cache_ttl

    def _rows(self, source, code, statement):
        key = f"akshare-1.19.1-v1-{source}-{code}-{statement}"
        path = self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        now = datetime.now(UTC)
        try:
            payload = json.loads(path.read_text())
            age = (now - datetime.fromisoformat(payload["fetched_at"])).total_seconds()
            if 0 <= age < self.ttl:
                return payload["rows"], payload["fetched_at"], True
        except (OSError, ValueError, KeyError, TypeError):
            pass
        rows = self.provider.fetch(source, code, statement)
        fetched = now.isoformat()
        if rows:  # No empty/error response cache.
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.cache_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(
                        dict(rows=rows, fetched_at=fetched),
                        f,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                os.replace(temp, path)
            finally:
                Path(temp).unlink(missing_ok=True)
        return rows, fetched, False

    def query(self, code, year, metrics):
        market_symbol(code)
        if (
            isinstance(year, bool)
            or not isinstance(year, int)
            or not 1990 <= year <= 2100
        ):
            raise ValueError("年份须为1990至2100的整数")
        if (
            not isinstance(metrics, list)
            or not metrics
            or len(metrics) > 9
            or any(not isinstance(m, str) for m in metrics)
        ):
            raise ValueError("请输入1至9个指标名称")
        metrics = list(dict.fromkeys(metrics))
        unsupported = [m for m in metrics if m not in METRICS]
        result = dict(
            status="no_data",
            company_code=code,
            year=year,
            scope="consolidated",
            items=[],
            missing=[],
            unsupported=unsupported,
            attempts=[],
            warnings=[],
        )
        wanted = [m for m in metrics if m in METRICS]
        for statement in dict.fromkeys(METRICS[m][0] for m in wanted):
            remaining = [m for m in wanted if METRICS[m][0] == statement]
            for source in ("eastmoney", "sina"):
                try:
                    rows, fetched, cached = self._rows(source, code, statement)
                    row = annual_row(rows, source, year, code)
                    found = []
                    for m in remaining:
                        _, em_field, sina_field, name = METRICS[m]
                        field = em_field if source == "eastmoney" else sina_field
                        value = amount(row.get(field)) if row else None
                        if value is None:
                            continue
                        found.append(m)
                        interface = (
                            EM_FUNCTIONS[statement]
                            if source == "eastmoney"
                            else "stock_financial_report_sina"
                        )
                        result["items"].append(
                            dict(
                                metric=m,
                                name=name,
                                value=value,
                                display_amount=display_amount(value),
                                unit="CNY",
                                company_code=code,
                                year=year,
                                report_date=f"{year}-12-31",
                                scope="consolidated",
                                provider="akshare",
                                source=source,
                                interface=interface,
                                source_url=(
                                    f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index?type=web&code={market_symbol(code).lower()}"
                                    if source == "eastmoney"
                                    else f"https://vip.stock.finance.sina.com.cn/corp/go.php/vFD_FinanceSummary/stockid/{code}/displaytype/4.phtml"
                                ),
                                source_field=field,
                                raw_value=row[field],
                                fetched_at=fetched,
                                cached=cached,
                                source_notice_date=row.get(
                                    "NOTICE_DATE"
                                    if source == "eastmoney"
                                    else "公告日期"
                                ),
                                source_update_date=row.get(
                                    "UPDATE_DATE"
                                    if source == "eastmoney"
                                    else "更新日期"
                                ),
                            )
                        )
                    result["attempts"].append(
                        dict(
                            source=source,
                            statement=statement,
                            status="ok"
                            if len(found) == len(remaining)
                            else "partial"
                            if found
                            else "no_data",
                            cached=cached,
                        )
                    )
                    remaining = [m for m in remaining if m not in found]
                except Exception as exc:
                    # Sanitized diagnostics only: HTTP exceptions may contain credentials.
                    result["attempts"].append(
                        dict(
                            source=source,
                            statement=statement,
                            status="error",
                            reason="请求超时"
                            if isinstance(exc, TimeoutError)
                            else "上游失败或数据格式异常",
                        )
                    )
                if not remaining:
                    break
            result["missing"].extend(remaining)
        errors = [a for a in result["attempts"] if a["status"] == "error"]
        if result["items"]:
            result["status"] = "partial" if result["missing"] or unsupported else "ok"
        elif not wanted:
            result["status"] = "unsupported"
        elif errors:
            result["status"] = "error"
        if errors:
            result["warnings"].append("部分财务来源调用失败；无返回不等于未披露")
        return result


def display_amount(value):
    return f"约{Decimal(value) / Decimal(100000000):,.2f}亿元"


def supported_money_mentions(answer, values):
    """Validate every explicit amount, allowing rounding and 'net outflow' wording."""
    answer = answer.replace(",", "").replace("，", "")
    found = False
    for match in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(亿元|万元|元)", answer):
        found = True
        number, unit = match.groups()
        factor = {"亿元": Decimal(10**8), "万元": Decimal(10**4), "元": Decimal(1)}[
            unit
        ]
        parsed = Decimal(number) * factor
        if parsed >= 0 and re.search(
            r"净流出[约为计共：:\s]*$",
            answer[max(0, match.start() - 16) : match.start()],
        ):
            parsed = -parsed
        precision = len(number.split(".")[1]) if "." in number else 0
        tolerance = max(factor / (Decimal(10) ** precision) / 2, Decimal(".01"))
        if not any(abs(parsed - Decimal(v)) <= tolerance for v in values):
            return False
    return found
