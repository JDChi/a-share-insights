"""Download only the pinned Heron + TableFormer accurate artifacts for this experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
MODELS = [
    ("docling-project/docling-layout-heron", "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8", {
        "config.json": (3268, None),
        "preprocessor_config.json": (444, None),
        "model.safetensors": (171658996, "00333a43451945aaf89db8ca9c0a17e75d1537c17db60fdb91aa95f4c7929e0c"),
    }),
    ("docling-project/docling-models", "2199320848bb9a8a519d22e4b528185a4f9a6f64", {
        "model_artifacts/tableformer/accurate/tm_config.json": (7060, None),
        "model_artifacts/tableformer/accurate/tableformer_accurate.safetensors":
            (212758388, "2a7d6c924b3cd12fb99a09280ca9c33a89c5d60b93253617d2e088c1a40374d9"),
    }),
]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(root):
    root.mkdir(parents=True, exist_ok=True)
    manifest = []
    for repo, revision, files in MODELS:
        for name, (size, expected) in files.items():
            dest = root / repo.replace("/", "--") / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
            if not (dest.is_file() and dest.stat().st_size == size and (not expected or sha256(dest) == expected)):
                pending = dest.with_suffix(dest.suffix + ".part")
                for host in ["https://hf-mirror.com", "https://huggingface.co"]:
                    route = url.replace("https://huggingface.co", host)
                    print(f"Downloading {repo}/{name}", flush=True)
                    command = ["curl", "--fail", "--location", "--silent", "--show-error",
                               "--connect-timeout", "15", "--max-time", "240", "--retry", "1",
                               "--output", str(pending), route]
                    if "hf-mirror.com" in host:
                        command[1:1] = ["--noproxy", "*"]
                    result = subprocess.run(command)
                    if result.returncode == 0 and pending.stat().st_size == size and (not expected or sha256(pending) == expected):
                        pending.replace(dest)
                        break
                else:
                    raise RuntimeError(f"Download/size/hash verification failed: {name}")
            manifest.append({"repo": repo, "revision": revision, "file": name,
                             "path": str(dest.relative_to(root)), "size_bytes": size,
                             "sha256": sha256(dest), "official_url": url})
            print(f"Verified {name}: {size} bytes", flush=True)
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/models/docling")
    download(parser.parse_args().output.resolve())
