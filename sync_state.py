from contextlib import contextmanager
from pathlib import Path
import sqlite3


DB_PATH = Path(__file__).with_name("sync.db")


@contextmanager
def database():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS mappings (
                    crm_id TEXT PRIMARY KEY,
                    accounting_id TEXT NOT NULL,
                    version INTEGER NOT NULL
                )"""
            )
            yield connection
    finally:
        connection.close()


def load(crm_id):
    with database() as connection:
        row = connection.execute("SELECT * FROM mappings WHERE crm_id = ?", (crm_id,)).fetchone()
        return dict(row) if row else None


def save(crm_id, record):
    with database() as connection:
        connection.execute(
            """INSERT INTO mappings (crm_id, accounting_id, version) VALUES (?, ?, ?)
               ON CONFLICT(crm_id) DO UPDATE SET
               accounting_id = excluded.accounting_id, version = excluded.version""",
            (crm_id, record["id"], record["version"]),
        )
