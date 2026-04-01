"""SQLite storage for the symbol index.

Uses WAL mode for concurrent read access. Single-writer model
for indexing; multiple readers for query-time lookups.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SymbolRecord:
    """A single symbol extracted from source code."""

    name: str
    qualified_name: str
    type: str  # "class" or "function"
    file_path: str  # relative to repo root
    start_line: int
    end_line: int
    signature: str  # first line of the definition
    parent_qualified_name: str  # "" if top-level


_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS symbols (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    type            TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    signature       TEXT NOT NULL DEFAULT '',
    parent_qualified_name TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols (name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified_name ON symbols (qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_file_path ON symbols (file_path);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES symbols(id),
    target_id   INTEGER NOT NULL REFERENCES symbols(id),
    edge_type   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target_id);

CREATE TABLE IF NOT EXISTS indexed_files (
    file_path       TEXT PRIMARY KEY,
    mtime           REAL NOT NULL,
    symbol_count    INTEGER NOT NULL DEFAULT 0
);
"""


class SymbolDB:
    """SQLite-backed symbol store."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def init_db(self) -> None:
        conn = self._connect()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Symbols ──────────────────────────────────────────────────────

    def insert_symbols_batch(self, symbols: list[SymbolRecord]) -> None:
        conn = self._connect()
        conn.executemany(
            """INSERT INTO symbols
               (name, qualified_name, type, file_path, start_line, end_line, signature, parent_qualified_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (s.name, s.qualified_name, s.type, s.file_path,
                 s.start_line, s.end_line, s.signature, s.parent_qualified_name)
                for s in symbols
            ],
        )
        conn.commit()

    def delete_file_symbols(self, file_path: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))
        conn.commit()

    def lookup_by_name(self, name: str) -> list[sqlite3.Row]:
        conn = self._connect()
        return conn.execute(
            "SELECT * FROM symbols WHERE name = ?", (name,)
        ).fetchall()

    def lookup_by_file(self, file_path: str) -> list[sqlite3.Row]:
        conn = self._connect()
        return conn.execute(
            "SELECT * FROM symbols WHERE file_path = ?", (file_path,)
        ).fetchall()

    def get_all_symbol_names(self) -> list[tuple[int, str, str]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, name, qualified_name FROM symbols"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def symbol_count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT count(*) FROM symbols").fetchone()[0]

    def lookup_by_id(self, symbol_id: int) -> sqlite3.Row | None:
        conn = self._connect()
        return conn.execute(
            "SELECT * FROM symbols WHERE id = ?", (symbol_id,)
        ).fetchone()

    def delete_all_edges(self) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM edges")
        conn.commit()

    # ── Indexed files (incremental) ──────────────────────────────────

    def get_file_mtime(self, file_path: str) -> float | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT mtime FROM indexed_files WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row[0] if row else None

    def get_file_symbol_count(self, file_path: str) -> int:
        conn = self._connect()
        row = conn.execute(
            "SELECT symbol_count FROM indexed_files WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row[0] if row else 0

    def upsert_file_record(self, file_path: str, mtime: float, symbol_count: int) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO indexed_files (file_path, mtime, symbol_count)
               VALUES (?, ?, ?)
               ON CONFLICT(file_path) DO UPDATE SET mtime=excluded.mtime, symbol_count=excluded.symbol_count""",
            (file_path, mtime, symbol_count),
        )
        conn.commit()

    def get_all_indexed_files(self) -> set[str]:
        conn = self._connect()
        rows = conn.execute("SELECT file_path FROM indexed_files").fetchall()
        return {r[0] for r in rows}

    def delete_file_record(self, file_path: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM indexed_files WHERE file_path = ?", (file_path,))
        conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))
        conn.commit()

    # ── Edges (stubs for Stage 2) ────────────────────────────────────

    def insert_edges_batch(self, edges: list[tuple[int, int, str]]) -> None:
        conn = self._connect()
        conn.executemany(
            "INSERT INTO edges (source_id, target_id, edge_type) VALUES (?, ?, ?)",
            edges,
        )
        conn.commit()

    def get_neighbors(self, symbol_id: int) -> list[sqlite3.Row]:
        conn = self._connect()
        return conn.execute(
            """SELECT s.* FROM symbols s
               JOIN edges e ON (e.target_id = s.id AND e.source_id = ?)
                            OR (e.source_id = s.id AND e.target_id = ?)""",
            (symbol_id, symbol_id),
        ).fetchall()

    def get_ego_graph(self, symbol_id: int, hops: int = 1) -> list[dict]:
        """Bidirectional 1-hop ego-graph query in a single SQL round-trip."""
        conn = self._connect()
        rows = conn.execute(
            """SELECT s2.id, s2.name, s2.qualified_name, s2.file_path, s2.start_line,
                      e.edge_type,
                      CASE WHEN e.source_id = ? THEN 'forward' ELSE 'backward' END as direction
               FROM edges e
               JOIN symbols s2 ON (
                   CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END = s2.id
               )
               WHERE (e.source_id = ? OR e.target_id = ?)
                 AND s2.id != ?""",
            (symbol_id, symbol_id, symbol_id, symbol_id, symbol_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_qualified_names_in_files(self, file_paths: list[str]) -> list[str]:
        """Return all qualified_name values for symbols in the given files."""
        if not file_paths:
            return []
        conn = self._connect()
        placeholders = ",".join("?" * len(file_paths))
        rows = conn.execute(
            f"SELECT DISTINCT qualified_name FROM symbols WHERE file_path IN ({placeholders})",
            file_paths,
        ).fetchall()
        return [r[0] for r in rows]

    def get_top_symbols_by_connectivity(self, limit: int = 20) -> list[dict]:
        """Return symbols with the most edges (most connected = most important)."""
        conn = self._connect()
        rows = conn.execute(
            """SELECT s.name, s.qualified_name, s.file_path, s.type,
                      COUNT(e.id) as edge_count
               FROM symbols s
               JOIN edges e ON (e.source_id = s.id OR e.target_id = s.id)
               GROUP BY s.id
               ORDER BY edge_count DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
