"""One-time read-only legacy import. Runtime and tests do not support SQLite."""

import argparse
import sqlite3
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import Date, DateTime, func, select, text

from ashare_insights.config import get_settings
from ashare_insights.db.models import Base
from ashare_insights.db.session import make_session_factory

TABLES = ["companies", "files", "announcements", "download_tasks", "cache_entries"]


def import_sqlite(source: Path, sessions):
    source = source.resolve()
    if not source.is_file():
        raise ValueError("SQLite 来源文件不存在")
    connection = sqlite3.connect(
        "file:" + quote(str(source), safe="/") + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    counts = {}
    try:
        connection.execute("BEGIN")
        with sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock(9184711)"))
            for table in Base.metadata.sorted_tables:
                if db.scalar(select(func.count()).select_from(table)):
                    raise ValueError("目标业务或RAG表非空，拒绝导入")
            for name in TABLES:
                table = Base.metadata.tables[name]
                rows = []
                for raw in connection.execute(f'SELECT * FROM "{name}"'):
                    row = dict(raw)
                    if set(row) != set(table.c.keys()):
                        raise ValueError(f"{name} 来源字段与迁移结构不一致")
                    for column in table.c:
                        if row[column.name] is not None:
                            if isinstance(column.type, DateTime):
                                row[column.name] = datetime.fromisoformat(
                                    row[column.name]
                                )
                            elif isinstance(column.type, Date):
                                row[column.name] = date.fromisoformat(row[column.name])
                    rows.append(row)
                if rows:
                    db.execute(table.insert(), rows)
                actual = [dict(r) for r in db.execute(select(table)).mappings()]
                pk = list(table.primary_key.columns)[0].name
                if sorted(rows, key=lambda r: r[pk]) != sorted(
                    actual, key=lambda r: r[pk]
                ):
                    raise ValueError(f"{name} 字段或记录校验失败")
                counts[name] = len(rows)
                if "id" in table.c:
                    db.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{name}','id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {name}"
                        )
                    )
        return counts
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    engine, sessions = make_session_factory(get_settings().database_url)
    try:
        print(import_sqlite(args.source, sessions))
    finally:
        engine.dispose()
