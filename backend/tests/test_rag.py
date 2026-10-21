import hashlib
from uuid import uuid4

import pytest
from conftest import FakeEmbedding
from sqlalchemy import func, select

from ashare_insights.db.models import (
    Announcement,
    Company,
    File,
    IndexGeneration,
    IndexTask,
    RagChunk,
    utc_now,
)
from ashare_insights.rag.embedding import validate_vectors
from ashare_insights.rag.evaluation import covers, metrics
from ashare_insights.rag.text import ParsedText, parse_pdf, split_text
from ashare_insights.services.indexing import IndexManager


def seed_report(app, tmp_path, text="白酒生产与销售。", code="600519"):
    path = tmp_path / (code + ".pdf")
    path.write_bytes(b"%PDF-1.4\n" + text.encode() + b"\n%%EOF")
    with app.state.sessions.begin() as db:
        company = Company(
            code=code,
            name=code,
            org_id=code,
            category="test",
            directory_fetched_at=utc_now(),
        )
        file = File(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            path=str(path),
            size_bytes=path.stat().st_size,
        )
        db.add_all([company, file])
        db.flush()
        report = Announcement(
            company_id=company.id,
            source="fixture",
            external_id=str(uuid4()),
            report_year=2024,
            title="年度报告",
            raw_title="年度报告",
            published_at=__import__("datetime").date(2025, 1, 1),
            pdf_url="https://static.cninfo.com.cn/finalpage/2025-01-01/1.PDF",
            source_payload="{}",
            fetched_at=utc_now(),
            file_id=file.id,
        )
        db.add(report)
        db.flush()
        return report.id


def fake_parse(monkeypatch):
    def parse(path):
        text = (
            "白酒主营业务，生产与销售。"
            if "600519" in str(path)
            else "家电主营业务，研发与销售。"
        )
        return ParsedText(text, [dict(page=1, start=0, end=len(text))], [])

    monkeypatch.setattr("ashare_insights.services.indexing.parse_pdf", parse)


def test_cross_page_offsets_and_token_budget():
    provider = FakeEmbedding()
    parsed = ParsedText(
        "甲乙丙丁戊己庚辛壬癸",
        [dict(page=1, start=0, end=4), dict(page=2, start=4, end=10)],
        [],
    )
    chunks = split_text(parsed, provider, size=6, overlap=2)
    assert chunks[0]["text"] == "甲乙丙丁戊己" and chunks[0]["page"] == 1
    assert chunks[1]["page"] == 2 and chunks[1]["start"] == 4
    assert all(c["text"] == parsed.text[c["start"] : c["end"]] for c in chunks)
    assert all(provider.token_count(c["text"]) <= 6 for c in chunks)
    with pytest.raises(ValueError):
        split_text(parsed, provider, size=5, overlap=5)


def test_blank_pdf_and_vector_validation(tmp_path):
    from pypdf import PdfWriter

    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.write(path)
    with pytest.raises(ValueError, match="无可用文字"):
        parse_pdf(path)
    for vectors in [[[float("nan"), 0, 1]], [[2, 0, 0]], [[1, 0]]]:
        with pytest.raises(ValueError):
            validate_vectors(vectors, 1, 3)
    assert validate_vectors([[1, 0, 0]], 1, 3) == [[1.0, 0.0, 0.0]]


def test_quote_restores_only_layout_whitespace():
    from ashare_insights.services.rag import original_quote

    assert (
        original_quote("公司营\n销网络覆盖64 个国家。", "公司营销网络覆盖64个国家")
        == "公司营\n销网络覆盖64 个国家"
    )
    assert original_quote("营业收入64亿元", "营业收入65亿元") is None
    assert original_quote("营业收入64亿元", "  ") is None


