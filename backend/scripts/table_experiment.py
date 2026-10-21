# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdf==6.10.0", "pdfplumber==0.11.9", "pypdfium2==5.14.0"]
# ///
"""Offline, sample-specific PDF table experiment. Not a production parser.

uv run --script backend/scripts/table_experiment.py
All generated files are written under ignored data/table-experiment/.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform

import pdfplumber
import pypdfium2 as pdfium
from pypdf import PdfReader
from table_checks import check_cell

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("table_samples.json")
METHODS = {
    "grid": {},
    "text": {"vertical_strategy": "text", "horizontal_strategy": "text"},
    "tuned": None,
}


def md_cell(value):
    if value is None:
        return "⟪合并覆盖/无独立单元格⟫"
    return value.replace("|", "\\|").replace("\n", "<br>")


def table_markdown(rows, columns):
    width = max((len(row) for row in rows), default=len(columns))
    labels = columns if len(columns) == width else [f"原始列 {i + 1}" for i in range(width)]
    lines = ["| " + " | ".join(labels) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(md_cell(c) for c in row) + " |")
    return "\n".join(lines)


def extract(crop, settings):
    tables = crop.find_tables(settings)
    return [{"bbox": list(t.bbox), "rows": t.extract(x_tolerance=2, y_tolerance=3)} for t in tables]


def run(pdf_dir, output, fixture):
    spec = json.loads(fixture.read_text())
    output.mkdir(parents=True, exist_ok=True)
    versions = {n: importlib.metadata.version(n) for n in ["pypdf", "pdfplumber", "pypdfium2"]}
    results = {"versions": versions, "python": platform.python_version(),
               "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
               "samples": [], "sources": {},
               "comparison": "cell checks apply only to pdfplumber; pypdf outputs are text baselines",
               "annotation_policy": spec["annotation_policy"]}
    for source_id, source in spec["sources"].items():
        path = pdf_dir / (source["sha256"] + ".pdf")
        if not path.is_file():
            raise FileNotFoundError(f"Existing local PDF required: {path}; no automatic download")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise ValueError(f"PDF checksum mismatch: {path}")
        results["sources"][source_id] = {**source, "path": str(path)}
        reader = PdfReader(path)
        render_doc = pdfium.PdfDocument(str(path))
        with pdfplumber.open(path) as doc:
            pages = sorted({s["page"] for s in spec["samples"] if s["source"] == source_id})
            for page_number in pages:
                page_dir = output / f"{source_id}-{page_number}"
                page_dir.mkdir(exist_ok=True)
                render_page = render_doc[page_number - 1]
                bitmap = render_page.render(scale=2)
                bitmap.to_pil().save(page_dir / "page.png")
                bitmap.close()
                render_page.close()
                pdf_page = reader.pages[page_number - 1]
                for name, kwargs in [("pypdf-plain", {}), ("pypdf-layout", {"extraction_mode": "layout"})]:
                    (page_dir / f"{name}.txt").write_text(pdf_page.extract_text(**kwargs) or "")
                page = doc.pages[page_number - 1]
                for sample in [s for s in spec["samples"] if s["source"] == source_id and s["page"] == page_number]:
                    crop = page.crop(sample["bbox"])
                    item = {**sample, "methods": {}, "manual_configuration": True}
                    for method, settings in METHODS.items():
                        if method == "tuned":
                            settings = sample["tuned"]
                        tables = extract(crop, settings)
                        checks = [check_cell(tables, a) for a in sample["checks"]]
                        item["methods"][method] = {"settings": settings, "tables": tables,
                            "checks": checks, "passed": sum(c["passed"] for c in checks), "total": len(checks)}
                        stem = page_dir / f'{sample["id"]}-{method}'
                        stem.with_suffix(".json").write_text(json.dumps(item["methods"][method], ensure_ascii=False, indent=2))
                        lines = [f'# {sample["id"]} · {method}', f'PDF 物理页：{page_number}',
                                 f'本页表名：{sample["title"] or "缺失，不自动继承"}',
                                 f'本页单位：{sample["unit"] or "缺失，不自动继承"}',
                                 "列名来源：人工对照图像配置；不是自动表头识别的评测结果。",
                                 "原始表头行保留在下表中，未自动合并跨行/跨页内容。",
                                 "空字符串表示原页单元格未提取到文本；合并覆盖以专用标记显示；横线不转零。"]
                        for table in tables:
                            rows = [r for r in table["rows"] if any(c not in ("", None) for c in r)]
                            lines += ["", table_markdown(rows, sample["columns"])]
                        if not tables:
                            lines += ["", "未识别到表格。"]
                        stem.with_suffix(".md").write_text("\n\n".join(lines) + "\n")
                    results["samples"].append(item)
        render_doc.close()
    results["totals"] = {m: {"passed": sum(s["methods"][m]["passed"] for s in results["samples"]),
                             "total": sum(s["methods"][m]["total"] for s in results["samples"])} for m in METHODS}
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    write_report(output, results)
    print(json.dumps(results["totals"], ensure_ascii=False))
    print(f"Report: {output / 'REPORT.md'}")


def write_report(output, results):
    lines = ["# 年报表格解析实验", "## 结论与边界",
        "网格表格适合使用线框模式；无完整网格表格需要显式列边界。调优配置来自人工看图，不代表自动识别能力。",
        "多行行名、跨行附注和跨列父表头仍可能被拆散；不自动拼接，不自动合并续表。局部单元格通过不能证明整表正确。",
        "原始 JSON 保留 null 与空字符串；Markdown 区分合并覆盖单元格，横线和括号负数保持原样。",
        "pypdf 两种结果仅用于文字/阅读顺序对照，不把没有表格结构的文本计作单元格解析通过。",
        "pypdf 普通文字在本样本中能保留多数字符和数字，但没有单元格语义；保留布局模式更方便人工核对列位置，仍不会重建多层表头或多行项目归属。美的158页两种模式都保留了被换行的项目名，不能直接视为结构化行。",
        "当前可用性：茅台样本网格表格的被检单元格可用；美的156/157页在人工区域和列边界配置下，被检单元格可用；美的158/161页仍有跨行内容归属错误，不适合作为可靠结构化表格直接接入。",
        "跨列父表头在原始输出中仍被拆散。Markdown完整列名来自人工模板，说明其目标表达形式，不能宣称已实现自动表头展开。无自动续表合并，续页缺失信息不补造。",
        "## 固定样本检查", "对人工看图标注的单元格逐项比较，仅忽略排版空白，不忽略数字、符号和列位置。",
        "| 区域 | 网格 | 文本对齐 | 显式列调优 |", "| --- | --- | --- | --- |"]
    for s in results["samples"]:
        values = [f'{s["methods"][m]["passed"]}/{s["methods"][m]["total"]}' for m in METHODS]
        lines.append(f'| {s["id"]} | ' + " | ".join(values) + " |")
    lines += ["", "总计：" + "; ".join(f'{m}: {v["passed"]}/{v["total"]}' for m,v in results["totals"].items()),
              "## 页面与输出", "调优区域 bbox、显式列线和容差全部保存在 results.json；六页是固定验证样本，不是全量年报评测。"]
    for s in results["samples"]:
        folder = f'{s["source"]}-{s["page"]}'
        lines += [f'### {s["id"]}', f'[原页截图]({folder}/page.png) · [普通文字]({folder}/pypdf-plain.txt) · [保留布局]({folder}/pypdf-layout.txt)',
                  f'本页表名：{s["title"] or "缺失"}；单位：{s["unit"] or "缺失"}；表头：{s["header_status"]}。',
                  f'注意：{s["notes"]}',
                  " · ".join(f'[{m}]({folder}/{s["id"]}-{m}.md)' for m in METHODS)]
    lines += ["## 错误清单", "完整核验项（包括通过项）在 results.json。以下列出所有失败项："]
    failures = []
    for s in results["samples"]:
        for m, data in s["methods"].items():
            for c in data["checks"]:
                if not c["passed"]:
                    failures.append({"sample": s["id"], "method": m, **c})
                    lines.append(f'- {s["id"]}/{m}：{c["row"]}，列 {c["column"] + 1}；应为 `{c["expected"]}`，实际 `{c["actual"]}`（{c["reason"]}）。')
    (output / "errors.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2))
    # Keep Markdown table rows and list items contiguous, with paragraph separation.
    blocks = []
    group = []
    group_kind = None
    for line in lines:
        kind = "table" if line.startswith("|") else "list" if line.startswith("- ") else None
        if group and (kind is None or kind != group_kind):
            blocks.append("\n".join(group))
            group = []
        if kind:
            group.append(line)
            group_kind = kind
        elif line:
            blocks.append(line)
    if group:
        blocks.append("\n".join(group))
    (output / "REPORT.md").write_text("\n\n".join(blocks) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "data/pdfs")
    parser.add_argument("--output", type=Path, default=ROOT / "data/table-experiment")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    args = parser.parse_args()
    run(args.pdf_dir.resolve(), args.output.resolve(), args.fixture)
