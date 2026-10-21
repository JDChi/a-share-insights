import json
from pathlib import Path
from dataclasses import replace
from uuid import uuid4
import math
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from urllib.parse import parse_qs
import httpx
import pytest
from fastapi.testclient import TestClient
from ashare_insights.config import Settings, get_settings
from ashare_insights.rag.embedding import EmbeddingProvider
from ashare_insights.db.models import Base
from ashare_insights.main import create_app
from ashare_insights.providers.cninfo import CninfoProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_transport():
    def respond(request: httpx.Request):
        if request.url.path.endswith("szse_stock.json"):
            return httpx.Response(
                200, json=json.loads((FIXTURES / "companies.json").read_text())
            )
        if request.url.path.endswith("hisAnnouncement/query"):
            stock = parse_qs(request.content.decode())["stock"][0].split(",")[0]
            return httpx.Response(
                200, json=json.loads((FIXTURES / f"reports-{stock}.json").read_text())
            )
        if request.url.host == "static.cninfo.com.cn":
            return httpx.Response(
                200,
                content=b"%PDF-1.4\n1 0 obj\nendobj\n%%EOF\n",
                headers={"content-type": "application/pdf"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(respond)


class FakeEmbedding(EmbeddingProvider):
    fail = False
    version = 1

    @property
    def descriptor(self):
        return dict(
            provider="fake-test-only", dimension=3, max_tokens=512, version=self.version
        )

    def offsets(self, text):
        return [(i, i + 1) for i in range(len(text)) if not text[i].isspace()]

    def token_count(self, text):
        return len(self.offsets(text))

    def document_vectors(self, texts):
        if self.fail:
            raise ValueError("模拟向量失败")
        return [self.query_vector(t) for t in texts]

    def query_vector(self, text):
        v = [float("白酒" in text), float("家电" in text), 0.1]
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]


@pytest.fixture
def pg_url():
    admin = create_engine(get_settings().database_url)
    schema = "test_" + uuid4().hex
    with admin.begin() as db:
        db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.execute(text(f"CREATE SCHEMA {schema}"))
    Base.metadata.create_all(
        admin.execution_options(schema_translate_map={None: schema})
    )
    url = make_url(get_settings().database_url)
    url = url.update_query_dict({"options": f"-csearch_path={schema},public"})
    yield url.render_as_string(hide_password=False)
    with admin.begin() as db:
        db.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


@pytest.fixture
def api(tmp_path, fixture_transport, pg_url):
    client = httpx.Client(
        transport=fixture_transport, headers={"Referer": "https://www.cninfo.com.cn/"}
    )
    provider = CninfoProvider(client=client)
    app = create_app(
        replace(get_settings(), data_dir=tmp_path, database_url=pg_url, llm_api_key=""),
        provider,
        embedding=FakeEmbedding(),
    )
    with app.state.sessions() as db:
        assert db.scalar(text("SELECT current_schema()")).startswith("test_")
        assert db.scalar(text("SELECT count(*) FROM companies")) == 0
    with TestClient(app) as test_client:
        yield test_client, app
    client.close()
