from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


def make_session_factory(database_url: str):
    if not database_url or make_url(database_url).get_backend_name() != "postgresql":
        raise ValueError(
            "ASHARE_DATABASE_URL 必须配置 PostgreSQL；SQLite 仅用于一次性导入"
        )
    engine = create_engine(database_url, pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)
