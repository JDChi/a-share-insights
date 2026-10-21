"""Evidence-span retrieval evaluation; no LLM needed for primary metrics."""

import asyncio
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import time
from datetime import UTC, datetime
from html import escape

from sqlalchemy import select

from ashare_insights.db.models import Announcement, IndexGeneration
from ashare_insights.rag.text import PARSER_VERSION


def covers(intervals, start, end):
    cursor = start
    for a, b in sorted(intervals):
        if b <= cursor:
            continue
        if a > cursor:
            return False
        cursor = max(cursor, b)
        if cursor >= end:
            return True
    return False


def metrics(question, items, source_sha):
    valid = [i for i in items[:5] if i["file_sha256"] == source_sha]
    groups = question["evidence_groups"]

    def group_hit(group, subset):
        return any(
            covers([(i["start"], i["end"]) for i in subset], a["start"], a["end"])
            for a in group["alternatives"]
        )

    def single_hit(item):
        return any(group_hit(g, [item]) for g in groups)

    ranks = [
        n for n, item in enumerate(items[:5], 1) if item in valid and single_hit(item)
    ]
    covered = [group_hit(g, valid) for g in groups]
    return dict(
        hit_at_1=bool(ranks and ranks[0] == 1),
        hit_at_5=bool(ranks),
        evidence_recall_at_5=sum(covered) / len(groups),
        mrr_at_5=1 / ranks[0] if ranks else 0,
        first_evidence_rank=ranks[0] if ranks else None,
        covered_groups=covered,
    )


async def ragas_scores(question, items, settings):
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    model = settings.eval_model or settings.llm_model
    key = settings.eval_api_key or settings.llm_api_key
    url = settings.eval_base_url or settings.llm_base_url
    if not model or not key:
        return dict(
            status="not_configured",
            model=model or None,
            scores={},
            errors=["未配置评测模型/密钥"],
        )
    from llama_index.llms.openai_like import OpenAILike
    from ragas import SingleTurnSample
    from ragas.llms import LlamaIndexLLMWrapper
    from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall

    evaluator = LlamaIndexLLMWrapper(
        OpenAILike(
            model=model,
            api_key=key,
            api_base=url,
            is_chat_model=True,
            is_function_calling_model=False,
            context_window=32000,
            temperature=0.1,
            max_tokens=3000,
            timeout=settings.llm_timeout,
            max_retries=1,
        )
    )
    sample = SingleTurnSample(
        user_input=question["question"],
        reference=question["reference"],
        retrieved_contexts=[i["text"] for i in items],
    )
    scores, errors = {}, []
    start = time.perf_counter()
    for name, metric in [
        ("context_recall", LLMContextRecall(llm=evaluator)),
        ("context_precision", LLMContextPrecisionWithReference(llm=evaluator)),
    ]:
        try:
            value = float(
                await asyncio.wait_for(metric.single_turn_ascore(sample), timeout=180)
            )
            if not math.isfinite(value):
                raise ValueError("nonfinite")
            scores[name] = value
        except Exception as exc:
            # Exception text may contain raw requests/responses; retain only the type.
            errors.append(f"{name}: {type(exc).__name__}")
    return dict(
        status="completed" if not errors else "partial" if scores else "failed",
        model=model,
        base_url=url,
        scores=scores,
        errors=errors,
        seconds=time.perf_counter() - start,
        usage=None,
        implementation=["LLMContextRecall", "LLMContextPrecisionWithReference"],
    )


