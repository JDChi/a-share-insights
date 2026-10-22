"""Isolated native PyMuPDF 2.x experiment. Never changes production indexes.

data/.venv-pymupdf/bin/python backend/scripts/pymupdf_experiment.py --scope all
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import socket
import subprocess
import sys
import time
import traceback

from pymupdf_checks import exported_tables, match_table
from table_checks import check_cell, compact

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("table_samples.json")
REVIEW_PAGES = {
    "maotai": [1, 7, 8, 22, 23, 63, 64, 72, 100, 143],
    "midea": [1, 2, 50, 100, 148, 156, 157, 158, 161, 295],
}
MODES = ["layout", "lines_strict", "text"]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def retained_characters(original, converted):
    # Diagnostic only: multiset retention ignores order and cannot prove correctness.
    old = Counter(c for c in original if c.isalnum())
    new = Counter(c for c in converted if c.isalnum())
    return sum((old & new).values()) / sum(old.values()) if old else None


def worker(args):
    spec = json.loads(FIXTURE.read_text())
    sources = args.sources.split(",")
    paths = {}
    for source in sources:
        path = args.pdf_dir / (spec["sources"][source]["sha256"] + ".pdf")
        if checksum(path) != spec["sources"][source]["sha256"]:
            raise ValueError("PDF checksum mismatch: " + source)
        paths[source] = path
    args.output.mkdir(parents=True, exist_ok=False)
    attempts = []

    def deny_network(*a, **kw):
        attempts.append("python_socket_connect")
        raise RuntimeError("Network disabled during PDF conversion")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    os.environ.update(OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4", HF_HUB_OFFLINE="1")
    started = time.perf_counter()
    result = {
        "scope": args.scope,
        "mode": args.mode,
        "status": "running",
        "fixture_sha256": checksum(FIXTURE),
        "sources": spec["sources"],
        "python": platform.python_version(),
        "versions": {},
        "pages": [],
        "samples": [],
        "documents": [],
        "network_policy": "Python socket connects blocked before parser import",
        "ocr": False,
        "write_images": False,
        "manual_parsing_hints": False,
    }
    for name in [
        "pymupdf",
        "pymupdf-layout",
        "pymupdf4llm",
        "onnxruntime",
        "numpy",
        "pypdf",
        "markdown-it-py",
        "pillow",
    ]:
        try:
            result["versions"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result["versions"][name] = None
    try:
        import onnxruntime

        onnxruntime.disable_telemetry_events()
        import pymupdf
        from pypdf import PdfReader

        pymupdf.use_layout(args.mode == "layout")
        result["layout_enabled"] = args.mode == "layout"
        result["onnx_telemetry_disabled"] = True
        result["resources"] = []
        for name in ["pymupdf", "pymupdf-layout"]:
            dist = importlib.metadata.distribution(name)
            for file in dist.files or []:
                if str(file).endswith((".onnx", ".yaml", ".yml")):
                    path = Path(dist.locate_file(file))
                    result["resources"].append(
                        {
                            "package": name,
                            "path": str(file),
                            "bytes": path.stat().st_size,
                            "sha256": checksum(path),
                        }
                    )
        for source, path in paths.items():
            with pymupdf.open(path) as doc:
                reader = PdfReader(path)
                numbers = (
                    sorted(
                        {s["page"] for s in spec["samples"] if s["source"] == source}
                    )
                    if args.scope == "samples"
                    else list(range(1, len(doc) + 1))
                )
                document = {
                    "source": source,
                    "expected_pages": len(doc),
                    "selected_pages": numbers,
                    "completed_pages": [],
                    "failed_pages": [],
                    "markdown_seconds": 0.0,
                    "json_seconds": 0.0,
                }
                result["documents"].append(document)
                folder = args.output / source
                folder.mkdir()
                joined, mapping, position = [], [], 0
                for number in numbers:
                    page_dir = folder / f"page-{number:03}"
                    page_dir.mkdir()
                    t = time.perf_counter()
                    kwargs = dict(pages=[number - 1])
                    if args.mode == "layout":
                        kwargs["use_ocr"] = False
                    md_kwargs = dict(
                        kwargs,
                        page_chunks=True,
                        write_images=False,
                        embed_images=False,
                        show_progress=False,
                    )
                    if args.mode != "layout":
                        md_kwargs["table_strategy"] = args.mode
                    try:
                        chunks = doc.to_markdown(**md_kwargs)
                        md_seconds = time.perf_counter() - t
                        if not isinstance(chunks, list) or len(chunks) != 1:
                            raise ValueError("Expected exactly one exported page chunk")
                        chunk = chunks[0]
                        metadata = chunk.get("metadata", {})
                        exported_page = metadata.get(
                            "page_number", metadata.get("page")
                        )
                        if exported_page != number:
                            raise ValueError(
                                f"Unexpected physical page mapping {exported_page} != {number}"
                            )
                        text = chunk["text"]
                        (page_dir / "document.md").write_text(text)
                        dump(page_dir / "markdown-export.json", chunks)
                        tables = exported_tables(chunk)
                        dump(page_dir / "tables-from-markdown.json", tables)
                        # Native JSON export is independent of the Markdown table view.
                        json_start = time.perf_counter()
                        json_error = None
                        native_tables = []
                        json_status = "not_supported_in_rule_mode"
                        try:
                            if args.mode == "layout":
                                raw_json = doc.to_json(**kwargs)
                                data = (
                                    json.loads(raw_json)
                                    if isinstance(raw_json, str)
                                    else raw_json
                                )
                                json_status = "completed"
                                for native_page in data.get("pages", []):
                                    for box in native_page.get("boxes", []):
                                        table = box.get("table")
                                        if table:
                                            native_tables.append(
                                                {
                                                    "bbox": table["bbox"],
                                                    "rows": table["extract"],
                                                    "raw": table,
                                                }
                                            )
                            else:
                                raw_json = json.dumps(
                                    {
                                        "status": json_status,
                                        "reason": "PyMuPDF 2.0 native to_json requires layout mode; see markdown-export.json for the actual rule export metadata",
                                    }
                                )
                            if isinstance(raw_json, str):
                                (page_dir / "document.json").write_text(raw_json)
                            else:
                                dump(page_dir / "document.json", raw_json)
                        except Exception as exc:
                            json_error = f"{type(exc).__name__}: {exc}"
                        json_seconds = time.perf_counter() - json_start
                        dump(page_dir / "native-tables.json", native_tables)
                        original = reader.pages[number - 1].extract_text() or ""
                        info = {
                            "source": source,
                            "page": number,
                            "status": "completed",
                            "markdown_seconds": md_seconds,
                            "json_seconds": json_seconds,
                            "json_error": json_error,
                            "json_status": json_status,
                            "characters": len(text),
                            "empty": not text.strip(),
                            "replacement_characters": text.count("\ufffd"),
                            "tables": len(tables),
                            "headings": [
                                line
                                for line in text.splitlines()
                                if line.startswith("#")
                            ],
                            "pypdf_character_retention_diagnostic": retained_characters(
                                original, text
                            ),
                            "body_sha256": hashlib.sha256(
                                (compact(text) or "").encode()
                            ).hexdigest(),
                        }
                        result["pages"].append(info)
                        document["completed_pages"].append(number)
                        document["markdown_seconds"] += md_seconds
                        document["json_seconds"] += json_seconds
                        prefix = f"<!-- PDF physical page: {number} -->\n\n"
                        body_start = position + len(prefix)
                        mapping.append(
                            {
                                "source_sha256": spec["sources"][source]["sha256"],
                                "page": number,
                                "start": body_start,
                                "end": body_start + len(text),
                                "elements": chunk.get("page_boxes", []),
                                "element_offset_origin": "page_markdown",
                                "native_metadata": chunk.get("metadata"),
                            }
                        )
                        section = prefix + text + "\n\n"
                        joined.append(section)
                        position += len(section)
                        if args.scope == "samples" or number in REVIEW_PAGES[source]:
                            doc[number - 1].get_pixmap(
                                matrix=pymupdf.Matrix(1.5, 1.5)
                            ).save(page_dir / "page.png")
                        for sample in (
                            s
                            for s in spec["samples"]
                            if s["source"] == source and s["page"] == number
                        ):
                            selected, match = match_table(tables, sample["bbox"])
                            checks = [check_cell(selected, c) for c in sample["checks"]]
                            native_selected, native_match = match_table(
                                native_tables, sample["bbox"]
                            )
                            native_checks = (
                                [
                                    check_cell(native_selected, c)
                                    for c in sample["checks"]
                                ]
                                if json_status == "completed"
                                else None
                            )
                            record = {
                                "id": sample["id"],
                                "source": source,
                                "page": number,
                                "matching": match,
                                "checks": checks,
                                "passed": sum(c["passed"] for c in checks),
                                "total": len(checks),
                                "native_json_matching": native_match,
                                "native_json_checks": native_checks,
                                "native_json_passed": sum(
                                    c["passed"] for c in native_checks
                                )
                                if native_checks is not None
                                else None,
                            }
                            result["samples"].append(record)
                            print(
                                f"{args.mode}/{args.scope} {sample['id']}: {record['passed']}/{record['total']}",
                                flush=True,
                            )
                    except Exception as exc:
                        result["pages"].append(
                            {
                                "source": source,
                                "page": number,
                                "status": "failed",
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        document["failed_pages"].append(number)
                        # Failed extraction remains a failure for every annotated cell.
                        for sample in (
                            s
                            for s in spec["samples"]
                            if s["source"] == source and s["page"] == number
                        ):
                            checks = [check_cell([], c) for c in sample["checks"]]
                            result["samples"].append(
                                {
                                    "id": sample["id"],
                                    "source": source,
                                    "page": number,
                                    "matching": {"status": "page_failed"},
                                    "checks": checks,
                                    "passed": 0,
                                    "total": len(checks),
                                }
                            )
                        (page_dir / "error.txt").write_text(traceback.format_exc())
                    if number % 10 == 0 or args.scope == "samples":
                        print(
                            f"{args.mode}/{args.scope} {source}: page {number}/{len(doc)}",
                            flush=True,
                        )
                    dump(args.output / "results.json", result)
                full_text = "".join(joined)
                (folder / "document.md").write_text(full_text)
                dump(folder / "page-map.json", mapping)
                # Ensure offsets refer to exactly the native per-page Markdown, not a guessed PDF character offset.
                for item in mapping:
                    native = (
                        folder / f"page-{item['page']:03}" / "document.md"
                    ).read_text()
                    if full_text[item["start"] : item["end"]] != native:
                        raise ValueError("Markdown page offset mismatch")
                duplicates = {}
                for page in result["pages"]:
                    if (
                        page["source"] == source
                        and page["status"] == "completed"
                        and not page["empty"]
                    ):
                        duplicates.setdefault(page["body_sha256"], []).append(
                            page["page"]
                        )
                document["identical_page_groups"] = [
                    v for v in duplicates.values() if len(v) > 1
                ]
                document["output_bytes"] = sum(
                    p.stat().st_size for p in folder.rglob("*") if p.is_file()
                )
        result["status"] = (
            "completed"
            if not any(
                p["status"] == "failed" or p.get("json_error") for p in result["pages"]
            )
            else "partial"
        )
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        (args.output / "error.txt").write_text(traceback.format_exc())
    finally:
        result["blocked_network_attempts"] = len(attempts)
        result["seconds"] = time.perf_counter() - started
        result["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
            1024**2 if sys.platform == "darwin" else 1024
        )
        result["passed"] = sum(s["passed"] for s in result["samples"])
        result["total"] = sum(s["total"] for s in result["samples"])
        dump(args.output / "results.json", result)
        dump(
            args.output / "errors.json",
            [
                {"sample": s["id"], **c}
                for s in result["samples"]
                for c in s["checks"]
                if not c["passed"]
            ],
        )
    print(
        f"{args.mode}/{args.scope}: {result['status']}; {result['passed']}/{result['total']}; {result['seconds']:.1f}s",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=["samples", "full", "all"], default="all")
    parser.add_argument("--mode", choices=MODES + ["all"], default="all")
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "data/pdfs")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sources", default="maotai,midea", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.worker and args.scope == "full" and args.mode == "text":
        parser.error("text模式仅用于六页小样；全文请选择layout或lines_strict")
    if args.worker:
        worker(args)
        return
    output = args.output or ROOT / "data/pymupdf-experiment" / datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture_sha256": checksum(FIXTURE),
        "runs": [],
    }
    dump(output / "run.json", manifest)
    scopes = ["samples", "full"] if args.scope == "all" else [args.scope]
    modes = MODES if args.mode == "all" else [args.mode]
    for scope in scopes:
        for mode in modes:
            if scope == "full" and mode == "text":
                continue  # Full-document comparison is layout vs strict lines; text only on the six-page set.
            sources = ["maotai,midea"] if scope == "samples" else ["maotai", "midea"]
            for source in sources:
                folder = f"{scope}-{mode}" + (
                    "" if scope == "samples" else "-" + source
                )
                command = [
                    sys.executable,
                    __file__,
                    "--worker",
                    "--scope",
                    scope,
                    "--mode",
                    mode,
                    "--sources",
                    source,
                    "--pdf-dir",
                    str(args.pdf_dir.resolve()),
                    "--output",
                    str(output / folder),
                ]
                with (output / (folder + ".log")).open("w") as log:
                    process = subprocess.run(
                        command, stdout=log, stderr=subprocess.STDOUT
                    )
                manifest["runs"].append(
                    {"folder": folder, "exit_code": process.returncode}
                )
                dump(output / "run.json", manifest)
                print(folder, "finished", process.returncode, flush=True)
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("report_pymupdf_experiment.py")),
            str(output),
        ],
        check=True,
    )
    print(output / "REPORT.md")


if __name__ == "__main__":
    main()
