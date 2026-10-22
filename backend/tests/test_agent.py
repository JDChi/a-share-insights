import json
from dataclasses import replace


from ashare_insights.services.agent import AgentService
from test_rag import seed_report


class Rag:
    def __init__(self, status="answered", fail=False):
        self.status, self.fail = status, fail

    def ask(self, report_id, question):
        if self.fail:
            raise RuntimeError("credential must not leak")
        return dict(
            status=self.status,
            answer="主要生产茅台酒及系列酒"
            if self.status == "answered"
            else "当前检索证据不足",
            citations=[
                dict(
                    chunk_id="c1",
                    quote="茅台酒及系列酒",
                    page=7,
                    report_id=report_id,
                    title="年报",
                    file_url="/file",
                    file_sha256="test",
                )
            ]
            if self.status == "answered"
            else [],
            retrieval=dict(items=[], warnings=[]),
        )


class Financial:
    def __init__(self, status="ok"):
        self.status, self.calls = status, []

    def query(self, code, year, metrics):
        self.calls.append((code, year, metrics))
        return dict(
            status=self.status,
            items=[
                dict(
                    metric="revenue",
                    value="100",
                    unit="CNY",
                    company_code=code,
                    year=year,
                    scope="consolidated",
                    source="eastmoney",
                    name="营业收入",
                    display_amount="约0.00亿元",
                    provider="akshare",
                    report_date=f"{year}-12-31",
                    interface="stock_profit_sheet_by_yearly_em",
                    source_url="https://example.com",
                    source_field="OPERATE_INCOME",
                    raw_value="100",
                    fetched_at="2026-10-22T00:00:00+00:00",
                    cached=False,
                    source_notice_date=None,
                    source_update_date=None,
                )
            ]
            if self.status == "ok"
            else [],
            warnings=[],
        )


def test_three_scenarios_and_actual_react_parser(api, tmp_path, monkeypatch):
    """Scripted LLM responses test execution, NOT prompt reliability (live eval does)."""
    from llama_index.core.llms import ChatMessage, ChatResponse, MockLLM
    from pydantic import Field

    class ScriptedLLM(MockLLM):
        responses: list[str] = Field(default_factory=list)

        async def achat(self, messages, **kwargs):
            return ChatResponse(
                message=ChatMessage(role="assistant", content=self.responses.pop(0))
            )

    client, app = api
    report = seed_report(app, tmp_path)
    for category in ("rag", "api", "unavailable"):
        financial = Financial("no_data" if category == "unavailable" else "ok")
        rag = Rag("answered" if category == "rag" else "insufficient_evidence")
        responses = [
            'Thought: 查询年报。\nAction: query_annual_report\nAction Input: {"question":"查询"}'
        ]
        if category != "rag":
            responses.append(
                'Thought: 证据不足，补查年度财务。\nAction: get_financial_data\nAction Input: {"year":2018,"metrics":["revenue"]}'
            )
        responses.append(
            "Thought: I can answer without using any more tools.\nAnswer: "
            + json.dumps(
                dict(
                    status="insufficient_evidence"
                    if category == "unavailable"
                    else "answered",
                    answer="暂无足够证据"
                    if category == "unavailable"
                    else "100元"
                    if category == "api"
                    else "茅台酒及系列酒",
                    source_ids=[]
                    if category == "unavailable"
                    else ["tool-2-api-1"]
                    if category == "api"
                    else ["c1"],
                )
            )
        )

        def fake_llm(settings):
            llm = ScriptedLLM()
            llm.responses = responses
            return llm

        monkeypatch.setattr("ashare_insights.services.agent.make_llm", fake_llm)
        app.state.agent = AgentService(
            app.state.sessions, rag, app.state.settings, financial
        )
        result = client.post(
            f"/api/v1/reports/{report}/agent/ask", json=dict(question="查询")
        ).json()
        assert result["status"] == (
            "insufficient_evidence" if category == "unavailable" else "answered"
        ), json.dumps(result, ensure_ascii=False)
        assert [c["tool"] for c in result["tool_calls"]] == (
            ["query_annual_report"]
            if category == "rag"
            else ["query_annual_report", "get_financial_data"]
        )
        assert len(financial.calls) == (0 if category == "rag" else 1)
        if financial.calls:
            assert financial.calls[0] == ("600519", 2018, ["revenue"])


