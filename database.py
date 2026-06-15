import sqlite3
import os

DB_PATH = os.environ.get(
    'DB_PATH',
    os.path.join(os.path.dirname(__file__), 'memory_diary.db')
)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    # 出来事メモ（逐語でそのまま保存する「正本」）
    c.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,   -- 'YYYY-MM-DD HH:MM'（JST）
            entry_date TEXT NOT NULL,   -- 'YYYY-MM-DD'（JST、当日抽出用）
            text TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
