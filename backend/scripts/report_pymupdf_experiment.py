"""Summarize exported Markdown, native JSON and unchanged sample baselines."""

import hashlib
import json
from pathlib import Path
import sys

from pymupdf_checks import match_table
from table_checks import compact

ROOT = Path(__file__).resolve().parents[2]


def main(output):
    manifest = json.loads((output / "run.json").read_text())
    runs = {}
    for entry in manifest["runs"]:
        path = output / entry["folder"] / "results.json"
        runs[entry["folder"]] = (
            json.loads(path.read_text())
            if path.is_file()
            else {
                "status": "failed",
                "error": "Worker did not produce results",
                "samples": [],
                "pages": [],
            }
        )
    fixture_path = Path(__file__).with_name("table_samples.json")
    spec = json.loads(fixture_path.read_text())
    digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    baseline_path = ROOT / "data/table-experiment/results.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    docling = {}
    for mode in ["true", "false"]:
        path = ROOT / f"data/docling-experiment/cell-matching-{mode}/results.json"
        if path.exists():
            docling[mode] = json.loads(path.read_text())
    for result in [
        *runs.values(),
        *docling.values(),
        *([baseline] if baseline else []),
    ]:
        if result.get("fixture_sha256") and result["fixture_sha256"] != digest:
            raise ValueError("Cannot compare different annotation versions")
    consistency = []
    for folder, result in runs.items():
        if not folder.startswith("samples-") or result.get("mode") != "layout":
            continue
        for page in result["pages"]:
            if page["status"] != "completed":
                continue
            page_dir = output / folder / page["source"] / f"page-{page['page']:03}"
            md_tables = json.loads((page_dir / "tables-from-markdown.json").read_text())
            native = json.loads((page_dir / "native-tables.json").read_text())
            for index, table in enumerate(md_tables):
                selected, matched = (
                    match_table(native, table["bbox"])
                    if table.get("bbox")
                    else ([], {"status": "unmatched"})
                )
                differences = []
                if selected:
                    actual = selected[0]["rows"]
                    for row in range(max(len(actual), len(table["rows"]))):
                        left = actual[row] if row < len(actual) else []
                        right = table["rows"][row] if row < len(table["rows"]) else []
                        for column in range(max(len(left), len(right))):
                            a = left[column] if column < len(left) else None
                            b = right[column] if column < len(right) else None
                            if compact(a) != compact(b):
                                differences.append(
                                    {
                                        "row": row,
                                        "column": column,
                                        "native_json": a,
                                        "markdown": b,
                                    }
                                )
                consistency.append(
                    {
                        "source": page["source"],
                        "page": page["page"],
                        "table": index,
                        "matching": matched,
                        "differences": differences,
                    }
                )
    (output / "markdown-json-consistency.json").write_text(
        json.dumps(consistency, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# PyMuPDF 2.0 年报转换实验",
        "",
        "## 条件与评分边界",
        "",
        "使用已有茅台、美的PDF；六页、七个区域、68项固定检查。完整年报分别143和295页。",
        "自动版面模式、规则lines_strict、规则text均解析整页，不输入人工区域、列线或表头模板；区域只在转换后按IoU≥0.2匹配。",
        "主分数检查实际Markdown中的行列与数值；layout另对原生JSON单元格独立评分。Markdown空白与JSON合并覆盖不能互相猜测替换。",
        "规则模式不支持原生to_json，保留实际Markdown导出元数据JSON并标记该能力不可用；没有换用另一检测器伪造JSON正确率。",
        "关闭OCR与图片导出；Python socket连接被拦截，ONNX遥测关闭。没有远程LLM；未接入RAG、数据库或续表合并。",
        "pdfplumber基线使用人工区域，tuned还使用人工列边界；自动化程度与本次不同。分数只覆盖标注项，不等于整表或整份报告正确率。",
        "",
        "## 六页严格检查",
        "",
        "| 区域 | pdfplumber grid/text/tuned | Docling JSON 开/关 | layout MD / JSON | lines_strict MD | text MD |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sample in spec["samples"]:
        sid = sample["id"]
        cell = lambda r: next((s for s in r.get("samples", []) if s["id"] == sid), None)
        base = cell(baseline) if baseline else None
        base_text = (
            " / ".join(
                str(base["methods"][m]["passed"]) for m in ["grid", "text", "tuned"]
            )
            if base
            else "未运行"
        )
        doc_text = " / ".join(
            str(cell(docling[m])["passed"])
            if m in docling and cell(docling[m])
            else "未运行"
            for m in ["true", "false"]
        )
        vals = []
        for mode in ["layout", "lines_strict", "text"]:
            row = cell(runs.get("samples-" + mode, {}))
            vals.append(
                (str(row["passed"]) + "/" + str(len(sample["checks"])))
                if row
                else "未完成"
            )
            if mode == "layout" and row:
                vals[-1] += (
                    " / "
                    + str(row.get("native_json_passed"))
                    + "/"
                    + str(len(sample["checks"]))
                )
        lines.append(f"| {sid} | {base_text} | {doc_text} | " + " | ".join(vals) + " |")
    totals = []
    for mode in ["layout", "lines_strict", "text"]:
        run = runs.get("samples-" + mode, {})
        totals.append(
            f"{mode}: {run.get('passed', '未完成')}/{run.get('total', '未完成')}"
        )
    lines += [
        "",
        "Markdown总计：" + "；".join(totals) + "。",
        "自动版面MD与原生JSON的逐格表示对照见 [一致性明细](markdown-json-consistency.json)；一致不代表两者都正确。",
    ]
    lines += [
        "",
        "## 资源与完整性",
        "",
        "| 运行 | 状态 | 页完成/失败 | MD检查 | 墙钟秒 | MD秒 / JSON秒 | 峰值RSS MiB | Python网络尝试 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for folder, r in runs.items():
        md = sum(d.get("markdown_seconds", 0) for d in r.get("documents", []))
        js = sum(d.get("json_seconds", 0) for d in r.get("documents", []))
        completed = sum(p["status"] == "completed" for p in r["pages"])
        failed = len(r["pages"]) - completed
        lines.append(
            f"| {folder} | {r['status']} | {completed}/{failed} | {r.get('passed', '—')}/{r.get('total', '—')} | {r.get('seconds', 0):.2f} | {md:.2f} / {js:.2f} | {r.get('peak_rss_mib', 0):.1f} | {r.get('blocked_network_attempts', '—')} |"
        )
        if r.get("error"):
            lines.append(f"\n错误：{r['error']}\n")
    lines += [
        "",
        "每组为独立进程、串行运行；墙钟包含导入、模型加载、输出与核验，不能当作纯解析耗时。MD和JSON分别调用，分别计时。完整转换按原文顺序逐页导出，保留全局标题识别上下文。",
        "",
        "## 全文页面诊断",
        "",
        "字符保留诊断只比较pypdf文字与MD的字母数字字符多重集，忽略顺序；仅用于筛查，不能证明阅读顺序、表格或正文正确。重复页面也只是复核线索。",
    ]
    for folder, r in runs.items():
        if not folder.startswith("full-"):
            continue
        for document in r.get("documents", []):
            pages = [
                p
                for p in r["pages"]
                if p["source"] == document["source"] and p["status"] == "completed"
            ]
            empty = [p["page"] for p in pages if p["empty"]]
            replacement = [p["page"] for p in pages if p["replacement_characters"]]
            low = [
                p["page"]
                for p in pages
                if p["pypdf_character_retention_diagnostic"] is not None
                and p["pypdf_character_retention_diagnostic"] < 0.95
            ]
            lines += [
                "",
                f"### {folder}",
                "",
                f"- 应处理{document['expected_pages']}页，成功{len(document['completed_pages'])}页，失败{document['failed_pages']}。",
                f"- 每页平均MD转换{document['markdown_seconds'] / max(1, len(document['completed_pages'])):.4f}秒；JSON阶段{document['json_seconds'] / max(1, len(document['completed_pages'])):.4f}秒（规则模式仅为不支持标记的耗时）。",
                f"- 空输出页：{empty}；含替换字符页：{replacement}；字符保留诊断低于95%的页：{low}。",
                f"- 完全相同非空页面组：{document.get('identical_page_groups', [])}；产物{document.get('output_bytes', 0)} bytes。",
                f"- [完整MD]({folder}/{document['source']}/document.md) · [页码映射]({folder}/{document['source']}/page-map.json) · [逐页诊断]({folder}/results.json)。",
            ]
    lines += [
        "",
        "## 失败单元格与原始输出",
        "",
        "以下主分数来自实际Markdown；原生JSON失败另列。仅忽略排版空白，null、空字符串、横线、括号和列归属保持原样。",
    ]
    for folder, r in runs.items():
        if not folder.startswith("samples-"):
            continue
        lines += [
            "",
            f"### {folder}",
            "",
            f"[完整结果]({folder}/results.json) · [失败清单]({folder}/errors.json)",
            "",
        ]
        for s in r["samples"]:
            for c in s["checks"]:
                if not c["passed"]:
                    lines.append(
                        f"- {s['id']} · {c['row']} · 列{c['column'] + 1}：期望`{c['expected']}`，实际`{c['actual']}`，{c['reason']}。"
                    )
            for c in s.get("native_json_checks") or []:
                if not c["passed"]:
                    lines.append(
                        f"- JSON：{s['id']} · {c['row']} · 列{c['column'] + 1}：期望`{c['expected']}`，实际`{c['actual']}`。"
                    )
        for p in r["pages"]:
            if p["status"] == "completed":
                prefix = f"{folder}/{p['source']}/page-{p['page']:03}"
                lines.append(
                    f"- {p['source']}物理页{p['page']}：[原页]({prefix}/page.png) · [原生MD]({prefix}/document.md) · [原生JSON]({prefix}/document.json) · [MD单元格]({prefix}/tables-from-markdown.json)。"
                )
    lines += [
        "",
        "## 复核与结论",
        "",
        "开发助手原页对照、错误归因和接入建议见同目录 REVIEW.md；没有此文件时表示尚未完成原页复核。",
        "",
        "## 可复现信息",
        "",
        f"固定标注SHA-256：`{digest}`。每组results.json保留全部包版本、资源文件校验值、实际配置与耗时；输入PDF以SHA校验。",
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines))
    (output / "summary.json").write_text(
        json.dumps(
            {
                "fixture_sha256": digest,
                "runs": {
                    k: {
                        f: r.get(f)
                        for f in [
                            "status",
                            "passed",
                            "total",
                            "seconds",
                            "peak_rss_mib",
                            "versions",
                            "blocked_network_attempts",
                        ]
                    }
                    for k, r in runs.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(output / "REPORT.md")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
