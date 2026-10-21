import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str = field(default="", repr=False)
    host: str = "127.0.0.1"
    port: int = 8000
    request_timeout: float = 30.0
    embedding_model_path: Path | None = None
    embedding_batch_size: int = 4
    chunk_size: int = 384
    chunk_overlap: int = 64
    llm_base_url: str = "https://api.minimax.cn/v1"
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_timeout: float = 90.0
    eval_base_url: str = ""
    eval_model: str = ""
    eval_api_key: str = field(default="", repr=False)

    @property
    def model_path(self):
        return self.embedding_model_path or self.data_dir / "models/bge-large-zh-v1.5"


def get_settings() -> Settings:
    load_dotenv(ROOT / "backend/.env", override=False)
    data = Path(os.getenv("ASHARE_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
    return Settings(
        data_dir=data,
        database_url=os.getenv("ASHARE_DATABASE_URL", ""),
        host=os.getenv("ASHARE_HOST", "127.0.0.1"),
        port=int(os.getenv("ASHARE_PORT", "8000")),
        request_timeout=float(os.getenv("ASHARE_REQUEST_TIMEOUT", "30")),
        embedding_model_path=Path(
            os.getenv(
                "ASHARE_EMBEDDING_MODEL_PATH", str(data / "models/bge-large-zh-v1.5")
            )
        )
        .expanduser()
        .resolve(),
        embedding_batch_size=int(os.getenv("ASHARE_EMBEDDING_BATCH_SIZE", "4")),
        chunk_size=int(os.getenv("ASHARE_CHUNK_SIZE", "384")),
        chunk_overlap=int(os.getenv("ASHARE_CHUNK_OVERLAP", "64")),
        llm_base_url=os.getenv("ASHARE_LLM_BASE_URL", "https://api.minimax.cn/v1"),
        llm_model=os.getenv("ASHARE_LLM_MODEL", ""),
        llm_api_key=os.getenv("ASHARE_LLM_API_KEY", ""),
        llm_timeout=float(os.getenv("ASHARE_LLM_TIMEOUT", "90")),
        eval_base_url=os.getenv("ASHARE_EVAL_BASE_URL", ""),
        eval_model=os.getenv("ASHARE_EVAL_MODEL", ""),
        eval_api_key=os.getenv("ASHARE_EVAL_API_KEY", ""),
    )