def test_index_reuse_isolation_and_failed_replacement(api, tmp_path, monkeypatch):
    client, app = api
    fake_parse(monkeypatch)
    one = seed_report(app, tmp_path)
    two = seed_report(app, tmp_path, "家电研发", "000333")
    assert (
        client.post(
            f"/api/v1/reports/{one}/retrieve", json={"question": "白酒"}
        ).status_code
        == 409
    )
    for report in [one, two]:
        task = client.post(f"/api/v1/reports/{report}/index").json()
        app.state.indexer.queue.join()
        assert (
            client.get(f"/api/v1/index-tasks/{task['id']}").json()["status"]
            == "completed"
        )
    initial = client.post(f"/api/v1/reports/{one}/index").json()
    result = client.post(
        f"/api/v1/reports/{one}/retrieve", json={"question": "白酒"}
    ).json()
    assert result["items"] and all("家电" not in i["text"] for i in result["items"])
    assert client.post(f"/api/v1/reports/{one}/index").json()["id"] == initial["id"]
    app.state.indexer.provider.fail = True
    replacement = client.post(
        f"/api/v1/reports/{one}/index", json={"force": True}
    ).json()
    app.state.indexer.queue.join()
    assert (
        client.get(f"/api/v1/index-tasks/{replacement['id']}").json()["status"]
        == "failed"
    )
    still = client.post(
        f"/api/v1/reports/{one}/retrieve", json={"question": "白酒"}
    ).json()
    assert still["generation_id"] == result["generation_id"]
    app.state.indexer.provider.fail = False
    retry = client.post(f"/api/v1/reports/{one}/index", json={"force": True}).json()
    app.state.indexer.queue.join()
    assert (
        client.get(f"/api/v1/index-tasks/{retry['id']}").json()["status"] == "completed"
    )
    assert (
        client.post(
            f"/api/v1/reports/{one}/retrieve", json={"question": "白酒"}
        ).json()["generation_id"]
        != result["generation_id"]
    )
    app.state.indexer.provider.version = 2
    assert (
        client.post(
            f"/api/v1/reports/{one}/retrieve", json={"question": "白酒"}
        ).status_code
        == 409
    )


def test_citations_and_no_evidence(api, tmp_path, monkeypatch):
    client, app = api
    fake_parse(monkeypatch)
    report = seed_report(app, tmp_path)
    client.post(f"/api/v1/reports/{report}/index")
    app.state.indexer.queue.join()

    class Gateway:
        mode = "good"

        def answer(self, question, items):
            if self.mode == "none":
                return dict(status="insufficient_evidence", answer="未知", citations=[])
            return dict(
                status="answered",
                answer="白酒生产与销售",
                citations=[
                    dict(
                        chunk_id=items[0]["id"] if self.mode == "good" else "fake",
                        quote="白酒主营业务",
                    )
                ],
            )

    gateway = Gateway()
    app.state.rag.gateway = gateway
    path = f"/api/v1/reports/{report}/ask"
    assert (
        client.post(path, json={"question": "白酒"}).json()["citations"][0]["page"] == 1
    )
    gateway.mode = "bad"
    assert client.post(path, json={"question": "白酒"}).status_code == 503
    gateway.mode = "none"
    assert (
        client.post(path, json={"question": "不存在的证据"}).json()["status"]
        == "insufficient_evidence"
    )
    assert client.post(path, json={"question": "  "}).status_code == 422
    assert client.post("/api/v1/reports/999/index").status_code == 404


def test_restart_recovers_interrupted_index(api, tmp_path, monkeypatch):
    client, app = api
    fake_parse(monkeypatch)
    report = seed_report(app, tmp_path)
    task = client.post(f"/api/v1/reports/{report}/index").json()
    app.state.indexer.queue.join()
    app.state.indexer.stop()
    with app.state.sessions.begin() as db:
        gen = db.get(IndexGeneration, task["generation_id"])
        gen.active = False
        gen.status = "building"
        t = db.get(IndexTask, task["id"])
        t.status = "running"
        t.progress = 0.5
    replacement = IndexManager(
        app.state.sessions, app.state.indexer.provider, app.state.settings
    )
    app.state.indexer = replacement
    replacement.start()
    replacement.queue.join()
    assert replacement.get(task["id"])["status"] == "completed"
    with app.state.sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RagChunk)
                .where(RagChunk.generation_id == task["generation_id"])
            )
            == 1
        )
    replacement.stop()


def test_evidence_metrics_do_not_accept_same_page_or_wrong_file():
    question = {
        "evidence_groups": [
            {"alternatives": [{"start": 10, "end": 20}]},
            {"alternatives": [{"start": 30, "end": 40}]},
        ]
    }
    item = lambda a, b, sha="correct": dict(start=a, end=b, file_sha256=sha, page=1)
    assert metrics(question, [item(0, 9)], "correct")["hit_at_5"] is False
    assert (
        metrics(question, [item(0, 50, "wrong")], "correct")["evidence_recall_at_5"]
        == 0
    )
    result = metrics(question, [item(10, 20), item(30, 35), item(35, 40)], "correct")
    assert (
        result["hit_at_1"]
        and result["evidence_recall_at_5"] == 1
        and result["mrr_at_5"] == 1
    )
    assert covers([(10, 15), (15, 20)], 10, 20)
    assert not covers([(10, 14), (15, 20)], 10, 20)