def test_no_hardcoded_routing_and_bad_source_rejected(api, tmp_path):
    client, app = api
    report = seed_report(app, tmp_path)
    financial = Financial()

    async def runner(tools, prompt, question):
        # Deliberately violate the recommended sequence: wrapper must NOT secretly call RAG.
        await tools[1].acall(year=2024, metrics=["revenue"])
        return json.dumps(
            dict(status="answered", answer="伪造答案", source_ids=["invented"])
        )

    app.state.agent = AgentService(
        app.state.sessions, Rag(fail=True), app.state.settings, financial, runner
    )
    r = client.post(
        f"/api/v1/reports/{report}/agent/ask", json=dict(question="收入")
    ).json()
    assert r["status"] == "error" and r["sources"] == []
    assert [c["tool"] for c in r["tool_calls"]] == ["get_financial_data"]
    assert financial.calls


def test_errors_no_evidence_and_tool_call_limit(api, tmp_path):
    client, app = api
    report = seed_report(app, tmp_path)

    async def runner(tools, prompt, question):
        for _ in range(3):
            await tools[0].acall(question="查询")
        return json.dumps(
            dict(status="error", answer="服务失败，无法查询", source_ids=[])
        )

    app.state.agent = AgentService(
        app.state.sessions,
        Rag(fail=True),
        replace(app.state.settings, agent_max_tool_calls=1),
        runner=runner,
    )
    r = client.post(
        f"/api/v1/reports/{report}/agent/ask", json=dict(question="查询")
    ).json()
    assert r["status"] == "error" and len(r["tool_calls"]) == 3
    assert sum(c["executed"] for c in r["tool_calls"]) == 1
    assert "credential" not in json.dumps(r)
    assert r["tool_calls"][0]["status"] == "error"
    assert (
        client.post(
            f"/api/v1/reports/{report}/agent/ask", json=dict(question=" ")
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/reports/999999/agent/ask", json=dict(question="查询")
        ).status_code
        == 404
    )


def test_agent_without_any_tools_or_evidence_cannot_answer(api, tmp_path):
    client, app = api
    report = seed_report(app, tmp_path)

    async def runner(tools, prompt, question):
        return json.dumps(dict(status="answered", answer="模型猜测", source_ids=[]))

    app.state.agent = AgentService(
        app.state.sessions, Rag(), app.state.settings, runner=runner
    )
    r = client.post(
        f"/api/v1/reports/{report}/agent/ask", json=dict(question="查询")
    ).json()
    assert r["status"] == "error"


def test_legacy_bse_code_resolves_only_same_official_org_id(api, tmp_path):
    from ashare_insights.db.models import Company, utc_now

    client, app = api
    report = seed_report(app, tmp_path, code="430418")
    with app.state.sessions.begin() as db:
        db.add(
            Company(
                code="920418",
                name="same company",
                org_id="430418",
                category="test",
                directory_fetched_at=utc_now(),
            )
        )
        db.add(
            Company(
                code="920999",
                name="different company",
                org_id="other",
                category="test",
                directory_fetched_at=utc_now(),
            )
        )
    financial = Financial()

    async def runner(tools, prompt, question):
        await tools[1].acall(year=2024, metrics=["revenue"])
        return json.dumps(
            dict(status="answered", answer="100元", source_ids=["tool-1-api-1"])
        )

    app.state.agent = AgentService(
        app.state.sessions, Rag(), app.state.settings, financial, runner
    )
    r = client.post(
        f"/api/v1/reports/{report}/agent/ask", json=dict(question="收入")
    ).json()
    assert r["status"] == "answered" and financial.calls[0][0] == "920418"
    assert r["tool_calls"][0]["result"]["bound_company_code"] == "430418"
    assert r["sources"][0]["company_code"] == "920418"


def test_mismatched_conversion_cannot_be_success(api, tmp_path):
    client, app = api
    report = seed_report(app, tmp_path)

    async def runner(tools, prompt, question):
        await tools[1].acall(year=2024, metrics=["revenue"])
        return json.dumps(
            dict(
                status="answered",
                answer="100元，约100万元",
                source_ids=["tool-1-api-1"],
            )
        )

    app.state.agent = AgentService(
        app.state.sessions, Rag(), app.state.settings, Financial(), runner
    )
    r = client.post(
        f"/api/v1/reports/{report}/agent/ask", json=dict(question="收入")
    ).json()
    assert r["status"] == "error" and "金额或单位" in r["warnings"][0]
