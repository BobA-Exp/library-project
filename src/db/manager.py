"""SQLite catalog storage and Open Library response cache."""

import json
import logging
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from config import Settings, get_settings
from exceptions import DataValidationError

logger = logging.getLogger(__name__)

_ISBN_PATTERN = re.compile(r"[^0-9Xx]")


class DatabaseManager:
    """Store works, authors, and time-limited API responses in SQLite."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._path = Path(self._settings.database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, isolation_level="DEFERRED")
        self._connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "DatabaseManager":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def upsert_work(
        self,
        work_key: str,
        title: str,
        *,
        description: str | None = None,
        first_publish_year: int | None = None,
        isbn_list: Sequence[str] | None = None,
        author_keys: Sequence[str] | None = None,
        author_names: Sequence[str] | None = None,
        subjects: Sequence[str] | None = None,
        cover_id: int | None = None,
        raw_json: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connection:
            self._upsert_work(
                work_key=work_key,
                title=title,
                description=description,
                first_publish_year=first_publish_year,
                isbn_list=isbn_list,
                author_keys=author_keys,
                author_names=author_names,
                subjects=subjects,
                cover_id=cover_id,
                raw_json=raw_json,
            )

    def upsert_works(self, works: Sequence[Mapping[str, Any]]) -> None:
        """Insert or update many works in one transaction."""
        with self._connection:
            for work in works:
                self._upsert_work(**work)

    def upsert_author(
        self,
        author_key: str,
        name: str,
        *,
        raw_json: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connection:
            self._upsert_author(author_key, name, raw_json=raw_json)

    def upsert_authors(self, authors: Sequence[Mapping[str, Any]]) -> None:
        """Insert or update many authors in one transaction."""
        with self._connection:
            for author in authors:
                self._upsert_author(**author)

    def get_work(self, work_key: str) -> dict[str, Any] | None:
        key = _canonical_key(work_key, "work")
        row = self._connection.execute(
            "SELECT * FROM works WHERE work_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return _work_from_row(row)

    def get_author(self, author_key: str) -> dict[str, Any] | None:
        key = _canonical_key(author_key, "author")
        row = self._connection.execute(
            "SELECT * FROM authors WHERE author_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return _author_from_row(row)

    def search_works(
        self,
        *,
        title: str | None = None,
        author: str | None = None,
        isbn: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find catalog works by title, author name, or ISBN."""
        title_text = _optional_text(title)
        author_text = _optional_text(author)
        isbn_text = _normalize_isbn(isbn) if isbn and isbn.strip() else None
        if isbn and isbn.strip() and not isbn_text:
            raise DataValidationError("isbn is required")
        if not any((title_text, author_text, isbn_text)):
            raise DataValidationError("A title, author, or ISBN is required")

        joins: list[str] = []
        clauses: list[str] = []
        params: list[str] = []
        if title_text:
            clauses.append("LOWER(w.title) LIKE ?")
            params.append(f"%{title_text.lower()}%")
        if author_text:
            joins.append("JOIN json_each(w.author_names) AS author_name")
            clauses.append("LOWER(author_name.value) LIKE ?")
            params.append(f"%{author_text.lower()}%")
        if isbn_text:
            joins.append("JOIN json_each(w.isbn_list) AS isbn")
            clauses.append("isbn.value = ?")
            params.append(isbn_text)

        query = " ".join(
            [
                "SELECT DISTINCT w.* FROM works AS w",
                " ".join(joins),
                "WHERE",
                " AND ".join(clauses),
                "ORDER BY w.title",
            ]
        )
        rows = self._connection.execute(query, params).fetchall()
        return [_work_from_row(row) for row in rows]

    def set_cache(
        self, cache_key: str, endpoint: str, payload: Mapping[str, Any]
    ) -> None:
        key = _required_text(cache_key, "cache_key")
        endpoint_name = _required_text(endpoint, "endpoint")
        if not isinstance(payload, Mapping):
            raise DataValidationError("payload must be a JSON object")
        fetched_at = _now()
        expires_at = fetched_at + timedelta(seconds=self._settings.cache_ttl_seconds)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO api_cache (
                    cache_key, endpoint, payload, fetched_at, expires_at, hit_count
                )
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(cache_key) DO UPDATE SET
                    endpoint = excluded.endpoint,
                    payload = excluded.payload,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at,
                    hit_count = 0
                """,
                (
                    key,
                    endpoint_name,
                    json.dumps(dict(payload)),
                    _format_timestamp(fetched_at),
                    _format_timestamp(expires_at),
                ),
            )

    def get_cache(self, cache_key: str) -> dict[str, Any] | None:
        """Return a fresh cached payload.

        Missing and expired entries return None.
        """
        key = _required_text(cache_key, "cache_key")
        row = self._connection.execute(
            "SELECT payload, expires_at FROM api_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None or _parse_timestamp(row["expires_at"]) <= _now():
            self._record_lookup(hit=False)
            return None
        with self._connection:
            self._connection.execute(
                "UPDATE api_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (key,),
            )
            self._connection.execute(
                "UPDATE cache_stats SET hits = hits + 1 WHERE id = 1"
            )
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            raise DataValidationError("Cached payload was not a JSON object")
        return payload

    def purge_expired(self) -> int:
        """Delete expired API cache rows and return how many were removed."""
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM api_cache WHERE expires_at <= ?",
                (_format_timestamp(_now()),),
            )
        removed = cursor.rowcount
        logger.info("Purged %s expired API cache entries", removed)
        return removed

    def get_metrics(self) -> dict[str, Any]:
        hits, misses = self._lookup_counts()
        lookups = hits + misses
        return {
            "works": self._count("works"),
            "authors": self._count("authors"),
            "api_cache": self._count("api_cache"),
            "database_size_bytes": self._database_size(),
            "cache_hits": hits,
            "cache_misses": misses,
            "cache_hit_ratio": (hits / lookups) if lookups else 0.0,
        }

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS works (
                    work_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    first_publish_year INTEGER,
                    isbn_list TEXT NOT NULL,
                    author_keys TEXT NOT NULL,
                    author_names TEXT NOT NULL,
                    subjects TEXT NOT NULL,
                    cover_id INTEGER,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS authors (
                    author_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_cache (
                    cache_key TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS cache_stats (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    hits INTEGER NOT NULL DEFAULT 0,
                    misses INTEGER NOT NULL DEFAULT 0
                );

                INSERT INTO cache_stats (id, hits, misses)
                VALUES (1, 0, 0)
                ON CONFLICT(id) DO NOTHING;

                CREATE INDEX IF NOT EXISTS idx_works_title ON works(title);
                CREATE INDEX IF NOT EXISTS idx_api_cache_expires
                    ON api_cache(expires_at);
                """
            )

    def _upsert_work(
        self,
        work_key: str,
        title: str,
        description: str | None = None,
        first_publish_year: int | None = None,
        isbn_list: Sequence[str] | None = None,
        author_keys: Sequence[str] | None = None,
        author_names: Sequence[str] | None = None,
        subjects: Sequence[str] | None = None,
        cover_id: int | None = None,
        raw_json: Mapping[str, Any] | None = None,
    ) -> None:
        key = _canonical_key(work_key, "work")
        work_title = _required_text(title, "title")
        if raw_json is not None and not isinstance(raw_json, Mapping):
            raise DataValidationError("raw_json must be a JSON object")
        self._connection.execute(
            """
            INSERT INTO works (
                work_key, title, description, first_publish_year, isbn_list,
                author_keys, author_names, subjects, cover_id, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_key) DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                first_publish_year = excluded.first_publish_year,
                isbn_list = excluded.isbn_list,
                author_keys = excluded.author_keys,
                author_names = excluded.author_names,
                subjects = excluded.subjects,
                cover_id = excluded.cover_id,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                key,
                work_title,
                description,
                first_publish_year,
                json.dumps(
                    _string_list(
                        isbn_list, label="isbn_list", normalize=_normalize_isbn
                    )
                ),
                json.dumps(
                    [
                        _canonical_key(author_key, "author")
                        for author_key in _string_list(author_keys, label="author_keys")
                    ]
                ),
                json.dumps(_string_list(author_names, label="author_names")),
                json.dumps(_string_list(subjects, label="subjects")),
                cover_id,
                json.dumps(dict(raw_json or {})),
                _format_timestamp(_now()),
            ),
        )

    def _upsert_author(
        self,
        author_key: str,
        name: str,
        raw_json: Mapping[str, Any] | None = None,
    ) -> None:
        key = _canonical_key(author_key, "author")
        author_name = _required_text(name, "name")
        if raw_json is not None and not isinstance(raw_json, Mapping):
            raise DataValidationError("raw_json must be a JSON object")
        self._connection.execute(
            """
            INSERT INTO authors (author_key, name, raw_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(author_key) DO UPDATE SET
                name = excluded.name,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                key,
                author_name,
                json.dumps(dict(raw_json or {})),
                _format_timestamp(_now()),
            ),
        )

    def _record_lookup(self, *, hit: bool) -> None:
        column = "hits" if hit else "misses"
        with self._connection:
            self._connection.execute(
                f"UPDATE cache_stats SET {column} = {column} + 1 WHERE id = 1"
            )

    def _lookup_counts(self) -> tuple[int, int]:
        row = self._connection.execute(
            "SELECT hits, misses FROM cache_stats WHERE id = 1"
        ).fetchone()
        if row is None:
            return (0, 0)
        return (int(row["hits"]), int(row["misses"]))

    def _count(self, table: str) -> int:
        if table not in {"works", "authors", "api_cache"}:
            raise DataValidationError("Unknown metrics table")
        row = self._connection.execute(
            f"SELECT COUNT(*) AS count FROM {table}"
        ).fetchone()
        return int(row["count"])

    def _database_size(self) -> int:
        self._connection.commit()
        paths = [self._path, Path(f"{self._path}-wal"), Path(f"{self._path}-shm")]
        return sum(path.stat().st_size for path in paths if path.exists())


