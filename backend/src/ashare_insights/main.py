from contextlib import asynccontextmanager
from fastapi import FastAPI
from ashare_insights.api.router import router
from ashare_insights.config import Settings, get_settings
from ashare_insights.db.session import make_session_factory
from ashare_insights.providers.cninfo import CninfoProvider
from ashare_insights.services.catalog import CatalogService
from ashare_insights.services.downloads import DownloadManager
from ashare_insights.rag.embedding import LocalEmbeddingProvider
from ashare_insights.services.indexing import IndexManager
from ashare_insights.services.rag import RagService


def create_app(
    settings: Settings | None = None, provider=None, embedding=None, gateway=None
) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine, sessions = make_session_factory(settings.database_url)
    provider = provider or CninfoProvider(settings.request_timeout)
    downloads = DownloadManager(sessions, provider.client, settings.data_dir)
    embedding = embedding or LocalEmbeddingProvider(
        settings.model_path, settings.embedding_batch_size
    )
    indexer = IndexManager(sessions, embedding, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        downloads.start()
        indexer.start()
        try:
            yield
        finally:
            indexer.stop()
            downloads.stop()
            provider.close()
            engine.dispose()

    app = FastAPI(title="A-Share Insights", version="1.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.sessions = sessions
    app.state.catalog = CatalogService(sessions, provider)
    app.state.downloads = downloads
    app.state.indexer = indexer
    app.state.rag = RagService(sessions, embedding, indexer, settings, gateway)
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
