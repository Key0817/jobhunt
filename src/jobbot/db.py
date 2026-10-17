from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent

# Columnas agregadas después de la primera versión del esquema: (tabla, columna, definición).
# create table if not exists no modifica tablas que ya existen, así que se agregan aquí.
MIGRATIONS = [
    ("jobs", "score_detail", "text not null default '{}'"),
]

# sqlite3 ya no recomienda su adaptador de datetime por defecto (Python 3.12+).
sqlite3.register_adapter(datetime, lambda d: d.isoformat())


class Database:
    """Capa mínima sobre psycopg (Supabase/Postgres) o sqlite3 (desarrollo y tests).

    Las consultas se escriben con marcadores '?'. No uses el carácter '%' dentro del SQL.
    """

    def __init__(self, url: str):
        self.is_sqlite = url.startswith("sqlite:")
        if self.is_sqlite:
            path = url.removeprefix("sqlite:///").removeprefix("sqlite://") or ":memory:"
            # check_same_thread=False: FastAPI atiende cada petición en un hilo distinto.
            self.conn = sqlite3.connect(path, check_same_thread=False)
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

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        if self.is_sqlite:
            self.conn.executemany(sql, rows)
        else:
            with self.conn.cursor() as cur:
                cur.executemany(self._sql(sql), rows)

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

    def columns(self, table: str) -> set[str]:
        """Columnas que existen hoy en la tabla (vacío si la tabla no existe)."""
        if self.is_sqlite:
            return {row["name"] for row in self.query(f"pragma table_info({table})")}
        rows = self.query(
            "select column_name from information_schema.columns "
            "where table_schema = current_schema() and table_name = ?", (table,))
        return {row["column_name"] for row in rows}

    def migrate(self) -> None:
        """Agrega las columnas que falten. Es seguro llamarlo todas las veces."""
        for table, column, definition in MIGRATIONS:
            existing = self.columns(table)
            if existing and column not in existing:
                with self.transaction():
                    self.execute(f"alter table {table} add column {column} {definition}")
        self.conn.commit()

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
        self.migrate()

    def close(self) -> None:
        self.conn.close()