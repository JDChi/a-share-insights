import json
from pathlib import Path
from urllib.parse import parse_qs
import httpx
import pytest
from fastapi.testclient import TestClient
from ashare_insights.config import Settings
from ashare_insights.db.models import Base
from ashare_insights.main import create_app
from ashare_insights.providers.cninfo import CninfoProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_transport():
    def respond(request: httpx.Request):
        if request.url.path.endswith("szse_stock.json"):
            return httpx.Response(200, json=json.loads((FIXTURES / "companies.json").read_text()))
        if request.url.path.endswith("hisAnnouncement/query"):
            stock = parse_qs(request.content.decode())["stock"][0].split(",")[0]
            return httpx.Response(200, json=json.loads((FIXTURES / f"reports-{stock}.json").read_text()))
        if request.url.host == "static.cninfo.com.cn":
            return httpx.Response(200, content=b"%PDF-1.4\n1 0 obj\nendobj\n%%EOF\n", headers={"content-type": "application/pdf"})
        return httpx.Response(404)
    return httpx.MockTransport(respond)


@pytest.fixture
def api(tmp_path, fixture_transport):
    client = httpx.Client(transport=fixture_transport, headers={"Referer": "https://www.cninfo.com.cn/"})
    provider = CninfoProvider(client=client)
    app = create_app(Settings(data_dir=tmp_path), provider)
    Base.metadata.create_all(app.state.sessions.kw["bind"])
    with TestClient(app) as test_client:
        yield test_client, app
    client.close()
