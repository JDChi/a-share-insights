import hashlib
import json
import math
from abc import ABC, abstractmethod
from functools import cached_property
from pathlib import Path
from threading import RLock

QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_vectors(vectors, count, dimension):
    if len(vectors) != count:
        raise ValueError("Embedding 数量不匹配")
    for vector in vectors:
        if len(vector) != dimension or not all(math.isfinite(float(x)) for x in vector):
            raise ValueError("Embedding 维度或数值无效")
        norm = math.sqrt(sum(float(x) ** 2 for x in vector))
        if abs(norm - 1) > 1e-3:
            raise ValueError("Embedding 必须归一化")
    return [[float(x) for x in v] for v in vectors]


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def descriptor(self) -> dict: ...

    @property
    def profile_id(self):
        return fingerprint(self.descriptor)

    @abstractmethod
    def document_vectors(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def query_vector(self, text: str) -> list[float]: ...

    @abstractmethod
    def offsets(self, text: str) -> list[tuple[int, int]]: ...

    @abstractmethod
    def token_count(self, text: str) -> int: ...


class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, path: Path, batch_size=4):
        self.path = path
        self.batch_size = batch_size
        if batch_size < 1:
            raise ValueError("batch_size 必须大于零")
        self.lock = RLock()
        self._model = None
        self._tokenizer = None

    @cached_property
    def descriptor(self):
        required = [
            "pytorch_model.bin",
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
            "modules.json",
            "sentence_bert_config.json",
            "1_Pooling/config.json",
        ]
        hashes = {}
        for name in required:
            p = self.path / name
            if not p.is_file():
                raise ValueError(f"本地模型缺少文件：{name}；禁止自动下载")
            with p.open("rb") as f:
                hashes[name] = hashlib.file_digest(f, "sha256").hexdigest()
        config = json.loads((self.path / "config.json").read_text())
        manifest_path = self.path / "model-manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
        return dict(
            provider="sentence-transformers-local",
            model=manifest.get("model_id", self.path.name),
            dimension=config["hidden_size"],
            max_tokens=config["max_position_embeddings"],
            hashes=hashes,
            query_prefix=QUERY_PREFIX,
            normalized=True,
            adapter_version=1,
        )

    @property
    def tokenizer(self):
        with self.lock:
            if self._tokenizer is None:
                from transformers import AutoTokenizer

                self._tokenizer = AutoTokenizer.from_pretrained(
                    str(self.path),
                    local_files_only=True,
                    trust_remote_code=False,
                    use_fast=True,
                )
            return self._tokenizer

    def offsets(self, text):
        with self.lock:
            return [
                tuple(x)
                for x in self.tokenizer(
                    text,
                    add_special_tokens=False,
                    return_offsets_mapping=True,
                    truncation=False,
                    verbose=False,
                )["offset_mapping"]
            ]

    def token_count(self, text):
        return len(self.offsets(text))

    def _encode(self, texts):
        with self.lock:
            for text in texts:
                if (
                    not text.strip()
                    or self.token_count(text) + 2 > self.descriptor["max_tokens"]
                ):
                    raise ValueError("Embedding 输入为空或超过模型上限，禁止截断")
            if self._model is None:
                import torch
                from sentence_transformers import SentenceTransformer

                torch.set_num_threads(4)
                self._model = SentenceTransformer(
                    str(self.path),
                    device="cpu",
                    local_files_only=True,
                    trust_remote_code=False,
                )
            vectors = self._model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()
            return validate_vectors(vectors, len(texts), self.descriptor["dimension"])

    def document_vectors(self, texts):
        return self._encode(texts) if texts else []

    def query_vector(self, text):
        return self._encode([QUERY_PREFIX + text])[0]


def llama_embedding(provider):
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from pydantic import PrivateAttr

    class ProviderAdapter(BaseEmbedding):
        _provider: EmbeddingProvider = PrivateAttr()

        def __init__(self):
            super().__init__(
                model_name=provider.profile_id,
                embed_batch_size=provider.batch_size
                if hasattr(provider, "batch_size")
                else 4,
            )
            self._provider = provider

        def _get_text_embedding(self, text):
            return self._provider.document_vectors([text])[0]

        def _get_text_embeddings(self, texts):
            return self._provider.document_vectors(texts)

        def _get_query_embedding(self, query):
            return self._provider.query_vector(query)

        async def _aget_query_embedding(self, query):
            import asyncio

            return await asyncio.to_thread(self._get_query_embedding, query)

    return ProviderAdapter()
