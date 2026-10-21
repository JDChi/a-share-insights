"""Summarize both Docling runs against the unchanged 68-cell fixture and baselines."""
import hashlib
import json
from pathlib import Path
import unicodedata

from table_checks import check_cell

ROOT = Path(__file__).resolve().parents[2]


def label_form(value):
    if value is None:
        return None
    return unicodedata.normalize("NFKC", value).translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))


def format_review(output, runs, fixture):
    """Diagnostic only: normalize label typography, never numeric cells or blank/null."""
    reviews = {}
    for mode, result in runs.items():
        samples = []
        for s in result["samples"]:
            raw = json.loads((output / f'cell-matching-{mode}/{s["source"]}-{s["page"]}/tables.json').read_text())
            selected = [t for t in raw if t["table_index"] == s["matched_table"]]
            normalized = [{"rows": [[label_form(row[0]), *row[1:]] for row in t["rows"]]} for t in selected]
            target = next(t for t in fixture["samples"] if t["id"] == s["id"])
            checks = []
            for c in target["checks"]:
                checked = check_cell(normalized, {**c, "row": label_form(c["row"])})
                checks.append({**checked, "row": c["row"]})
            samples.append({"id": s["id"], "checks": checks, "passed": sum(c["passed"] for c in checks), "total": len(checks)})
        reviews[mode] = {"samples": samples, "passed": sum(s["passed"] for s in samples), "total": sum(s["total"] for s in samples)}
    (output / "format-review.json").write_text(json.dumps({"policy": "NFKC and curly/straight quotes in row labels only; numeric/content cells, blank/null and column positions unchanged; original 68 annotations and strict scores unchanged", "runs": reviews}, ensure_ascii=False, indent=2) + "\n")
    return reviews


def main():
    base = ROOT / "data/table-experiment"
    output = ROOT / "data/docling-experiment"
    baseline = json.loads((base / "results.json").read_text())
    runs = {mode: json.loads((output / f"cell-matching-{mode}/results.json").read_text()) for mode in ["true","false"]}
    fixture_hash = hashlib.sha256(Path(__file__).with_name("table_samples.json").read_bytes()).hexdigest()
    assert baseline["fixture_sha256"] == fixture_hash
    for r in runs.values():
        assert r["fixture_sha256"] == fixture_hash
        assert r["total"] == 68 and len(r["pages"]) == 6
    fixture = json.loads(Path(__file__).with_name("table_samples.json").read_text())
    review = format_review(output, runs, fixture)
    lines = ["# Docling 六页表格解析实测", "",
             "## 测试条件", "",
             "使用现有茅台、美的2024年报，六个物理页、七个表格区域，同一份68项人工标注。",
             "Docling解析完整单页，不传入人工裁剪区域、列线或表头模板。标注区域只在解析后用于匹配待核验表格（IoU≥0.2）。",
             "关闭OCR、代码/公式/图片增强和远端服务；CPU四线程。分别运行单元格匹配开启/关闭两个进程；推理时阻止网络连接。",
             "pdfplumber三个基线有人工表格区域；调优基线还使用人工列边界，不是同等自动化程度的比较。", "",
             "## 同一68项检查", "",
             "| 区域 | 网格基线 | 文本基线 | 人工调优基线 | Docling 匹配开 | Docling 匹配关 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for s in baseline["samples"]:
        vals = [f'{s["methods"][m]["passed"]}/{s["methods"][m]["total"]}' for m in ["grid","text","tuned"]]
        for r in runs.values():
            row = next(x for x in r["samples"] if x["id"] == s["id"])
            vals.append(f'{row["passed"]}/{row["total"]}')
        lines.append(f'| {s["id"]} | ' + " | ".join(vals) + " |")
    lines += ["", "## 运行资源", "", "| 单元格匹配 | 通过 | 六页耗时（含首次模型加载） | 峰值进程RSS | 被阻止的网络尝试 |", "| --- | --- | --- | --- | --- |"]
    for mode,r in runs.items():
        lines.append(f'| {mode} | {r["passed"]}/{r["total"]} | {r["seconds"]}秒 | {r["peak_rss_mib"]} MiB | {r["blocked_network_attempts"]} |')
    lines += ["", "这些是本机串行小样测量，不能外推整份年报耗时或不同机器性能。模型总文件大小：" + str(sum(x["size_bytes"] for x in next(iter(runs.values()))["model_manifest"])) + " bytes。", "",
              "## 格式差异辅助复核", "",
              "严格分数包含行名引号、全半角括号变化造成的失败。另做一次仅规范行名排版符号的辅助复核：NFKC和弯/直引号映射，不更改数值、列位置、空白或null，也不修改原始标注。该分数不替代严格结果。",
              "；".join(f'{mode}: {v["passed"]}/{v["total"]}' for mode,v in review.items()) + "。详见 [辅助复核明细](format-review.json)。", "",
              "## 人工复核结论", "",
              "1. 自动识别区域基本覆盖七个目标区域，但找到表格不等于正确还原行列。开启单元格匹配的表现优于关闭。",
              "2. 开启匹配后，美的157页的12项检查全部通过；158页的跨行营业收入附注、三行联营投资收益项目和换行公允价值项目得到改善。161页的跨列父表头及子列关系也被识别，自动Markdown展开为“归属于母公司股东权益 - 股本”等完整列名，不使用此前人工表头模板。",
              "3. 仍有真实结构问题：茅台63页表尾将4列压为3列（空附注列消失，不能直接套用原列号）；美的156页把货币资金与交易性金融资产合成一行；158页表头未纳入表格区域，归母净利润行名仍拆开；161页股份支付项目仍被拆成两行。",
              "4. 空白独立单元格在Docling输出中常表示为null，和pdfplumber空字符串的约定不同；此差异已保留在严格得分中，未据此宣称金额错误。原始跨度字段可用于进一步区分缺失与合并覆盖。",
              "5. 结论：Docling有自动定位、部分多行内容恢复与跨度识别的价值，但本六页测试未证明它能直接替代人工配置的pdfplumber。当前不能作为通用、无校验的年报表格解析器。后续需补行重建、表头关联和独立样本验证；不推荐仅凭本次结果自动串联兜底。", "",
              "## 原始结果与错误", "",
              "每次运行保留完整DoclingDocument JSON/Markdown、原始单元格、跨度和表头标记。未替换成手工正确值，未猜测拼接跨页表。", "",
              "空白、横线、零与合并覆盖不互换；只忽略排版空白。合并单元格仅在起始位置放文本，其余位置为null。引用页码为PDF物理页，未换成印刷页码。"]
    for mode, r in runs.items():
        folder = f"cell-matching-{mode}"
        lines += ["", f"### 匹配 {mode}", "", f'[完整结果]({folder}/results.json) · [失败清单]({folder}/errors.json)', ""]
        for p in r["pages"]:
            page = f'{p["source"]}-{p["page"]}'
            lines.append(f'- {page}：{p["table_count"]}张表，{p["seconds"]}秒；[原页](../table-experiment/{page}/page.png) · [自动识别区域]({folder}/{page}/detected-tables.png) · [解析Markdown]({folder}/{page}/document.md) · [表格结构]({folder}/{page}/tables.json)')
        lines += [""]
        for s in r["samples"]:
            for c in s["checks"]:
                if not c["passed"]:
                    lines.append(f'- {s["id"]}：{c["row"]}，列{c["column"]+1}，预期 `{c["expected"]}`，实际 `{c["actual"]}`（{c["reason"]}）。')
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(output / "REPORT.md")


if __name__ == "__main__":
    main()