def evaluate(service, report_id, fixture_path, output, with_ragas=False):
    fixture = json.loads(fixture_path.read_text())
    with service.sessions() as db:
        report = db.get(Announcement, report_id)
        if report is None or not report.file:
            raise ValueError("评测年报或文件不存在")
        if report.file.sha256 != fixture["source_sha256"]:
            raise ValueError("评测集与PDF SHA不一致")
        gen = db.scalar(
            select(IndexGeneration).where(
                IndexGeneration.file_id == report.file_id,
                IndexGeneration.pipeline_key == service.indexer.pipeline_key,
                IndexGeneration.active.is_(True),
            )
        )
        if gen is None:
            raise ValueError("请先建立当前配置索引")
        canonical = gen.canonical_text
        if (
            fixture["parser_version"] != PARSER_VERSION
            or hashlib.sha256(canonical.encode()).hexdigest()
            != fixture["canonical_sha256"]
        ):
            raise ValueError("解析文本/版本与标注不一致，禁止错位评分")
        for q in fixture["questions"]:
            for group in q["evidence_groups"]:
                for a in group["alternatives"]:
                    if canonical[a["start"] : a["end"]] != a["quote"]:
                        raise ValueError("标注原文与位置不一致")
        generation_id = gen.id
    output.mkdir(parents=True, exist_ok=False)
    result = dict(
        created_at=datetime.now(UTC).isoformat(),
        fixture_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        fixture_version=fixture["version"],
        source_sha256=fixture["source_sha256"],
        report_id=report_id,
        generation_id=generation_id,
        pipeline=service.indexer.pipeline,
        embedding=service.provider.descriptor,
        versions={},
        questions=[],
        manual_review="待人工复核；报告的确定性评分不是人工确认",
        limitations="固定回归集；不代表未知问题泛化；不评估表格结构理解。Hit要求单块完整覆盖，Recall允许前5块联合覆盖。",
    )
    for package in [
        "llama-index-core",
        "sentence-transformers",
        "torch",
        "pypdf",
        "pgvector",
        "ragas",
    ]:
        try:
            result["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["versions"][package] = None
    for q in fixture["questions"]:
        t = time.perf_counter()
        retrieval = service.retrieve(report_id, q["question"])
        if retrieval["generation_id"] != generation_id:
            raise ValueError("评测期间有效索引改变，请重新运行")
        entry = {
            **q,
            "retrieval": retrieval,
            "metrics": metrics(q, retrieval["items"], fixture["source_sha256"]),
            "retrieval_seconds": time.perf_counter() - t,
            "ragas": dict(status="not_run", scores={}, errors=[]),
        }
        result["questions"].append(entry)
        print(
            f"{q['id']}: hit@5={entry['metrics']['hit_at_5']}, evidence={entry['metrics']['evidence_recall_at_5']}",
            flush=True,
        )
    write_outputs(result, output)
    if with_ragas:

        async def run_all():
            sem = asyncio.Semaphore(2)

            async def score(q):
                async with sem:
                    try:
                        q["ragas"] = await ragas_scores(
                            q, q["retrieval"]["items"], service.settings
                        )
                    except Exception as exc:
                        q["ragas"] = dict(
                            status="failed", scores={}, errors=[type(exc).__name__]
                        )
                    write_outputs(result, output)

            await asyncio.gather(*(score(q) for q in result["questions"]))

        asyncio.run(run_all())
    write_outputs(result, output)
    return result


def write_outputs(result, output):
    questions = result["questions"]
    n = len(questions)
    result["summary"] = {
        key: sum(float(q["metrics"][key]) for q in questions) / n
        for key in ["hit_at_1", "hit_at_5", "evidence_recall_at_5", "mrr_at_5"]
    }
    result["summary"]["all_passed"] = all(
        q["metrics"]["hit_at_5"] and q["metrics"]["evidence_recall_at_5"] == 1
        for q in questions
    )
    result["ragas_summary"] = {}
    for name in ["context_precision", "context_recall"]:
        values = [
            q["ragas"]["scores"][name]
            for q in questions
            if name in q["ragas"]["scores"]
        ]
        result["ragas_summary"][name] = dict(
            mean=sum(values) / len(values) if values else None,
            valid=len(values),
            missing=n - len(values),
        )
    (output / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    with (output / "results.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "id",
                "category",
                "question",
                "reference",
                "hit_at_1",
                "hit_at_5",
                "evidence_recall_at_5",
                "mrr_at_5",
                "first_evidence_rank",
                "ragas_status",
                "context_precision",
                "context_recall",
            ],
        )
        writer.writeheader()
        for q in questions:
            row = {k: q[k] for k in ["id", "category", "question", "reference"]}
            row.update({k: v for k, v in q["metrics"].items() if k != "covered_groups"})
            row.update(
                ragas_status=q["ragas"]["status"],
                context_precision=q["ragas"]["scores"].get("context_precision"),
                context_recall=q["ragas"]["scores"].get("context_recall"),
            )
            # CSV can be opened in spreadsheets; do not allow document text to become formulas.
            row = {
                k: (
                    "'" + v
                    if isinstance(v, str) and v.startswith(("=", "+", "-", "@"))
                    else v
                )
                for k, v in row.items()
            }
            writer.writerow(row)
    body = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>年报RAG召回评测</title>',
        "<style>body{font:16px/1.65 system-ui;max-width:1150px;margin:40px auto;padding:0 24px;color:#243044}table{border-collapse:collapse;width:100%}td,th{padding:9px;border:1px solid #cbd5e1;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:12px}.pass{color:#13754c}.fail{color:#b42318}summary{cursor:pointer}section{margin:24px 0;border-top:1px solid #cbd5e1}</style>",
        "<h1>茅台2024年报 · 文字RAG召回评测</h1>",
        f"<p>{escape(result['limitations'])}</p>",
        f"<p>人工复核状态：{escape(result['manual_review'])}</p>",
        f"<pre>{escape(json.dumps(result['summary'], ensure_ascii=False, indent=2))}</pre>",
        "<h2>Ragas辅助评分</h2>",
        f"<pre>{escape(json.dumps(result['ragas_summary'], ensure_ascii=False, indent=2))}</pre>",
        "<p>缺失分数不计零；模型评分不替代证据覆盖核验。向量分数不是概率。页码为块起始物理页。</p>",
        "<table><tr><th>题号</th><th>问题</th><th>Hit@1</th><th>Hit@5</th><th>证据Recall@5</th><th>首证据排名</th></tr>",
    ]
    for q in questions:
        m = q["metrics"]
        body.append(
            f'<tr><td><a href="#{q["id"]}">{q["id"]}</a></td><td>{escape(q["question"])}</td><td>{m["hit_at_1"]}</td><td>{m["hit_at_5"]}</td><td>{m["evidence_recall_at_5"]:.2f}</td><td>{m["first_evidence_rank"]}</td></tr>'
        )
    body.append("</table>")
    for q in questions:
        passed = q["metrics"]["hit_at_5"] and q["metrics"]["evidence_recall_at_5"] == 1
        body.extend(
            [
                f'<section id="{q["id"]}"><h2 class="{"pass" if passed else "fail"}">{q["id"]} · {escape(q["question"])}</h2>',
                f"<p>参考答案：{escape(q['reference'])}</p>",
                "<h3>标注证据</h3>",
            ]
        )
        for index, group in enumerate(q["evidence_groups"], 1):
            for a in group["alternatives"]:
                body.append(
                    f"<p>证据组{index} · 原文物理页{a['page']} · 字符位置[{a['start']},{a['end']})</p><pre>{escape(a['quote'])}</pre>"
                )
        body.append("<h3>Top 5检索结果</h3>")
        for rank, item in enumerate(q["retrieval"]["items"], 1):
            body.append(
                f"<details><summary>排名{rank} · 起始页{item['page']} · 分数{item['score']:.4f} · 位置[{item['start']},{item['end']})</summary><pre>{escape(item['text'])}</pre></details>"
            )
        if not passed:
            body.append(
                "<p>未通过：已标注证据未被前5块完整命中或覆盖；需对照解析原文、块边界和实际排名定位，不能仅从分数推断原因。</p>"
            )
        body.append(
            f"<h3>Ragas（模型判断）</h3><pre>{escape(json.dumps(q['ragas'], ensure_ascii=False, indent=2))}</pre></section>"
        )
    body.extend(
        [
            "<h2>运行配置</h2>",
            f"<pre>{escape(json.dumps({k: result[k] for k in ['created_at', 'fixture_sha256', 'source_sha256', 'generation_id', 'pipeline', 'embedding', 'versions']}, ensure_ascii=False, indent=2))}</pre>",
            "</html>",
        ]
    )
    (output / "report.html").write_text("\n".join(body))
