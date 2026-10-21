"""CNINFO public web endpoints; not a contracted developer API."""
from datetime import datetime
import re
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import httpx

BASE = "https://www.cninfo.com.cn"
STATIC = "https://static.cninfo.com.cn/"
PDF_PATH = re.compile(r"finalpage/\d{4}-\d{2}-\d{2}/\d+\.pdf", re.I)
TITLE = re.compile(r"(?P<year>\d{4})年?年度报告(?:全文)?(?P<version>[（(][^）)]*[）)])?$")
SHANGHAI = ZoneInfo("Asia/Shanghai")


class SourceError(RuntimeError):
    pass


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", value))


def annual_title(value: str, year: int) -> tuple[str, str | None] | None:
    title = normalize_title(value)
    if any(word in title.lower() for word in ("摘要", "英文", "english", "取消", "撤销", "撤回", "更正公告", "更正说明")):
        return None
    match = TITLE.search(title)
    if not match or int(match.group("year")) != year:
        return None
    return title, match.group("version")


class CninfoProvider:
    def __init__(self, timeout: float = 30.0, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
            headers={"Referer": f"{BASE}/", "User-Agent": "A-Share-Insights/1.0"},
            trust_env=False,
        )
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def _json(self, path: str, data: dict | None = None) -> dict:
        try:
            response = self.client.get(f"{BASE}{path}") if data is None else self.client.post(f"{BASE}{path}", data=data)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("JSON root is not an object")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"巨潮请求或响应失败：{exc}") from exc

    def companies(self) -> list[dict]:
        payload = self._json("/new/data/szse_stock.json")
        rows = payload.get("stockList")
        if not isinstance(rows, list):
            raise SourceError("公司目录缺少 stockList")
        result = []
        for row in rows:
            if not isinstance(row, dict) or row.get("category") != "A股":
                continue
            code, name, org_id = row.get("code"), row.get("zwjc"), row.get("orgId")
            if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code) or not isinstance(name, str) or not isinstance(org_id, str) or not name or not org_id:
                raise SourceError("A 股公司目录字段异常")
            result.append({"code": code, "name": name, "org_id": org_id, "category": "A股"})
        if not result:
            raise SourceError("A 股公司目录为空")
        return result

    def reports(self, company: dict, year: int) -> list[dict]:
        today = datetime.now(SHANGHAI).date()
        found = {}
        for page in range(1, 101):
            payload = self._json("/new/hisAnnouncement/query", {
                "pageNum": page, "pageSize": 30, "column": "szse", "tabName": "fulltext",
                "stock": f'{company["code"]},{company["org_id"]}',
                "category": "category_ndbg_szsh", "isHLtitle": "false",
                "seDate": f"{year}-01-01~{today.isoformat()}",
            })
            if "announcements" not in payload:
                raise SourceError("公告响应缺少 announcements")
            announcements = payload.get("announcements")
            if announcements is None:
                announcements = []
            if not isinstance(announcements, list) or not isinstance(payload.get("hasMore"), bool):
                raise SourceError("公告分页结构异常")
            for row in announcements:
                if not isinstance(row, dict) or row.get("secCode") != company["code"]:
                    continue
                parsed = annual_title(str(row.get("announcementTitle") or ""), year)
                if parsed is None:
                    continue
                adjunct = row.get("adjunctUrl")
                external_id = row.get("announcementId")
                timestamp = row.get("announcementTime")
                if not isinstance(adjunct, str) or not PDF_PATH.fullmatch(adjunct) or not external_id or not isinstance(timestamp, int):
                    raise SourceError("年报公告字段或 PDF 路径异常")
                found[str(external_id)] = {
                    "source": "cninfo", "external_id": str(external_id),
                    "report_year": year, "raw_title": str(row["announcementTitle"]),
                    "title": parsed[0], "version_label": parsed[1],
                    "published_at": datetime.fromtimestamp(timestamp / 1000, SHANGHAI).date(),
                    "pdf_url": urljoin(STATIC, adjunct), "source_payload": row,
                }
            if not payload["hasMore"]:
                return sorted(found.values(), key=lambda item: (item["published_at"], item["external_id"]), reverse=True)
            if not announcements:
                raise SourceError("公告列表为空但仍有下一页")
        raise SourceError("公告超过 100 页，拒绝返回不完整结果")
