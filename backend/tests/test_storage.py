import httpx
import pytest
from ashare_insights.storage.local import save_pdf

URL = "https://static.cninfo.com.cn/finalpage/2025-04-03/1222993920.PDF"


def test_rejects_non_pdf_and_cleans_temp(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"not a pdf"))) as client:
        with pytest.raises(ValueError, match="PDF"):
            save_pdf(client, URL, tmp_path)
    assert not list((tmp_path / "tmp").glob("*.part"))


def test_rejects_redirect_to_other_host(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"location": "https://example.org/file.pdf"}))) as client:
        with pytest.raises(ValueError, match="巨潮"):
            save_pdf(client, URL, tmp_path)
