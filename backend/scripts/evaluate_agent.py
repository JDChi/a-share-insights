"""Evaluate real prompt-directed agent through the API; never dispatch tools for it."""

import argparse
import hashlib
import html
import json
import time
from decimal import Decimal
from pathlib import Path
from datetime import UTC, datetime
from importlib.metadata import version

from ashare_insights.services.agent import PROMPT

import httpx

from ashare_insights.rag.evaluation import metrics as retrieval_metrics
from ashare_insights.providers.financial import supported_money_mentions


def check(question, result, sha):
    calls = result.get("tool_calls", [])
    reasons = []
    if not calls or calls[0]["tool"] != "query_annual_report":
        reasons.append("未先查询RAG")
    rag = next(
        (c.get("result", {}) for c in calls if c["tool"] == "query_annual_report"), {}
    )
    api = [c for c in calls if c["tool"] == "get_financial_data"]
    category = question["category"]
    if category == "rag":
        if rag.get("status") != "answered" or result.get("status") != "answered" or api:
            reasons.append("RAG正例未直接成功或额外调用API")
        retrieved = rag.get("retrieval", {}).get("items", [])
        if not retrieval_metrics(question, retrieved, sha)["hit_at_5"]:
            reasons.append("RAG未覆盖固定原文证据")
        if not result.get("sources") or any(
            s.get("type") != "pdf" for s in result["sources"]
        ):
            reasons.append("缺少有效PDF引用")
    elif category == "api":
        if rag.get("status") != "insufficient_evidence":
            reasons.append("RAG未返回证据不足（不得人为隐藏证据）")
        matching = [
            c
            for c in api
            if c["arguments"].get("year") == question["year"]
            and question["metric"] in c["arguments"].get("metrics", [])
        ]
        if not matching:
            reasons.append("Agent未发起正确的年度/指标补查")
        expected = question["expected_value"]
        valid_sources = [
            s
            for s in result.get("sources", [])
            if s.get("type") == "api"
            and s.get("metric") == question["metric"]
            and s.get("year") == question["year"]
            and Decimal(s["value"]) == Decimal(expected)
        ]
        if not valid_sources or result.get("status") != "answered":
            reasons.append("API答案或来源与冻结参考不一致")
        if not supported_money_mentions(result.get("answer", ""), [expected]):
            reasons.append("答案金额/单位不匹配冻结参考")
    else:
        if result.get("status") != "insufficient_evidence" or result.get("sources"):
            reasons.append("无数据题未明确拒答或生成了结论来源")
        if question["api_expected"] and not any(
            c["arguments"].get("year") == question["year"]
            and question["metric"] in c["arguments"].get("metrics", [])
            and c.get("result", {}).get("status") == "no_data"
            for c in api
        ):
            reasons.append("适用指标未查API或API未确认无数据")
        if not question["api_expected"] and api:
            reasons.append("对不支持的问题调用了不适用的财务工具")
    if any(c.get("status") == "error" for c in calls):
        reasons.append("存在工具故障，不能记为无数据成功")
    return dict(passed=not reasons, failures=reasons)


def write_report(output, payload):
    payload["summary"] = {
        "completed_questions": len(payload["results"]),
        "flow_evidence_passed": sum(r["passed"] for r in payload["results"]),
        "by_category": {
            category: {
                "completed": sum(r["category"] == category for r in payload["results"]),
                "passed": sum(
                    r["category"] == category and r["passed"]
                    for r in payload["results"]
                ),
            }
            for category in ("rag", "api", "unavailable")
        },
        "semantic_review": "pending_human_review",
    }
    (output / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)
    )
    lines = [
        "# Agent 20题验证",
        "",
        f"数据集SHA256：`{payload['fixture_sha256']}`",
        "",
        "检查实际调用顺序、工具状态、冻结参考值和引用；不等于最终答案语义正确的人工验收。",
        "",
        f"本次已完成{len(payload['results'])}题，流程与证据检查通过{payload['summary']['flow_evidence_passed']}题。",
        "",
        "|类别|已完成|通过|",
        "|---|---|---|",
        *[
            f"|{c}|{v['completed']}|{v['passed']}|"
            for c, v in payload["summary"]["by_category"].items()
        ],
        "",
        "|题号|类别|通过|耗时|失败原因|",
        "|---|---|---|---|---|",
    ]
    for item in payload["results"]:
        lines.append(
            f"|{item['id']}|{item['category']}|{item['passed']}|{item['seconds']}|{'; '.join(item['failures'])}|"
        )
    lines += ["", "## 逐题答案与调用", ""]
    for item in payload["results"]:
        lines += [
            f"### {item['id']} {item['question']}",
            "",
            item["response"].get("answer", "请求失败"),
            "",
            "```json",
            json.dumps(item["response"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    (output / "REPORT.md").write_text("\n".join(lines))
    (output / "report.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Agent evaluation</title><style>body{font:15px system-ui;max-width:1100px;margin:32px auto}pre{white-space:pre-wrap}</style><pre>'
        + html.escape("\n".join(lines))
        + "</pre>"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8001")
    parser.add_argument("--report-id", required=True, type=int)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "evaluations/agent-maotai-2024.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture_bytes = args.fixture.read_bytes()
    fixture = json.loads(fixture_bytes)
    if args.output.exists():
        raise SystemExit("Output must not exist")
    if any(
        q["category"] == "api" and "expected_value" not in q
        for q in fixture["questions"]
    ):
        raise SystemExit(
            "Freeze independent API reference values before running agent evaluation"
        )
    args.output.mkdir(parents=True)
    payload = dict(
        run_at=datetime.now(UTC).isoformat(),
        prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),
        versions={
            name: version(name)
            for name in ("akshare", "llama-index-core", "llama-index-llms-openai-like")
        },
        fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
        api_url=args.api_url,
        report_id=args.report_id,
        results=[],
    )
    with httpx.Client(base_url=args.api_url, trust_env=False, timeout=260) as client:
        for question in fixture["questions"]:
            start = time.monotonic()
            try:
                response = client.post(
                    f"/api/v1/reports/{args.report_id}/agent/ask",
                    json=dict(question=question["question"]),
                )
                response.raise_for_status()
                result = response.json()
                checked = check(question, result, fixture["source_sha256"])
            except Exception as exc:
                result, checked = (
                    dict(status="error"),
                    dict(passed=False, failures=[type(exc).__name__]),
                )
            item = dict(
                **question,
                response=result,
                seconds=round(time.monotonic() - start, 2),
                **checked,
            )
            payload["results"].append(item)
            write_report(args.output, payload)
            print(question["id"], checked, "seconds=", item["seconds"], flush=True)
    print(
        "Passed",
        sum(r["passed"] for r in payload["results"]),
        "/",
        len(payload["results"]),
        flush=True,
    )


if __name__ == "__main__":
    main()
