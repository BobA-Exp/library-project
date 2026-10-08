"""Interactive menu for the Open Library catalog tool."""

import asyncio
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from api.async_client import AsyncLibraryClient
from api.sync_client import SyncLibraryClient
from config import Settings, get_settings
from db.manager import DatabaseManager
from exceptions import DataValidationError, LibraryError
from processing.cleaner import (
    clean_query,
    clean_subject,
    clean_title,
    load_catalog,
    normalize_isbn,
)
from reporting.generator import ReportGenerator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUBJECT_PAGE_SIZE = 50
MENU_RULE = "=" * 70

InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]

_EDITABLE_SETTINGS = (
    ("1", "OPENLIBRARY_BASE_URL", "openlibrary_base_url"),
    ("2", "CACHE_TTL_SECONDS", "cache_ttl_seconds"),
    ("3", "LOG_LEVEL", "log_level"),
    ("4", "LIBRARY_INSECURE_SSL", "library_insecure_ssl"),
    ("5", "MAX_RETRIES", "max_retries"),
    ("6", "HTTPS_PROXY", "https_proxy"),
    ("7", "HTTP_PROXY", "http_proxy"),
)
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_DIAGNOSTIC_COMMANDS = (
    ["uv", "run", "ruff", "check", "."],
    ["uv", "format", "--check"],
    ["uv", "run", "pytest"],
)


