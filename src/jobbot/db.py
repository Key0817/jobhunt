from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent

# Columnas agregadas después de la primera versión del esquema: (tabla, columna, definición).
# create table if not exists no modifica tablas que ya existen, así que se agregan aquí.
MIGRATIONS = [
    ("email_imports", "parser_version", "integer not null default 0"),
    ("jobs", "score_detail", "text not null default '{}'"),
    ("jobs", "last_seen_at", "timestamptz"),
    ("runs", "status", "text not null default 'unknown'"),
    ("runs", "details", "text not null default '{}'"),
    ("cv_versions", "profile_key", "text not null default ''"),
    ("cv_versions", "file_path", "text not null default ''"),
    ("cv_versions", "status", "text not null default 'pending_review'"),
    ("cv_versions", "model_name", "text"),
    ("cv_versions", "approved_at", "timestamptz"),
    ("cv_versions", "cover_letter", "text"),
    ("cv_versions", "cover_letter_model_name", "text"),
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
        """Una migración por revisión, serializada y con transacción corta en Postgres."""
        if self.is_sqlite:
            with self.transaction():
                self._migrate_schema()
            return
        import hashlib
        from time import sleep

        scripts = ("collection_schema.sql", "mail_schema.sql", "gmail_oauth_schema.sql")
        revision = hashlib.sha256((repr(MIGRATIONS) + "".join(
            (HERE / name).read_text(encoding="utf-8") for name in scripts)).encode()).hexdigest()
        for attempt in range(3):
            try:
                with self.transaction():
                    self.execute("set local lock_timeout = '5s'")
                    self.execute("select pg_advisory_xact_lock(741906271)")
                    self.execute("create table if not exists jobbot_schema_revisions (revision text primary key)")
                    if not self.query("select revision from jobbot_schema_revisions where revision = ?", (revision,)):
                        self.execute("alter table jobbot_schema_revisions enable row level security")
                        self._migrate_schema()
                        self.execute("insert into jobbot_schema_revisions (revision) values (?)", (revision,))
                return
            except Exception as exc:
                if getattr(exc, "sqlstate", None) not in {"40P01", "40001", "55P03"} or attempt == 2:
                    raise
                sleep(.5 * (attempt + 1))

    def _migrate_schema(self) -> None:
        for table, column, definition in MIGRATIONS:
            existing = self.columns(table)
            if existing and column not in existing:
                self.execute(f"alter table {table} add column {column} {definition}")
        job_columns = self.columns("jobs")
        if "id" in job_columns:
            script = (HERE / "collection_schema.sql").read_text(encoding="utf-8")
            # Execute each DDL statement in the same transaction on both engines.
            for statement in script.split(";"):
                if statement.strip():
                    self.execute(statement)
            if not self.is_sqlite:
                self._enable_rls("job_sources")
            if {"source", "external_id", "url", "detected_at"} <= job_columns:
                self.execute("update jobs set last_seen_at = detected_at where last_seen_at is null")
                self.execute(
                    "insert into job_sources (source, source_key, job_id, external_id, url, canonical_url, first_seen_at, last_seen_at) "
                    "select source, case when external_id is not null and external_id <> '' "
                    "then 'id:' || external_id else 'url:' || url end, id, external_id, url, url, detected_at, "
                    "coalesce(last_seen_at, detected_at) from jobs where true "
                    "on conflict (source, source_key) do nothing"
                )
        script = (HERE / "mail_schema.sql").read_text(encoding="utf-8")
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)
        if not self.is_sqlite:
            self._enable_rls("email_imports")
        script = (HERE / "gmail_oauth_schema.sql").read_text(encoding="utf-8")
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)
        if not self.is_sqlite:
            self._enable_rls("gmail_connections")
            self._enable_rls("gmail_oauth_states")

    def _enable_rls(self, table: str) -> None:
        rows = self.query("select relrowsecurity from pg_class where oid = to_regclass(?)", (table,))
        if rows and not rows[0]["relrowsecurity"]:
            self.execute(f"alter table {table} enable row level security")

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
