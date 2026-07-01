from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional


TABLES = {
    "work": "work_cache",
    "query": "query_cache",
    "resolution": "resolution_cache",
}

PRESENCE_TABLE = "qdrant_presence_cache"
PRESENCE_SNAPSHOT_TABLE = "qdrant_presence_snapshot"


class SQLiteTTLCache:
    """Small SQLite cache with TTL semantics and WAL for concurrent readers/writers."""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            for table_name in TABLES.values():
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        cache_key TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL,
                        fetched_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        source TEXT,
                        status_code INTEGER,
                        payload_hash TEXT
                    )
                    """
                )
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table_name}_expires_at ON {table_name}(expires_at)"
                )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {PRESENCE_TABLE} (
                    collection_name TEXT NOT NULL,
                    doi TEXT NOT NULL,
                    present INTEGER NOT NULL,
                    checked_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    source TEXT,
                    PRIMARY KEY(collection_name, doi)
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{PRESENCE_TABLE}_expires_at ON {PRESENCE_TABLE}(expires_at)"
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {PRESENCE_SNAPSHOT_TABLE} (
                    collection_name TEXT PRIMARY KEY,
                    generated_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    doi_count INTEGER NOT NULL,
                    source TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO cache_meta(key, value)
                VALUES('schema_version', '1')
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """
            )
            conn.commit()

    def _table(self, kind: str) -> str:
        table_name = TABLES.get(kind)
        if not table_name:
            raise ValueError(f"Unknown cache kind: {kind}")
        return table_name

    @staticmethod
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def stable_hash(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def key_from_object(prefix: str, value: Any) -> str:
        return f"{prefix}:{SQLiteTTLCache.stable_hash(value)}"

    def read(self, kind: str, cache_key: str, allow_stale: bool = False) -> Optional[Dict[str, Any]]:
        table_name = self._table(kind)
        now_ts = self._now()
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT cache_key, payload_json, fetched_at, expires_at, source, status_code, payload_hash "
                f"FROM {table_name} WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None

        stale = int(row["expires_at"]) <= now_ts
        if stale and not allow_stale:
            return None

        try:
            payload = json.loads(str(row["payload_json"]))
        except Exception:
            return None

        return {
            "cache_key": str(row["cache_key"]),
            "payload": payload,
            "fetched_at": int(row["fetched_at"]),
            "expires_at": int(row["expires_at"]),
            "source": str(row["source"] or ""),
            "status_code": int(row["status_code"] or 0),
            "payload_hash": str(row["payload_hash"] or ""),
            "stale": stale,
            "age_seconds": max(0, now_ts - int(row["fetched_at"])),
        }

    def write(
        self,
        kind: str,
        cache_key: str,
        payload: Dict[str, Any],
        ttl_seconds: int,
        source: str,
        status_code: int = 200,
    ) -> Dict[str, Any]:
        table_name = self._table(kind)
        now_ts = self._now()
        ttl = max(1, int(ttl_seconds))
        expires_at = now_ts + ttl
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {table_name}(
                    cache_key, payload_json, fetched_at, expires_at, source, status_code, payload_hash
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    fetched_at=excluded.fetched_at,
                    expires_at=excluded.expires_at,
                    source=excluded.source,
                    status_code=excluded.status_code,
                    payload_hash=excluded.payload_hash
                """,
                (
                    cache_key,
                    payload_json,
                    now_ts,
                    expires_at,
                    source,
                    int(status_code),
                    payload_hash,
                ),
            )
            conn.commit()

        return {
            "cache_key": cache_key,
            "fetched_at": now_ts,
            "expires_at": expires_at,
            "stale": False,
            "status_code": int(status_code),
            "source": source,
            "payload_hash": payload_hash,
        }

    def invalidate(self, kind: Optional[str] = None, prefix: str = "") -> Dict[str, Any]:
        targets = [self._table(kind)] if kind else list(TABLES.values())
        total_deleted = 0
        with self._connect() as conn:
            for table_name in targets:
                if prefix:
                    cur = conn.execute(
                        f"DELETE FROM {table_name} WHERE cache_key LIKE ?",
                        (f"{prefix}%",),
                    )
                else:
                    cur = conn.execute(f"DELETE FROM {table_name}")
                total_deleted += int(cur.rowcount or 0)
            conn.commit()
        return {"deleted_rows": total_deleted, "tables": targets, "prefix": prefix}

    def invalidate_presence(self, collection_name: Optional[str] = None) -> Dict[str, Any]:
        deleted_presence = 0
        deleted_snapshots = 0
        with self._connect() as conn:
            if collection_name:
                cur1 = conn.execute(
                    f"DELETE FROM {PRESENCE_TABLE} WHERE collection_name = ?",
                    (collection_name,),
                )
                cur2 = conn.execute(
                    f"DELETE FROM {PRESENCE_SNAPSHOT_TABLE} WHERE collection_name = ?",
                    (collection_name,),
                )
                deleted_presence += int(cur1.rowcount or 0)
                deleted_snapshots += int(cur2.rowcount or 0)
            else:
                cur1 = conn.execute(f"DELETE FROM {PRESENCE_TABLE}")
                cur2 = conn.execute(f"DELETE FROM {PRESENCE_SNAPSHOT_TABLE}")
                deleted_presence += int(cur1.rowcount or 0)
                deleted_snapshots += int(cur2.rowcount or 0)
            conn.commit()
        return {
            "deleted_presence_rows": deleted_presence,
            "deleted_snapshot_rows": deleted_snapshots,
            "collection_name": collection_name,
        }

    def gc(self, kind: Optional[str] = None) -> Dict[str, Any]:
        targets = [self._table(kind)] if kind else list(TABLES.values())
        now_ts = self._now()
        removed = 0
        with self._connect() as conn:
            for table_name in targets:
                cur = conn.execute(
                    f"DELETE FROM {table_name} WHERE expires_at <= ?",
                    (now_ts,),
                )
                removed += int(cur.rowcount or 0)
            cur_presence = conn.execute(
                f"DELETE FROM {PRESENCE_TABLE} WHERE expires_at <= ?",
                (now_ts,),
            )
            removed += int(cur_presence.rowcount or 0)
            cur_snapshot = conn.execute(
                f"DELETE FROM {PRESENCE_SNAPSHOT_TABLE} WHERE expires_at <= ?",
                (now_ts,),
            )
            removed += int(cur_snapshot.rowcount or 0)
            conn.commit()
        return {"removed_rows": removed, "tables": targets, "gc_at": now_ts}

    def write_presence_batch(
        self,
        collection_name: str,
        doi_to_present: Dict[str, bool],
        ttl_seconds: int,
        source: str,
    ) -> Dict[str, Any]:
        if not doi_to_present:
            return {"upserted_rows": 0, "collection_name": collection_name}

        now_ts = self._now()
        expires_at = now_ts + max(1, int(ttl_seconds))
        rows = [
            (
                collection_name,
                doi,
                1 if bool(present) else 0,
                now_ts,
                expires_at,
                source,
            )
            for doi, present in doi_to_present.items()
            if doi
        ]
        with self._connect() as conn:
            conn.executemany(
                f"""
                INSERT INTO {PRESENCE_TABLE}(
                    collection_name, doi, present, checked_at, expires_at, source
                )
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_name, doi) DO UPDATE SET
                    present=excluded.present,
                    checked_at=excluded.checked_at,
                    expires_at=excluded.expires_at,
                    source=excluded.source
                """,
                rows,
            )
            conn.commit()
        return {
            "upserted_rows": len(rows),
            "collection_name": collection_name,
            "checked_at": now_ts,
            "expires_at": expires_at,
        }

    def read_presence_batch(
        self,
        collection_name: str,
        dois: list[str],
        allow_stale: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        doi_list = [d for d in dois if d]
        if not doi_list:
            return {}

        now_ts = self._now()
        out: Dict[str, Dict[str, Any]] = {}
        chunk_size = 500
        with self._connect() as conn:
            for i in range(0, len(doi_list), chunk_size):
                chunk = doi_list[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                query = (
                    f"SELECT doi, present, checked_at, expires_at, source "
                    f"FROM {PRESENCE_TABLE} "
                    f"WHERE collection_name = ? AND doi IN ({placeholders})"
                )
                params = [collection_name, *chunk]
                for row in conn.execute(query, params).fetchall():
                    stale = int(row["expires_at"]) <= now_ts
                    if stale and not allow_stale:
                        continue
                    out[str(row["doi"])] = {
                        "present": bool(int(row["present"])),
                        "checked_at": int(row["checked_at"]),
                        "expires_at": int(row["expires_at"]),
                        "source": str(row["source"] or ""),
                        "stale": stale,
                    }
        return out

    def set_presence_snapshot(
        self,
        collection_name: str,
        doi_count: int,
        ttl_seconds: int,
        source: str,
    ) -> Dict[str, Any]:
        now_ts = self._now()
        expires_at = now_ts + max(1, int(ttl_seconds))
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {PRESENCE_SNAPSHOT_TABLE}(
                    collection_name, generated_at, expires_at, doi_count, source
                )
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(collection_name) DO UPDATE SET
                    generated_at=excluded.generated_at,
                    expires_at=excluded.expires_at,
                    doi_count=excluded.doi_count,
                    source=excluded.source
                """,
                (collection_name, now_ts, expires_at, int(doi_count), source),
            )
            conn.commit()
        return {
            "collection_name": collection_name,
            "generated_at": now_ts,
            "expires_at": expires_at,
            "doi_count": int(doi_count),
            "source": source,
            "stale": False,
        }

    def get_presence_snapshot(
        self,
        collection_name: str,
        allow_stale: bool = False,
    ) -> Optional[Dict[str, Any]]:
        now_ts = self._now()
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT collection_name, generated_at, expires_at, doi_count, source
                FROM {PRESENCE_SNAPSHOT_TABLE}
                WHERE collection_name = ?
                """,
                (collection_name,),
            ).fetchone()
        if row is None:
            return None
        stale = int(row["expires_at"]) <= now_ts
        if stale and not allow_stale:
            return None
        return {
            "collection_name": str(row["collection_name"]),
            "generated_at": int(row["generated_at"]),
            "expires_at": int(row["expires_at"]),
            "doi_count": int(row["doi_count"]),
            "source": str(row["source"] or ""),
            "stale": stale,
        }

    def stats(self) -> Dict[str, Any]:
        now_ts = self._now()
        output: Dict[str, Any] = {
            "db_path": str(self.db_path),
            "generated_at": now_ts,
            "tables": {},
        }

        with self._connect() as conn:
            for kind, table_name in TABLES.items():
                total = int(
                    conn.execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()["n"]
                )
                expired = int(
                    conn.execute(
                        f"SELECT COUNT(*) AS n FROM {table_name} WHERE expires_at <= ?",
                        (now_ts,),
                    ).fetchone()["n"]
                )
                newest = conn.execute(
                    f"SELECT MAX(fetched_at) AS ts FROM {table_name}"
                ).fetchone()["ts"]
                oldest = conn.execute(
                    f"SELECT MIN(fetched_at) AS ts FROM {table_name}"
                ).fetchone()["ts"]
                output["tables"][kind] = {
                    "table_name": table_name,
                    "rows_total": total,
                    "rows_expired": expired,
                    "rows_fresh": max(0, total - expired),
                    "oldest_fetched_at": int(oldest) if oldest is not None else None,
                    "newest_fetched_at": int(newest) if newest is not None else None,
                }

            presence_total = int(
                conn.execute(f"SELECT COUNT(*) AS n FROM {PRESENCE_TABLE}").fetchone()["n"]
            )
            presence_expired = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM {PRESENCE_TABLE} WHERE expires_at <= ?",
                    (now_ts,),
                ).fetchone()["n"]
            )
            snapshot_rows = int(
                conn.execute(f"SELECT COUNT(*) AS n FROM {PRESENCE_SNAPSHOT_TABLE}").fetchone()["n"]
            )
            output["tables"]["qdrant_presence"] = {
                "table_name": PRESENCE_TABLE,
                "rows_total": presence_total,
                "rows_expired": presence_expired,
                "rows_fresh": max(0, presence_total - presence_expired),
            }
            output["tables"]["qdrant_presence_snapshot"] = {
                "table_name": PRESENCE_SNAPSHOT_TABLE,
                "rows_total": snapshot_rows,
            }

        return output