class CLIApp:
    """Loop-driven terminal menu for catalog ingestion, search, and reports."""

    def __init__(
        self,
        input_func: InputFunc | None = None,
        output_func: OutputFunc | None = None,
        settings: Settings | None = None,
        database: DatabaseManager | None = None,
        env_path: Path | None = None,
    ) -> None:
        self._input = input_func or input
        self._output = output_func or print
        self._settings = settings or get_settings()
        self._database = database
        self._owns_database = database is None
        self._env_path = env_path or (PROJECT_ROOT / ".env")

    def run(self) -> None:
        """Display the menu until the user exits."""
        if self._database is None:
            self._database = DatabaseManager(self._settings)
        try:
            while True:
                try:
                    self._print_menu()
                    choice = self._prompt("Select an option: ")
                    if choice == "0":
                        self._output("Goodbye.")
                        return
                    handler = {
                        "1": self._ingest_subject,
                        "2": self._search_catalog,
                        "3": self._process_catalog_file,
                        "4": self._generate_reports,
                        "5": self._system_health,
                        "6": self._run_diagnostics,
                    }.get(choice)
                    if handler is None:
                        self._output("Choose an option from 0 to 6.")
                        continue
                    handler()
                except (KeyboardInterrupt, EOFError):
                    self._output("Goodbye.")
                    return
                except (LibraryError, OSError, ValueError) as error:
                    self._output(f"Error: {error}")
                except Exception as error:
                    self._output(f"Unexpected error: {error}")
        finally:
            if self._owns_database and self._database is not None:
                self._database.close()

    def _print_menu(self) -> None:
        self._output(MENU_RULE)
        self._output("OPEN LIBRARY INTELLIGENCE & CATALOG AUTOMATION SYSTEM")
        self._output(MENU_RULE)
        self._output("[1] Ingest Works by Subject")
        self._output("[2] Search Books by Author/Title/ISBN")
        self._output("[3] Process & Deduplicate Local Catalog File")
        self._output("[4] Generate Analytical Reports")
        self._output("[5] System Health, Cache Maintenance & Database Administration")
        self._output("[6] Run Quality Checks & Test Suite Diagnostic")
        self._output("[0] Exit")
        self._output(MENU_RULE)

    def _ingest_subject(self) -> None:
        subject = clean_subject(self._prompt("Subject: "))
        if not subject:
            raise DataValidationError("A subject is required.")
        page_depth = self._positive_int("Page depth: ")
        sync_started = time.perf_counter()
        sync_pages = self._fetch_subject_sync(subject, page_depth)
        sync_elapsed = time.perf_counter() - sync_started
        async_started = time.perf_counter()
        async_pages = asyncio.run(self._fetch_subject_async(subject, page_depth))
        async_elapsed = time.perf_counter() - async_started
        stored = self._cache_pages(subject, sync_pages)
        self._cache_pages(subject, async_pages)
        self._output(f"Sync fetch finished in {sync_elapsed:.2f} seconds.")
        self._output(f"Async fetch finished in {async_elapsed:.2f} seconds.")
        self._output(f"Stored {stored} works.")

    def _search_catalog(self) -> None:
        while True:
            self._output("Search by:")
            self._output("[1] Title")
            self._output("[2] Author")
            self._output("[3] ISBN")
            self._output("[0] Back")
            choice = self._prompt("Select a search field: ")
            if choice == "0":
                return
            if choice not in {"1", "2", "3"}:
                self._output("Choose a search field from 0 to 3.")
                continue
            self._search_by(choice)

    def _process_catalog_file(self) -> None:
        raw_path = self._prompt("Catalog file: ").strip().strip('"')
        catalog_path = Path(raw_path)
        if not catalog_path.is_file():
            raise DataValidationError(f"Catalog file not found: {catalog_path}")
        self._output(f"Processing {catalog_path.name}...")
        stored = load_catalog(catalog_path, self._require_database())
        self._output(f"Stored {stored} works from {catalog_path.name}.")

    def _generate_reports(self) -> None:
        self._output("Generating reports...")
        paths = ReportGenerator.from_database(
            self._require_database(),
            self._settings,
        ).generate()
        for label, path in paths.items():
            self._output(f"{label}: {path}")

    def _system_health(self) -> None:
        self._print_metrics()
        while True:
            self._output("[1] Purge expired cache")
            self._output("[2] Show configuration")
            self._output("[3] Update configuration")
            self._output("[0] Back")
            choice = self._prompt("Select an admin option: ")
            if choice == "0":
                return
            if choice == "1":
                removed = self._require_database().purge_expired()
                self._output(f"Purged {removed} expired cache entries.")
            elif choice == "2":
                self._print_configuration()
            elif choice == "3":
                self._update_configuration()
            else:
                self._output("Choose an admin option from 0 to 3.")

    def _run_diagnostics(self) -> None:
        for command in _DIAGNOSTIC_COMMANDS:
            self._output("$ " + " ".join(command))
            try:
                completed = subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as error:
                self._output(f"Error: {error}")
                continue
            if completed.stdout.strip():
                self._output(completed.stdout.rstrip())
            if completed.stderr.strip():
                self._output(completed.stderr.rstrip())
            self._output(f"Exit code: {completed.returncode}")

    def _fetch_subject_sync(
        self, subject: str, page_depth: int
    ) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        with SyncLibraryClient(self._settings) as client:
            for index in range(page_depth):
                offset = index * SUBJECT_PAGE_SIZE
                self._output(f"Fetching sync page {index + 1}/{page_depth}")
                pages.append(
                    client.get_subject(subject, limit=SUBJECT_PAGE_SIZE, offset=offset)
                )
        return pages

    async def _fetch_subject_async(
        self,
        subject: str,
        page_depth: int,
    ) -> list[dict[str, Any]]:
        async with AsyncLibraryClient(self._settings) as client:

            async def fetch(index: int) -> dict[str, Any]:
                offset = index * SUBJECT_PAGE_SIZE
                payload = await client.get_subject(
                    subject,
                    limit=SUBJECT_PAGE_SIZE,
                    offset=offset,
                )
                self._output(f"Fetched async page {index + 1}/{page_depth}")
                return payload

            return list(
                await asyncio.gather(*(fetch(index) for index in range(page_depth)))
            )

    def _cache_pages(self, subject: str, pages: Sequence[Mapping[str, Any]]) -> int:
        database = self._require_database()
        stored = 0
        for index, page in enumerate(pages):
            offset = index * SUBJECT_PAGE_SIZE
            database.set_cache(f"subject:{subject}:{offset}", "subjects", page)
            works = works_from_payload(page)
            self._store_works(works)
            stored += len(works)
        return stored

    def _search_by(self, choice: str) -> None:
        if choice == "1":
            text = clean_query(self._prompt("Title: "))
            if not text:
                raise DataValidationError("A title is required.")
            self._search_field("title", text, title=text)
            return
        if choice == "2":
            text = clean_query(self._prompt("Author: "))
            if not text:
                raise DataValidationError("An author is required.")
            self._search_field("author", text, author=text)
            return
        text = self._prompt("ISBN: ")
        isbn = normalize_isbn(text)
        if not isbn:
            raise DataValidationError("isbn checksum is invalid")
        self._search_field("isbn", isbn, isbn=isbn)

    def _search_field(self, field: str, query: str, **filters: str) -> None:
        database = self._require_database()
        local_works = database.search_works(**filters)
        if local_works:
            self._output(f"Found {len(local_works)} works in the local catalog.")
            self._print_works(local_works)
            return
        cache_key = f"search:{field}:{query.casefold()}"
        cached = database.get_cache(cache_key)
        if cached is not None:
            works = works_from_payload(cached)
            self._output(f"Found {len(works)} works in the API cache.")
            self._print_works(works)
            return
        self._output("Cache miss. Querying Open Library...")
        with SyncLibraryClient(self._settings) as client:
            payload = client.search(query)
        database.set_cache(cache_key, "search", payload)
        works = works_from_payload(payload)
        self._store_works(works)
        self._output(f"Found {len(works)} works from Open Library.")
        self._print_works(works)

    def _store_works(self, works: Sequence[Mapping[str, Any]]) -> None:
        database = self._require_database()
        if works:
            database.upsert_works(works)
        authors = _author_records(works)
        if authors:
            database.upsert_authors(authors)

    def _print_works(self, works: Sequence[Mapping[str, Any]]) -> None:
        if not works:
            self._output("No works found.")
            return
        rows = [
            (
                str(work.get("title") or ""),
                ""
                if work.get("first_publish_year") is None
                else str(work.get("first_publish_year")),
                ", ".join(work.get("author_names") or []),
                ", ".join(work.get("isbn_list") or []),
            )
            for work in works
        ]
        for line in _format_table(rows, ("Title", "Year", "Authors", "ISBN")):
            self._output(line)

    def _print_metrics(self) -> None:
        metrics = self._require_database().get_metrics()
        labels = (
            ("works", "Works"),
            ("authors", "Authors"),
            ("api_cache", "API cache rows"),
            ("database_size_bytes", "Database size (bytes)"),
            ("cache_hits", "Cache hits"),
            ("cache_misses", "Cache misses"),
        )
        for key, label in labels:
            self._output(f"{label}: {metrics[key]}")
        self._output(f"Cache hit ratio: {metrics['cache_hit_ratio']:.2%}")

    def _print_configuration(self) -> None:
        for _number, env_key, field in _EDITABLE_SETTINGS:
            self._output(f"{env_key}={getattr(self._settings, field)}")

    def _update_configuration(self) -> None:
        for number, env_key, field in _EDITABLE_SETTINGS:
            self._output(f"[{number}] {env_key}={getattr(self._settings, field)}")
        number = self._prompt("Setting number: ")
        selected = next(
            (item for item in _EDITABLE_SETTINGS if item[0] == number), None
        )
        if selected is None:
            raise DataValidationError("Choose a setting number from the list.")
        _env_number, env_key, field = selected
        parsed, stored = _coerce_setting(field, self._prompt("New value: "))
        _write_env(self._env_path, env_key, stored)
        os.environ[env_key] = stored
        setattr(self._settings, field, parsed)
        get_settings.cache_clear()
        self._output(f"Updated {env_key}.")

    def _positive_int(self, label: str) -> int:
        raw = self._prompt(label)
        if not raw.isdigit() or int(raw) < 1:
            raise DataValidationError(
                f"{label.strip(': ')} must be a whole number of at least 1."
            )
        return int(raw)

    def _prompt(self, label: str) -> str:
        return self._input(label).strip()

    def _require_database(self) -> DatabaseManager:
        if self._database is None:
            raise RuntimeError("Database is not open")
        return self._database


