"""SQLite is used here solely as a legacy import fixture, never as a runtime DB."""

import hashlib
import sqlite3

import pytest
from sqlalchemy import Integer, func, select

from ashare_insights.db.import_sqlite import TABLES, import_sqlite
from ashare_insights.db.models import Base, Company, utc_now
from ashare_insights.db.session import make_session_factory


def legacy_source(path, bad_foreign_key=False):
    with sqlite3.connect(path) as connection:
        for name in TABLES:
            table = Base.metadata.tables[name]
            fields = ",".join(
                '"'
                + c.name
                + '" '
                + ("INTEGER" if isinstance(c.type, Integer) else "TEXT")
                for c in table.c
            )
            connection.execute(f'CREATE TABLE "{name}" ({fields})')
        connection.execute(
            "INSERT INTO companies VALUES (?,?,?,?,?,?)",
            (7, "000001", "样本公司", "org1", "szse", "2025-01-01 00:00:00"),
        )
        if bad_foreign_key:
            connection.execute(
                "INSERT INTO announcements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    1,
                    999,
                    "fixture",
                    "1",
                    2024,
                    "年度报告",
                    "年度报告",
                    None,
                    "2025-01-01",
                    "https://example.com/a.pdf",
                    "{}",
                    "2025-01-01 00:00:00",
                    None,
                ),
            )


def test_import_is_read_only_verified_and_refuses_nonempty(pg_url, tmp_path):
    path = tmp_path / "legacy.db"
    legacy_source(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    engine, sessions = make_session_factory(pg_url)
    try:
        counts = import_sqlite(path, sessions)
        assert counts["companies"] == 1 and counts["announcements"] == 0
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        with sessions.begin() as db:
            assert db.get(Company, 7).code == "000001"
            row = Company(
                code="000002",
                name="新公司",
                org_id="org2",
                category="szse",
                directory_fetched_at=utc_now(),
            )
            db.add(row)
            db.flush()
            assert row.id > 7
        with pytest.raises(ValueError, match="非空"):
            import_sqlite(path, sessions)
    finally:
        engine.dispose()


def test_import_rolls_back_on_invalid_fk(pg_url, tmp_path):
    path = tmp_path / "bad.db"
    legacy_source(path, True)
    engine, sessions = make_session_factory(pg_url)
    try:
        with pytest.raises(Exception):
            import_sqlite(path, sessions)
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(Company)) == 0
    finally:
        engine.dispose()
