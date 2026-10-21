import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

PARSER_VERSION = "pypdf-6.10.0-nfc-v1"
SPLITTER_VERSION = "token-offset-sentence-v2"


@dataclass
class ParsedText:
    text: str
    pages: list[dict]
    warnings: list[str]


def parse_pdf(path: Path) -> ParsedText:
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF 已加密，无法提取文字")
    texts, pages, warnings = [], [], []
    position = 0
    for number, page in enumerate(reader.pages, 1):
        try:
            content = unicodedata.normalize(
                "NFC",
                (page.extract_text() or "").replace("\x00", "").replace("\r\n", "\n"),
            )
        except Exception:
            content = ""
            warnings.append(f"物理页 {number} 提取失败")
        if len(content.strip()) < 20:
            warnings.append(f"物理页 {number} 文字过少；本版本不做OCR")
        if texts:
            texts.append("\n\n")
            position += 2
        pages.append(dict(page=number, start=position, end=position + len(content)))
        texts.append(content)
        position += len(content)
    text = "".join(texts)
    if not text.strip():
        raise ValueError("PDF 无可用文字；本版本不做OCR")
    return ParsedText(text, pages, warnings)


def split_text(parsed: ParsedText, provider, size=384, overlap=64):
    if not 0 <= overlap < size or size + 2 > provider.descriptor["max_tokens"]:
        raise ValueError("切块大小/重叠超过有效范围")
    offsets = provider.offsets(parsed.text)
    starts = [p["start"] for p in parsed.pages]
    chunks, index = [], 0
    while index < len(offsets):
        end_index = min(index + size, len(offsets))
        # Stop at a sentence/paragraph boundary once there is enough new content.
        # Packing unrelated sections into a maximum-sized block dilutes retrieval.
        # 384 is a ceiling, not a target to fill; retain the configured overlap.
        if end_index < len(offsets):
            minimum = max(overlap + 1, size // 3)
            low = offsets[min(index + minimum, end_index - 1)][0]
            end = offsets[end_index - 1][1]
            matches = list(re.finditer(r"[。！？；]|\n\s*\n", parsed.text[low:end]))
            if matches:
                boundary = low + matches[0].end()
                while (
                    end_index > index + overlap + 1
                    and offsets[end_index - 1][1] > boundary
                ):
                    end_index -= 1
        start, end = offsets[index][0], offsets[end_index - 1][1]
        while provider.token_count(parsed.text[start:end]) > size:
            end_index -= 1
            if end_index <= index:
                raise ValueError("单个token无法安全切分")
            end = offsets[end_index - 1][1]
        content = parsed.text[start:end]
        if content.strip():
            page_index = max(0, bisect_right(starts, start) - 1)
            chunks.append(
                dict(
                    text=content,
                    start=start,
                    end=end,
                    page=parsed.pages[page_index]["page"],
                )
            )
        if end_index >= len(offsets):
            break
        index = max(index + 1, end_index - overlap)
    if not chunks:
        raise ValueError("PDF 没有可索引的文本块")
    return chunks
