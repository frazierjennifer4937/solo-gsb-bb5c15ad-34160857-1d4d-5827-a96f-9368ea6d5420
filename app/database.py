"""SQLite 访问层（标准库 sqlite3，线程安全的每请求连接）。"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("SEAT_DB", "/data/seatarrange.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sections (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 0  -- 0 表示不限
);

CREATE TABLE IF NOT EXISTS tables (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    capacity   INTEGER NOT NULL CHECK (capacity > 0),
    section_id INTEGER REFERENCES sections(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS participants (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    note  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type TEXT NOT NULL CHECK (rule_type IN ('must_together','must_not','fixed')),
    pids      TEXT NOT NULL DEFAULT '[]',   -- JSON: [pid,...]（together/not）
    pid       INTEGER REFERENCES participants(id) ON DELETE CASCADE,  -- fixed
    tid       INTEGER REFERENCES tables(id) ON DELETE CASCADE,        -- fixed
    note      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS assignments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id INTEGER NOT NULL UNIQUE REFERENCES participants(id) ON DELETE CASCADE,
    table_id       INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    locked         INTEGER NOT NULL DEFAULT 0
);
"""


def _connect() -> sqlite3.Connection:
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    """清空全部业务数据（演示数据加载前使用）。"""
    with get_conn() as conn:
        conn.executescript(
            """
            DELETE FROM assignments;
            DELETE FROM rules;
            DELETE FROM participants;
            DELETE FROM tables;
            DELETE FROM sections;
            DELETE FROM sqlite_sequence;
            """
        )
