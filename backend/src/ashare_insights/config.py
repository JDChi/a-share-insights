from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    request_timeout: float = 30.0

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'insights.db'}"


def get_settings() -> Settings:
    default = Path(__file__).resolve().parents[3] / "data"
    return Settings(
        data_dir=Path(os.getenv("ASHARE_DATA_DIR", str(default))).expanduser().resolve(),
        host=os.getenv("ASHARE_HOST", "127.0.0.1"),
        port=int(os.getenv("ASHARE_PORT", "8000")),
        request_timeout=float(os.getenv("ASHARE_REQUEST_TIMEOUT", "30")),
    )