def works_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Map an Open Library subject or search payload onto catalog works."""
    records = payload.get("works") or payload.get("docs") or []
    if not isinstance(records, list):
        return []
    works: list[dict[str, Any]] = []
    for record in records:
        if isinstance(record, dict):
            mapped = _map_work(record)
            if mapped is not None:
                works.append(mapped)
    return works


def _map_work(record: Mapping[str, Any]) -> dict[str, Any] | None:
    title = clean_title(str(record.get("title") or ""))
    work_key = str(record.get("key") or "").strip()
    if not title or not work_key:
        return None
    author_names, author_keys = _authors_from_record(record)
    year = record.get("first_publish_year")
    cover_id = record.get("cover_id")
    return {
        "work_key": work_key,
        "title": title,
        "first_publish_year": year if isinstance(year, int) else None,
        "isbn_list": _valid_isbns(record.get("isbn")),
        "author_keys": author_keys,
        "author_names": author_names,
        "subjects": _text_list(record.get("subject") or record.get("subjects")),
        "languages": _text_list(record.get("language") or record.get("languages")),
        "cover_id": cover_id if isinstance(cover_id, int) else None,
        "raw_json": dict(record),
    }


def _authors_from_record(record: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    authors = record.get("authors")
    if isinstance(authors, list) and any(isinstance(item, dict) for item in authors):
        names: list[str] = []
        keys: list[str] = []
        for author in authors:
            if not isinstance(author, dict):
                continue
            name = str(author.get("name") or "").strip()
            key = str(author.get("key") or "").strip()
            if name:
                names.append(name)
            if key:
                keys.append(key)
        return names, keys
    return _text_list(record.get("author_name")), _text_list(record.get("author_key"))


def _valid_isbns(value: Any) -> list[str]:
    isbns: list[str] = []
    for item in _text_list(value):
        canonical = normalize_isbn(item)
        if canonical:
            isbns.append(canonical)
    return isbns


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    texts: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("name") or item.get("key") or "").strip()
        else:
            continue
        if text:
            texts.append(text)
    return texts


def _author_records(works: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for work in works:
        keys = list(work.get("author_keys") or [])
        names = list(work.get("author_names") or [])
        for index, key in enumerate(keys):
            if key in seen:
                continue
            seen.add(key)
            name = names[index] if index < len(names) and names[index] else key
            records.append({"author_key": str(key), "name": str(name)})
    return records


def _format_table(
    rows: Sequence[tuple[str, ...]], headers: tuple[str, ...]
) -> list[str]:
    widths = []
    for index, header in enumerate(headers):
        width = len(header)
        for row in rows:
            width = max(width, len(row[index]))
        widths.append(min(width, 40))

    def clip(text: str, width: int) -> str:
        if len(text) <= width:
            return text.ljust(width)
        if width <= 3:
            return text[:width]
        return text[: width - 3] + "..."

    lines = [
        "  ".join(clip(header, widths[index]) for index, header in enumerate(headers))
    ]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append(
            "  ".join(clip(row[index], widths[index]) for index in range(len(headers)))
        )
    return lines


def _coerce_setting(field: str, raw: str) -> tuple[Any, str]:
    text = raw.strip()
    if field == "library_insecure_ssl":
        token = text.casefold()
        if token in {"1", "true", "yes", "on"}:
            return True, "1"
        if token in {"0", "false", "no", "off"}:
            return False, "0"
        raise DataValidationError("LIBRARY_INSECURE_SSL must be 0 or 1.")
    if field == "cache_ttl_seconds":
        if not text.isdigit():
            raise DataValidationError("CACHE_TTL_SECONDS must be a whole number.")
        return int(text), text
    if field == "max_retries":
        if not text.isdigit() or int(text) < 1:
            raise DataValidationError(
                "MAX_RETRIES must be a whole number of at least 1."
            )
        return int(text), text
    if field == "log_level":
        level = text.upper()
        if level not in _LOG_LEVELS:
            raise DataValidationError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL."
            )
        return level, level
    if field == "openlibrary_base_url":
        if not text.startswith("http"):
            raise DataValidationError("OPENLIBRARY_BASE_URL must start with http.")
        return text, text
    return (text or None), text


def _write_env(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = False
    rewritten: list[str] = []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if name == key:
            rewritten.append(f"{key}={value}")
            updated = True
        else:
            rewritten.append(line)
    if not updated:
        rewritten.append(f"{key}={value}")
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