def _canonical_key(value: str, kind: str) -> str:
    text = _required_text(value, f"{kind}_key")
    text = text.removesuffix(".json").rstrip("/")
    prefix = "/works/" if kind == "work" else "/authors/"
    if text.startswith(prefix):
        remainder = text[len(prefix) :]
    else:
        remainder = text.split("/")[-1]
    remainder = remainder.strip()
    if not remainder:
        raise DataValidationError(f"{kind}_key is required")
    return f"{prefix}{remainder}"


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise DataValidationError(f"{label} is required")
    text = value.strip()
    if not text:
        raise DataValidationError(f"{label} is required")
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _normalize_isbn(value: str) -> str:
    return _ISBN_PATTERN.sub("", value).upper()


def _string_list(
    values: Sequence[str] | None,
    *,
    label: str,
    normalize: Any = None,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise DataValidationError(f"{label} must be a list of strings")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise DataValidationError(f"{label} must be a list of strings")
        text = value.strip()
        if normalize is not None:
            text = normalize(text)
        if text:
            cleaned.append(text)
    return cleaned


def _work_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "work_key": row["work_key"],
        "title": row["title"],
        "description": row["description"],
        "first_publish_year": row["first_publish_year"],
        "isbn_list": json.loads(row["isbn_list"]),
        "author_keys": json.loads(row["author_keys"]),
        "author_names": json.loads(row["author_names"]),
        "subjects": json.loads(row["subjects"]),
        "cover_id": row["cover_id"],
        "raw_json": json.loads(row["raw_json"]),
        "updated_at": row["updated_at"],
    }


def _author_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "author_key": row["author_key"],
        "name": row["name"],
        "raw_json": json.loads(row["raw_json"]),
        "updated_at": row["updated_at"],
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
