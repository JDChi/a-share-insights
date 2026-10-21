from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    VectorStoreQuery,
    VectorStoreQueryResult,
)
from pydantic import PrivateAttr
from sqlalchemy import select

from ashare_insights.db.models import IndexGeneration, IndexTask, RagChunk, utc_now


class ScopedPgVectorStore(BasePydanticVectorStore):
    """A LlamaIndex store scoped to an immutable file/profile/generation.

    All data, including text and offsets, is PostgreSQL-backed. The temporary
    LlamaIndex docstore is never the source of truth after a restart.
    """

    stores_text: bool = True
    _sessions: object = PrivateAttr()
    _generation: str = PrivateAttr()
    _dimension: int = PrivateAttr()
    _task_id: int | None = PrivateAttr(default=None)
    _total: int = PrivateAttr(default=0)
    _added: int = PrivateAttr(default=0)

    def __init__(self, sessions, generation, dimension, task_id=None, total=0):
        super().__init__()
        self._sessions, self._generation, self._dimension = (
            sessions,
            generation,
            dimension,
        )
        self._task_id, self._total = task_id, total

    @property
    def client(self):
        return self._sessions

    def add(self, nodes, **kwargs):
        with self._sessions.begin() as db:
            generation = db.get(IndexGeneration, self._generation)
            if not generation or generation.status != "building":
                raise ValueError("只允许写入正在构建的索引")
            for node in nodes:
                db.add(
                    RagChunk(
                        id=node.node_id,
                        generation_id=self._generation,
                        text=node.text,
                        start=node.metadata["start"],
                        end=node.metadata["end"],
                        page=node.metadata["page"],
                        dimension=self._dimension,
                        embedding=node.get_embedding(),
                    )
                )
            self._added += len(nodes)
            if self._task_id:
                task = db.get(IndexTask, self._task_id)
                task.progress = min(
                    0.95, 0.1 + 0.85 * self._added / max(1, self._total)
                )
                task.updated_at = utc_now()
        return [n.node_id for n in nodes]

    def delete(self, ref_doc_id, **kwargs):
        raise NotImplementedError("索引仅通过任务代次替换，禁止跨代次删除")

    def query(self, query: VectorStoreQuery, **kwargs):
        if (
            query.query_embedding is None
            or len(query.query_embedding) != self._dimension
        ):
            raise ValueError("查询向量维度不匹配")
        distance = RagChunk.embedding.cosine_distance(query.query_embedding).label(
            "distance"
        )
        with self._sessions() as db:
            generation = db.get(IndexGeneration, self._generation)
            if generation is None or not generation.active:
                raise ValueError("索引代次已失效，请重试检索")
            # Mandatory immutable generation filter precedes distance ordering.
            rows = db.execute(
                select(RagChunk, distance)
                .where(
                    RagChunk.generation_id == self._generation,
                    RagChunk.dimension == self._dimension,
                )
                .order_by(distance, RagChunk.start)
                .limit(query.similarity_top_k)
            ).all()
            nodes = [
                TextNode(
                    id_=c.id,
                    text=c.text,
                    metadata=dict(start=c.start, end=c.end, page=c.page),
                )
                for c, _ in rows
            ]
            return VectorStoreQueryResult(
                nodes=nodes,
                similarities=[1 - float(d) for _, d in rows],
                ids=[c.id for c, _ in rows],
            )
