"""Regex cleaning, ISBN checks, and catalog file loading."""

import csv
import json
import re
import unicodedata
from collections.abc import Generator, Iterable, Mapping
from pathlib import Path
from typing import Any

from exceptions import DataValidationError

_CONTROL_CHARACTERS = re.compile(r"[\u0000-\u001f\u007f]+")
_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCTUATION = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_ISBN_DIGITS = re.compile(r"[^0-9Xx]")
_YEAR = re.compile(r"\b(1\d{3}|20\d{2})\b")
_AUTHOR_SPLIT = re.compile(r"[|;]+")
_LIST_SPLIT = re.compile(r"[|;,]+")
_ISBN_CANDIDATE = re.compile(
    r"(?i)(?<!\d)(?:97[89](?:[-\s]?\d){10}|\d(?:[-\s]?\d){8}[-\s]?[\dx])(?!\d)"
)


def normalize_text(value: str | None) -> str:
    """Trim, collapse whitespace, and normalize UTF-8 text to NFC."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _CONTROL_CHARACTERS.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def clean_query(value: str | None) -> str:
    """Clean free-text menu input such as a title or author name."""
    return normalize_text(value)


def clean_title(value: str | None) -> str:
    """Normalize a title and remove punctuation stuck to either end."""
    return _EDGE_PUNCTUATION.sub("", normalize_text(value)).strip()


def clean_subject(value: str | None) -> str:
    """Turn a subject into a lowercase token separated by underscores."""
    text = normalize_text(value).casefold()
    return _NON_SLUG.sub("_", text).strip("_")


def is_valid_isbn10(isbn: str) -> bool:
    digits = _isbn_digits(isbn)
    if len(digits) != 10 or not digits[:9].isdigit():
        return False
    if digits[9] not in "0123456789X":
        return False
    total = sum(
        (10 - index) * (10 if character == "X" else int(character))
        for index, character in enumerate(digits)
    )
    return total % 11 == 0


def is_valid_isbn13(isbn: str) -> bool:
    digits = _isbn_digits(isbn)
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum(
        int(character) * (1 if index % 2 == 0 else 3)
        for index, character in enumerate(digits[:12])
    )
    check_digit = (10 - (total % 10)) % 10
    return check_digit == int(digits[12])


def normalize_isbn(value: str | None) -> str | None:
    """Return the canonical ISBN-10 or ISBN-13, or None when the checksum fails."""
    text = normalize_text(value)
    if not text:
        return None
    direct = _canonical_isbn(text)
    if direct:
        return direct
    for match in _ISBN_CANDIDATE.finditer(text):
        canonical = _canonical_isbn(match.group())
        if canonical:
            return canonical
    return None


def extract_isbns(text: str | None) -> list[str]:
    """Extract every valid ISBN-10 and ISBN-13 from free text."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _ISBN_CANDIDATE.finditer(normalize_text(text)):
        canonical = _canonical_isbn(match.group())
        if canonical and canonical not in seen:
            seen.add(canonical)
            found.append(canonical)
    return found


def stream_catalog(path: str | Path) -> Generator[dict[str, str]]:
    """Yield one normalized record at a time from a UTF-8 CSV or JSON catalog."""
    catalog_path = Path(path)
    suffix = catalog_path.suffix.casefold()
    if suffix == ".csv":
        yield from _stream_csv(catalog_path)
        return
    if suffix == ".json":
        yield from _stream_json(catalog_path)
        return
    raise DataValidationError("Catalog file must be CSV or JSON")


def deduplicate_records(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Drop duplicate catalog rows, keeping the first copy of each key."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        key = catalog_key(record)
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(record))
    return unique


def catalog_key(record: Mapping[str, Any]) -> str:
    raw_key = normalize_text(str(record.get("work_key") or ""))
    if raw_key:
        token = raw_key.removesuffix(".json").rstrip("/").split("/")[-1].casefold()
        return f"work:{token}"
    title = clean_title(str(record.get("title") or "")).casefold()
    isbns = extract_isbns(_isbn_source(record))
    isbn = isbns[0] if isbns else ""
    return f"title:{title}|isbn:{isbn}"


def load_catalog(path: str | Path, database: Any) -> int:
    """Stream, clean, deduplicate, and upsert a dirty catalog file."""
    works = [
        work
        for record in deduplicate_records(stream_catalog(path))
        if (work := _record_to_work(record)) is not None
    ]
    if works:
        database.upsert_works(works)
    return len(works)


def _stream_csv(path: Path) -> Generator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield {
                key: normalize_text(value) if isinstance(value, str) else ""
                for key, value in row.items()
                if key is not None
            }


def _stream_json(path: Path) -> Generator[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if not stripped:
        return
    if stripped.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise DataValidationError("JSON catalog must be a list of objects")
        records = payload
    else:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    for record in records:
        if not isinstance(record, dict):
            raise DataValidationError("JSON catalog must be a list of objects")
        yield {
            str(key): normalize_text(value) if isinstance(value, str) else value
            for key, value in record.items()
        }


def _record_to_work(record: Mapping[str, Any]) -> dict[str, Any] | None:
    title = clean_title(str(record.get("title") or ""))
    if not title:
        return None
    isbns = extract_isbns(_isbn_source(record))
    raw_key = normalize_text(str(record.get("work_key") or ""))
    suffix = isbns[0] if isbns else (clean_subject(title) or "untitled")
    work_key = raw_key or f"local_{suffix}"
    description = clean_query(str(record.get("description") or ""))
    return {
        "work_key": work_key,
        "title": title,
        "description": description or None,
        "first_publish_year": _year_from_text(
            str(record.get("first_publish_year") or "")
        ),
        "isbn_list": isbns,
        "author_keys": _split_tokens(record.get("author_keys"), pattern=_AUTHOR_SPLIT),
        "author_names": _split_tokens(
            record.get("author_names"), pattern=_AUTHOR_SPLIT
        ),
        "subjects": [
            subject
            for item in _split_tokens(record.get("subjects"), pattern=_LIST_SPLIT)
            if (subject := clean_subject(item))
        ],
        "languages": [
            item.casefold()
            for item in _split_tokens(record.get("languages"), pattern=_LIST_SPLIT)
        ],
        "raw_json": dict(record),
    }


def _isbn_source(record: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for field in ("isbn", "isbn_list", "notes"):
        value = record.get(field)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts)


def _split_tokens(value: Any, *, pattern: re.Pattern[str]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        pieces = [str(item) for item in value]
    else:
        pieces = pattern.split(str(value))
    return [token for item in pieces if (token := normalize_text(item))]


def _year_from_text(value: str) -> int | None:
    match = _YEAR.search(normalize_text(value))
    if match is None:
        return None
    return int(match.group(1))


def _isbn_digits(value: str) -> str:
    return _ISBN_DIGITS.sub("", value).upper()


def _canonical_isbn(value: str) -> str | None:
    digits = _isbn_digits(value)
    if len(digits) == 13 and is_valid_isbn13(digits):
        return digits
    if len(digits) == 10 and is_valid_isbn10(digits):
        return digits
    return None
