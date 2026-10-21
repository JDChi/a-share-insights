"""Run pinned local Docling on six full PDF pages; no manual parsing boundaries.

Use the isolated experiment environment described in README-table-experiment.md.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import socket
import sys
import time

from table_checks import check_cell

ROOT = Path(__file__).resolve().parents[2]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def overlap(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - area
    return area / union if union > 0 else 0


def raw_rows(data):
    rows = [[None for _ in range(data.num_cols)] for _ in range(data.num_rows)]
    for cell in data.table_cells:
        rows[cell.start_row_offset_idx][cell.start_col_offset_idx] = cell.text
    return rows


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.models / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for item in manifest:
        p = args.models / item["path"]
        with p.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == item["sha256"], p
    spec_path = Path(__file__).with_name("table_samples.json")
    spec = json.loads(spec_path.read_text())
    paths = {}
    for key, source in spec["sources"].items():
        p = args.pdf_dir / (source["sha256"] + ".pdf")
        assert hashlib.sha256(p.read_bytes()).hexdigest() == source["sha256"], p
        paths[key] = p
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    blocked_attempts = []
    def deny_network(*a, **kw):
        blocked_attempts.append("network_connection_attempt")
        raise RuntimeError("Network disabled during Docling inference")
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
    from docling.document_converter import DocumentConverter, PdfFormatOption
    import torch
    torch.set_num_threads(4)
    options = PdfPipelineOptions(artifacts_path=args.models.resolve())
    options.do_ocr = False
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    options.table_structure_options.do_cell_matching = args.cell_matching == "true"
    options.do_picture_classification = False
    options.do_picture_description = False
    options.do_code_enrichment = False
    options.do_formula_enrichment = False
    options.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU)
    converter = DocumentConverter(allowed_formats=[InputFormat.PDF], format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    results = {"versions": {}, "python": platform.python_version(), "torch_path": torch.__file__,
               "fixture_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
               "model_manifest": manifest, "pipeline_options": options.model_dump(mode="json"),
               "cell_matching": args.cell_matching, "ocr": False,
               "device": "cpu", "network_blocked": True,
               "evaluation_policy": "Full page parsing. Fixture bboxes used ONLY to match detected tables to annotated regions after conversion. No manual column boundaries or header substitutions.",
               "pages": [], "samples": []}
    for name in ["docling-slim", "docling-core", "docling-ibm-models", "docling-parse", "torch", "torchvision", "transformers", "opencv-python-headless"]:
        results["versions"][name] = importlib.metadata.version(name)
    started = time.perf_counter()
    for key in paths:
        for number in sorted({s["page"] for s in spec["samples"] if s["source"] == key}):
            page_dir = args.output / f"{key}-{number}"
            page_dir.mkdir(exist_ok=True)
            print(f"Converting {key} physical page {number}, cell_matching={args.cell_matching}", flush=True)
            begin = time.perf_counter()
            result = converter.convert(paths[key], page_range=(number, number))
            doc = result.document
            doc.save_as_json(page_dir / "document.json")
            doc.save_as_markdown(page_dir / "document.md")
            tables = []
            for i, table in enumerate(doc.tables):
                prov = table.prov[0]
                box = prov.bbox.to_top_left_origin(doc.pages[prov.page_no].size.height)
                tables.append({"table_index": i, "page_no": prov.page_no, "bbox": [box.l, box.t, box.r, box.b],
                               "num_rows": table.data.num_rows, "num_cols": table.data.num_cols,
                               "rows": raw_rows(table.data), "raw": table.model_dump(mode="json")})
                (page_dir / f"table-{i}.md").write_text(table.export_to_markdown(doc=doc) + "\n")
            dump(page_dir / "tables.json", tables)
            from PIL import Image, ImageDraw
            original = ROOT / f"data/table-experiment/{key}-{number}/page.png"
            if original.is_file():
                with Image.open(original) as img:
                    overlay = img.convert("RGB")
                draw = ImageDraw.Draw(overlay)
                page_size = next(iter(doc.pages.values())).size
                sx, sy = overlay.width / page_size.width, overlay.height / page_size.height
                for table in tables:
                    l,t,r,b = table["bbox"]
                    draw.rectangle((l*sx,t*sy,r*sx,b*sy),outline="red",width=3)
                    draw.text((l*sx,t*sy),f'Table {table["table_index"]}',fill="red")
                overlay.save(page_dir / "detected-tables.png")
            page_info = {"source": key, "page": number, "seconds": round(time.perf_counter()-begin,3),
                         "status": str(result.status), "table_count": len(tables)}
            results["pages"].append(page_info)
            for s in [s for s in spec["samples"] if s["source"] == key and s["page"] == number]:
                ranked = sorted(tables, key=lambda t: overlap(t["bbox"],s["bbox"]), reverse=True)
                selected = ranked[:1] if ranked and overlap(ranked[0]["bbox"],s["bbox"]) >= .2 else []
                checks = [check_cell(selected, c) for c in s["checks"]]
                record = {"id": s["id"], "source": key, "page": number,
                          "matched_table": selected[0]["table_index"] if selected else None,
                          "matching_iou": overlap(selected[0]["bbox"],s["bbox"]) if selected else 0,
                          "checks": checks, "passed": sum(c["passed"] for c in checks), "total": len(checks)}
                results["samples"].append(record)
                print(f'{s["id"]}: {record["passed"]}/{record["total"]}', flush=True)
            results["blocked_network_attempts"] = len(blocked_attempts)
            results["seconds"] = round(time.perf_counter()-started,3)
            results["peak_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2 if sys.platform == "darwin" else 1024),1)
            results["passed"] = sum(s["passed"] for s in results["samples"])
            results["total"] = sum(s["total"] for s in results["samples"])
            dump(args.output / "results.json", results)
    dump(args.output / "errors.json", [{"sample": s["id"], **c} for s in results["samples"] for c in s["checks"] if not c["passed"]])
    print(f'Total {results["passed"]}/{results["total"]}; {results["seconds"]} seconds; peak RSS {results["peak_rss_mib"]} MiB', flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=ROOT / "data/models/docling")
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "data/pdfs")
    parser.add_argument("--cell-matching", choices=["true","false"], default="true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.output = args.output or ROOT / f"data/docling-experiment/cell-matching-{args.cell_matching}"
    run(args)
