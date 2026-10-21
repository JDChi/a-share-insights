import logging
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from uuid import uuid4

from sqlalchemy import delete, func, select, update

from ashare_insights.db.models import (
    Announcement,
    EmbeddingProfile,
    File,
    IndexGeneration,
    IndexTask,
    RagChunk,
    utc_now,
)
from ashare_insights.rag.embedding import fingerprint, llama_embedding
from ashare_insights.rag.text import (
    PARSER_VERSION,
    SPLITTER_VERSION,
    parse_pdf,
    split_text,
)
from ashare_insights.rag.vector_store import ScopedPgVectorStore

logger = logging.getLogger(__name__)


class RagConflict(Exception):
    pass


class RagUnavailable(Exception):
    pass


class IndexManager:
    def __init__(self, sessions, provider, settings):
        self.sessions, self.provider, self.settings = sessions, provider, settings
        self.queue = Queue()
        self.lock = Lock()
        self.thread = None

    @property
    def pipeline(self):
        return dict(
            parser=PARSER_VERSION,
            splitter=SPLITTER_VERSION,
            chunk_size=self.settings.chunk_size,
            overlap=self.settings.chunk_overlap,
            embedding_profile=self.provider.profile_id,
        )

    @property
    def pipeline_key(self):
        return fingerprint(self.pipeline)

    def start(self):
        with self.sessions.begin() as db:
            for t in db.scalars(select(IndexTask).where(IndexTask.status == "running")):
                t.status, t.progress, t.error = "queued", 0, None
                t.updated_at = utc_now()
                db.execute(
                    delete(RagChunk).where(RagChunk.generation_id == t.generation_id)
                )
            queued = list(
                db.scalars(
                    select(IndexTask.id)
                    .where(IndexTask.status == "queued")
                    .order_by(IndexTask.id)
                )
            )
        self.thread = Thread(target=self._run, name="rag-index-worker", daemon=True)
        self.thread.start()
        for task_id in queued:
            self.queue.put(task_id)

    def stop(self):
        self.queue.put(None)
        if self.thread:
            # Do not dispose the shared DB engine underneath an in-flight indexing transaction.
            self.thread.join()

    def request(self, report_id, force=False):
        try:
            descriptor, key = self.provider.descriptor, self.pipeline_key
        except (OSError, ValueError) as exc:
            raise RagUnavailable(str(exc)) from exc
        with self.lock, self.sessions.begin() as db:
            report = db.get(Announcement, report_id)
            if report is None:
                raise LookupError("公告不存在")
            if not report.file or not Path(report.file.path).is_file():
                raise RagConflict("PDF 尚未下载")
            profile_id = self.provider.profile_id
            if db.get(EmbeddingProfile, profile_id) is None:
                db.add(
                    EmbeddingProfile(
                        id=profile_id,
                        descriptor=descriptor,
                        dimension=descriptor["dimension"],
                    )
                )
                db.flush()
            existing = db.scalar(
                select(IndexTask)
                .join(IndexGeneration, IndexTask.generation_id == IndexGeneration.id)
                .where(
                    IndexGeneration.file_id == report.file_id,
                    IndexGeneration.pipeline_key == key,
                    IndexTask.status.in_(["queued", "running"]),
                )
            )
            if existing:
                return self._serialize(existing, db)
            active = db.scalar(
                select(IndexGeneration).where(
                    IndexGeneration.file_id == report.file_id,
                    IndexGeneration.pipeline_key == key,
                    IndexGeneration.active.is_(True),
                )
            )
            if active and not force:
                return self._serialize(
                    db.scalar(
                        select(IndexTask).where(IndexTask.generation_id == active.id)
                    ),
                    db,
                )
            generation = IndexGeneration(
                id=str(uuid4()),
                file_id=report.file_id,
                profile_id=profile_id,
                pipeline_key=key,
                active=False,
                status="building",
                warnings=[],
            )
            db.add(generation)
            db.flush()
            task = IndexTask(
                announcement_id=report_id,
                generation_id=generation.id,
                status="queued",
                progress=0,
                attempts=0,
            )
            db.add(task)
            db.flush()
            result = self._serialize(task, db)
        self.queue.put(result["id"])
        return result

    def get(self, task_id):
        with self.sessions() as db:
            task = db.get(IndexTask, task_id)
            if task is None:
                raise LookupError("索引任务不存在")
            return self._serialize(task, db)

    def _serialize(self, task, db):
        generation = db.get(IndexGeneration, task.generation_id)
        return dict(
            id=task.id,
            announcement_id=task.announcement_id,
            generation_id=task.generation_id,
            status=task.status,
            error=task.error,
            progress=task.progress,
            attempts=task.attempts,
            updated_at=task.updated_at,
            warnings=generation.warnings,
            active=generation.active,
        )

    def _run(self):
        while True:
            task_id = self.queue.get()
            try:
                if task_id is None:
                    return
                self._process(task_id)
            except Exception as exc:
                logger.error("索引任务 %s 失败 (%s)", task_id, type(exc).__name__)
                with self.sessions.begin() as db:
                    task = db.get(IndexTask, task_id)
                    if task:
                        task.status, task.error, task.updated_at = (
                            "failed",
                            str(exc)[:1000],
                            utc_now(),
                        )
                        generation = db.get(IndexGeneration, task.generation_id)
                        generation.status = "failed"
                        db.execute(
                            delete(RagChunk).where(
                                RagChunk.generation_id == generation.id
                            )
                        )
            finally:
                self.queue.task_done()

    def _process(self, task_id):
        with self.sessions.begin() as db:
            task = db.get(IndexTask, task_id)
            if task is None or task.status != "queued":
                return
            task.status, task.error, task.updated_at = "running", None, utc_now()
            task.attempts += 1
            gen = db.get(IndexGeneration, task.generation_id)
            if gen.pipeline_key != self.pipeline_key:
                raise ValueError("模型或切块配置已变更，请重新请求索引")
            file = db.get(File, gen.file_id)
            path, generation_id, dimension = (
                Path(file.path),
                gen.id,
                self.provider.descriptor["dimension"],
            )
            expected_sha = file.sha256
            db.execute(delete(RagChunk).where(RagChunk.generation_id == generation_id))
        import hashlib

        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha:
                raise ValueError("PDF 文件校验失败，内容与文件SHA不一致")
        parsed = parse_pdf(path)
        chunks = split_text(
            parsed, self.provider, self.settings.chunk_size, self.settings.chunk_overlap
        )
        with self.sessions.begin() as db:
            gen = db.get(IndexGeneration, generation_id)
            gen.canonical_text, gen.pages, gen.warnings = (
                parsed.text,
                parsed.pages,
                parsed.warnings,
            )
        from llama_index.core import StorageContext, VectorStoreIndex
        from llama_index.core.schema import TextNode

        nodes = [
            TextNode(
                id_=str(uuid4()),
                text=c["text"],
                metadata={k: c[k] for k in ["start", "end", "page"]},
                excluded_embed_metadata_keys=["start", "end", "page"],
                excluded_llm_metadata_keys=["start", "end", "page"],
            )
            for c in chunks
        ]
        store = ScopedPgVectorStore(
            self.sessions, generation_id, dimension, task_id, len(nodes)
        )
        VectorStoreIndex(
            nodes,
            storage_context=StorageContext.from_defaults(vector_store=store),
            embed_model=llama_embedding(self.provider),
            insert_batch_size=32,
        )
        with self.sessions.begin() as db:
            if db.scalar(
                select(func.count())
                .select_from(RagChunk)
                .where(RagChunk.generation_id == generation_id)
            ) != len(nodes):
                raise ValueError("索引写入不完整")
            gen = db.get(IndexGeneration, generation_id)
            db.execute(
                update(IndexGeneration)
                .where(
                    IndexGeneration.file_id == gen.file_id,
                    IndexGeneration.pipeline_key == gen.pipeline_key,
                    IndexGeneration.active.is_(True),
                )
                .values(active=False, status="inactive")
            )
            db.flush()
            gen.active, gen.status = True, "active"
            task = db.get(IndexTask, task_id)
            task.status, task.progress, task.updated_at = "completed", 1, utc_now()
