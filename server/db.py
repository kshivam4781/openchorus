"""Tiny SQLite store for the song library and personas."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SONG_COLUMNS = [
    "id", "batch_id", "title", "style", "lyrics", "description", "mode",
    "instrumental", "task", "parent_id", "persona_id", "status", "error",
    "engine", "audio_file", "duration", "bpm", "key_scale", "time_signature",
    "seed", "liked", "params", "created_at", "updated_at", "cover_file", "disliked",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT '',
    lyrics TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'custom',
    instrumental INTEGER NOT NULL DEFAULT 0,
    task TEXT NOT NULL DEFAULT 'create',
    parent_id TEXT,
    persona_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    engine TEXT,
    audio_file TEXT,
    duration REAL,
    bpm INTEGER,
    key_scale TEXT,
    time_signature TEXT,
    seed INTEGER,
    liked INTEGER NOT NULL DEFAULT 0,
    params TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS songs_created ON songs(created_at DESC);
CREATE TABLE IF NOT EXISTS personas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    style TEXT NOT NULL DEFAULT '',
    source_song_id TEXT,
    reference_file TEXT,
    created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        # Columns added after the first release.
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(songs)")}
        for col, decl in (("cover_file", "TEXT"), ("disliked", "INTEGER NOT NULL DEFAULT 0")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE songs ADD COLUMN {col} {decl}")
        self._conn.commit()
        # Anything left "running" by a crash will never finish; mark it failed.
        self._conn.execute(
            "UPDATE songs SET status='failed', error='Server restarted while generating' "
            "WHERE status IN ('queued','running')"
        )
        self._conn.commit()

    # ---- songs -----------------------------------------------------------
    @staticmethod
    def _song(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        song = dict(row)
        song["instrumental"] = bool(song["instrumental"])
        song["liked"] = bool(song["liked"])
        song["disliked"] = bool(song.get("disliked") or 0)
        song["params"] = json.loads(song["params"] or "{}")
        return song

    def create_song(self, **fields: Any) -> dict[str, Any]:
        now = time.time()
        song = {c: None for c in SONG_COLUMNS}
        song.update(
            id=uuid.uuid4().hex[:12], title="", style="", lyrics="", description="",
            mode="custom", instrumental=False, task="create", status="queued",
            liked=False, disliked=False, params={}, created_at=now, updated_at=now,
        )
        song.update(fields)
        row = dict(song)
        row["instrumental"] = int(bool(row["instrumental"]))
        row["liked"] = int(bool(row["liked"]))
        row["disliked"] = int(bool(row["disliked"]))
        row["params"] = json.dumps(row["params"] or {})
        cols = ",".join(SONG_COLUMNS)
        marks = ",".join("?" for _ in SONG_COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO songs ({cols}) VALUES ({marks})",
                [row[c] for c in SONG_COLUMNS],
            )
            self._conn.commit()
        return self.get_song(song["id"])  # type: ignore[return-value]

    def update_song(self, song_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return self.get_song(song_id)
        fields["updated_at"] = time.time()
        if "params" in fields:
            fields["params"] = json.dumps(fields["params"] or {})
        for key in ("instrumental", "liked", "disliked"):
            if key in fields:
                fields[key] = int(bool(fields[key]))
        unknown = set(fields) - set(SONG_COLUMNS)
        if unknown:
            raise ValueError(f"unknown song fields: {sorted(unknown)}")
        sets = ",".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE songs SET {sets} WHERE id=?", [*fields.values(), song_id]
            )
            self._conn.commit()
        return self.get_song(song_id)

    def get_song(self, song_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        return self._song(row)

    def list_songs(self, liked: bool | None = None, q: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM songs"
        where: list[str] = []
        args: list[Any] = []
        if liked:
            where.append("liked=1")
        if q:
            where.append("(title LIKE ? OR style LIKE ? OR lyrics LIKE ?)")
            args += [f"%{q}%"] * 3
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, rowid ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._song(r) for r in rows]  # type: ignore[misc]

    def delete_song(self, song_id: str) -> dict[str, Any] | None:
        song = self.get_song(song_id)
        if song:
            with self._lock:
                self._conn.execute("DELETE FROM songs WHERE id=?", (song_id,))
                self._conn.commit()
        return song

    # ---- personas --------------------------------------------------------
    def create_persona(self, name: str, style: str, source_song_id: str | None,
                       reference_file: str | None) -> dict[str, Any]:
        pid = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO personas (id,name,style,source_song_id,reference_file,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (pid, name, style, source_song_id, reference_file, time.time()),
            )
            self._conn.commit()
        return self.get_persona(pid)  # type: ignore[return-value]

    def get_persona(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM personas WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None

    def list_personas(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM personas ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_persona(self, pid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM personas WHERE id=?", (pid,))
            self._conn.commit()
        return cur.rowcount > 0
