"""Checks for native Markdown exports; never repair the extracted content."""

from markdown_it import MarkdownIt


def markdown_tables(text):
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    tables, table, row, cell = [], None, None, None
    for token in tokens:
        if token.type == "table_open":
            table = {"rows": [], "line_range": token.map}
        elif table is not None:
            if token.type == "tr_open":
                row = []
            elif token.type in ("th_open", "td_open"):
                cell = ""
            elif token.type == "inline" and cell is not None:
                for part in token.children or []:
                    if part.type in ("text", "code_inline"):
                        cell += part.content
                    elif part.type in ("softbreak", "hardbreak"):
                        cell += "\n"
                    elif part.type == "html_inline":
                        cell += (
                            "\n"
                            if part.content.lower() in ("<br>", "<br/>", "<br />")
                            else part.content
                        )
            elif token.type in ("th_close", "td_close"):
                row.append(cell)
                cell = None
            elif token.type == "tr_close":
                table["rows"].append(row)
            elif token.type == "table_close":
                tables.append(table)
                table = None
    return tables


def iou(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - area
    return area / union if union > 0 else 0


def exported_tables(chunk):
    """Read the actual MD view, using export metadata only for its location."""
    text = chunk["text"]
    boxes = [b for b in chunk.get("page_boxes", []) if b.get("class") == "table"]
    result = []
    for box in boxes:
        start, end = box["pos"]
        for table in markdown_tables(text[start:end]):
            result.append(
                {
                    **table,
                    "bbox": list(box["bbox"]),
                    "markdown_range": [start, end],
                    "association": "exported_page_box",
                    "cell_span_information": "not_represented_in_markdown",
                }
            )
    if result:
        return result
    tables = markdown_tables(text)
    locations = chunk.get("tables", [])
    # Older rule exports may lack positional spans. Do not guess if counts differ.
    if len(locations) == len(tables):
        locations = sorted(locations, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        for table, location in zip(tables, locations):
            table.update(
                bbox=list(location["bbox"]),
                association="matching_counts_page_reading_order",
                cell_span_information="not_represented_in_markdown",
            )
    else:
        for table in tables:
            table.update(
                bbox=None,
                association="unresolved",
                cell_span_information="not_represented_in_markdown",
            )
    return tables


def match_table(tables, bbox):
    ranked = sorted(
        ((iou(t["bbox"], bbox), t) for t in tables if t.get("bbox")),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.2:
        return [], {"status": "unmatched", "iou": ranked[0][0] if ranked else 0}
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 1e-6:
        return [], {"status": "ambiguous", "iou": ranked[0][0]}
    return [ranked[0][1]], {"status": "matched", "iou": ranked[0][0]}
