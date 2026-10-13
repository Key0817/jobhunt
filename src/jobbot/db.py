from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent

sqlite3.register_adapter(datetime, lambda d: d.isoformat())


class Database:
    """Capa mínima sobre psycopg (Supabase/Postgres) o sqlite3 (desarrollo y tests).

    Las consultas se escriben con marcadores '?'. No uses el carácter '%' dentro del SQL.
    """

    def __init__(self, url: str):
        self.is_sqlite = url.startswith("sqlite:")
        if self.is_sqlite:
            path = url.removeprefix("sqlite:///").removeprefix("sqlite://") or ":memory:"
            self.conn = sqlite3.connect(path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("pragma foreign_keys = on")
        else:
            import psycopg
            from psycopg.rows import dict_row

            self.conn = psycopg.connect(url, row_factory=dict_row)

    def _sql(self, sql: str) -> str:
        return sql if self.is_sqlite else sql.replace("?", "%s")

    def execute(self, sql: str, params: tuple = ()):
        return self.conn.execute(self._sql(sql), params)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in self.execute(sql, params).fetchall()]

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def init_schema(self) -> None:
        scripts = [(HERE / "schema.sql").read_text(encoding="utf-8")]
        if not self.is_sqlite:
            scripts.append((HERE / "rls.sql").read_text(encoding="utf-8"))
        for script in scripts:
            if self.is_sqlite:
                self.conn.executescript(script)
            else:
                self.conn.execute(script)
                self.conn.commit()

    def close(self) -> None:
        self.conn.close()