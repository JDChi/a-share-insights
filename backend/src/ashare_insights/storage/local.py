import hashlib
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
import httpx

PDF_PATH = re.compile(r"/finalpage/\d{4}-\d{2}-\d{2}/\d+\.pdf", re.I)
MAX_BYTES = 200 * 1024 * 1024


def validate_pdf_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "static.cninfo.com.cn" or parsed.port is not None or parsed.username or parsed.password or parsed.query or parsed.fragment or not PDF_PATH.fullmatch(parsed.path):
        raise ValueError("PDF 地址不是允许的巨潮静态文件路径")


def save_pdf(client: httpx.Client, url: str, data_dir: Path) -> tuple[str, int, str]:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "tmp").mkdir(exist_ok=True)
    (data_dir / "pdfs").mkdir(exist_ok=True)
    partial = None
    try:
        for _ in range(6):
            validate_pdf_url(url)
            with client.stream("GET", url, follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location", "")
                    url = str(httpx.URL(url).join(location))
                    continue
                response.raise_for_status()
                digest = hashlib.sha256()
                total = 0
                first = bytearray()
                tail = bytearray()
                with tempfile.NamedTemporaryFile(dir=data_dir / "tmp", suffix=".part", delete=False) as stream:
                    partial = Path(stream.name)
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError("PDF 超过 200 MiB 上限")
                        if len(first) < 5:
                            first.extend(chunk[:5 - len(first)])
                        tail = (tail + chunk)[-2048:]
                        digest.update(chunk)
                        stream.write(chunk)
                if bytes(first) != b"%PDF-" or b"%%EOF" not in tail:
                    raise ValueError("响应未通过 PDF 文件头和结束标记检查")
                sha = digest.hexdigest()
                target = data_dir / "pdfs" / f"{sha}.pdf"
                if target.exists():
                    partial.unlink()
                else:
                    os.replace(partial, target)
                partial = None
                return sha, total, str(target)
        raise ValueError("PDF 重定向次数过多")
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
