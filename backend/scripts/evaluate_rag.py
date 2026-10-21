"""Evaluate an existing active index; no implicit indexing or PDF downloads."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from ashare_insights.config import get_settings
from ashare_insights.db.session import make_session_factory
from ashare_insights.rag.embedding import LocalEmbeddingProvider
from ashare_insights.rag.evaluation import evaluate
from ashare_insights.services.indexing import IndexManager
from ashare_insights.services.rag import RagService


class ApiRetrievalService(RagService):
    """Evaluate the running local backend without loading a second Large instance."""

    def __init__(self, *args, api_url, **kwargs):
        super().__init__(*args, **kwargs)
        import httpx

        self.client = httpx.Client(base_url=api_url, trust_env=False, timeout=120)

    def retrieve(self, report_id, question, top_k=5):
        response = self.client.post(
            f"/api/v1/reports/{report_id}/retrieve",
            json=dict(question=question, top_k=top_k),
        )
        response.raise_for_status()
        return response.json()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-id", type=int, required=True)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "evaluations/maotai-2024.json",
    )
    parser.add_argument("--ragas", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--api-url",
        help="Use a running backend for retrieval; avoids a second Large instance",
    )
    args = parser.parse_args()
    settings = get_settings()
    engine, sessions = make_session_factory(settings.database_url)
    provider = LocalEmbeddingProvider(
        settings.model_path, settings.embedding_batch_size
    )
    indexer = IndexManager(sessions, provider, settings)
    service = (
        ApiRetrievalService(sessions, provider, indexer, settings, api_url=args.api_url)
        if args.api_url
        else RagService(sessions, provider, indexer, settings)
    )
    output = args.output or settings.data_dir / "evaluations" / datetime.now(
        UTC
    ).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        result = evaluate(service, args.report_id, args.fixture, output, args.ragas)
        print(result["summary"])
        print(output / "report.html")
    finally:
        if args.api_url:
            service.client.close()
        engine.dispose()
