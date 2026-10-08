from pathlib import Path

import pytest

from db.manager import DatabaseManager
from exceptions import DataValidationError
from processing.cleaner import (
    clean_subject,
    clean_title,
    deduplicate_records,
    extract_isbns,
    is_valid_isbn10,
    is_valid_isbn13,
    load_catalog,
    normalize_isbn,
    normalize_text,
    stream_catalog,
)

SAMPLE_CATALOG = Path(__file__).resolve().parents[1] / "data" / "sample_catalog.csv"


def test_normalize_text_collapses_whitespace_and_uses_nfc() -> None:
    assert normalize_text("  The   Hobbit \n") == "The Hobbit"
    assert normalize_text("Cafe\u0301") == "Café"


def test_clean_subject_and_title() -> None:
    assert clean_subject("  Science Fiction! ") == "science_fiction"
    assert clean_title("  The Hobbit!! ") == "The Hobbit"


def test_isbn_checksums_and_extraction() -> None:
    assert is_valid_isbn13("9780140328721")
    assert is_valid_isbn10("0-306-40615-2")
    assert not is_valid_isbn13("9780140328722")
    assert normalize_isbn("978-0-14-032872-1") == "9780140328721"
    assert normalize_isbn("9780140328722") is None
    assert extract_isbns("See ISBN 0-306-40615-2 and 9780140328721.") == [
        "0306406152",
        "9780140328721",
    ]


def test_deduplicate_records_keeps_the_first_work_key() -> None:
    records = deduplicate_records(
        [
            {"work_key": "OL1W", "title": "The Hobbit"},
            {"work_key": "/works/OL1W", "title": "Duplicate"},
            {"title": "Dune"},
        ]
    )

    assert [record["title"] for record in records] == ["The Hobbit", "Dune"]


def test_stream_catalog_rejects_unsupported_files(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.txt"
    catalog.write_text("title\nDune\n", encoding="utf-8")
    with pytest.raises(DataValidationError):
        list(stream_catalog(catalog))


def test_load_catalog_cleans_deduplicates_and_upserts(tmp_path: Path) -> None:
    database_path = tmp_path / "library.db"
    from config import Settings

    with DatabaseManager(
        Settings(_env_file=None, database_path=database_path)
    ) as database:
        loaded = load_catalog(SAMPLE_CATALOG, database)
        hobbit = database.get_work("OL1W")
        notes = database.get_work("OL5W")
        invalid = database.get_work("OL3W")
        accented = database.get_work("OL4W")

    assert loaded == 5
    assert hobbit is not None
    assert hobbit["title"] == "The Hobbit"
    assert hobbit["isbn_list"] == ["9780140328721"]
    assert hobbit["languages"] == ["eng"]
    assert notes is not None
    assert notes["isbn_list"] == ["0306406152"]
    assert notes["first_publish_year"] == 1951
    assert invalid is not None
    assert invalid["isbn_list"] == []
    assert accented is not None
    assert accented["title"] == "Les Misérables"
    assert accented["languages"] == ["fre"]
