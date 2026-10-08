from pathlib import Path

import pytest

from config import Settings
from db.manager import DatabaseManager
from exceptions import DataValidationError


def make_manager(tmp_path: Path, **overrides: object) -> DatabaseManager:
    values: dict[str, object] = {
        "database_path": tmp_path / "library.db",
        "cache_ttl_seconds": 3600,
    }
    values.update(overrides)
    settings = Settings(_env_file=None, **values)
    return DatabaseManager(settings)


def test_upsert_work_inserts_and_updates(tmp_path: Path) -> None:
    with make_manager(tmp_path) as database:
        database.upsert_work(
            "/works/OL123W.json",
            "The Hobbit",
            author_names=["J. R. R. Tolkien"],
            author_keys=["OL26320A"],
            isbn_list=["978-0-14-032872-1"],
            subjects=["Fantasy"],
            first_publish_year=1937,
        )
        stored = database.get_work("OL123W")
        database.upsert_work("OL123W", "The Hobbit (revised)")
        revised = database.get_work("OL123W")

    assert stored is not None
    assert stored["author_keys"] == ["/authors/OL26320A"]
    assert stored["isbn_list"] == ["9780140328721"]
    assert stored["first_publish_year"] == 1937
    assert revised is not None
    assert revised["work_key"] == "/works/OL123W"
    assert revised["title"] == "The Hobbit (revised)"


def test_batch_upsert_rolls_back_when_one_record_is_invalid(tmp_path: Path) -> None:
    with make_manager(tmp_path) as database:
        database.upsert_work("OL1W", "Kept")
        with pytest.raises(DataValidationError):
            database.upsert_works(
                [
                    {"work_key": "OL2W", "title": "Rolled back"},
                    {"work_key": "   ", "title": "Invalid"},
                ]
            )

        assert database.get_work("OL1W")["title"] == "Kept"
        assert database.get_work("OL2W") is None


def test_search_works_by_title_author_and_isbn(tmp_path: Path) -> None:
    with make_manager(tmp_path) as database:
        database.upsert_works(
            [
                {
                    "work_key": "OL1W",
                    "title": "The Hobbit",
                    "author_names": ["J. R. R. Tolkien"],
                    "isbn_list": ["9780140328721"],
                },
                {
                    "work_key": "OL2W",
                    "title": "Dune",
                    "author_names": ["Frank Herbert"],
                    "isbn_list": ["9780441172719"],
                },
            ]
        )

        by_title = database.search_works(title="hobbit")
        by_author = database.search_works(author="herbert")
        by_isbn = database.search_works(isbn="978-0-441-17271-9")

    assert [work["work_key"] for work in by_title] == ["/works/OL1W"]
    assert [work["work_key"] for work in by_author] == ["/works/OL2W"]
    assert [work["work_key"] for work in by_isbn] == ["/works/OL2W"]


def test_author_upsert_and_lookup(tmp_path: Path) -> None:
    with make_manager(tmp_path) as database:
        database.upsert_authors(
            [
                {"author_key": "OL26320A", "name": "J. R. R. Tolkien"},
                {"author_key": "/authors/OL123A", "name": "Frank Herbert"},
            ]
        )
        database.upsert_author("OL26320A", "John Ronald Reuel Tolkien")
        author = database.get_author("/authors/OL26320A.json")
        missing = database.get_author("OL0A")

    assert author is not None
    assert author["name"] == "John Ronald Reuel Tolkien"
    assert missing is None


def test_cache_hit_miss_and_purge(tmp_path: Path) -> None:
    with make_manager(tmp_path, cache_ttl_seconds=3600) as database:
        assert database.get_cache("search:tolkien:1") is None
        database.set_cache(
            "search:tolkien:1", "search", {"docs": [{"title": "The Hobbit"}]}
        )
        cached = database.get_cache("search:tolkien:1")
        fresh_metrics = database.get_metrics()

    with make_manager(tmp_path / "expired", cache_ttl_seconds=-1) as expired:
        expired.set_cache("subject:fantasy:0", "subjects", {"works": []})
        assert expired.get_cache("subject:fantasy:0") is None
        removed = expired.purge_expired()
        metrics = expired.get_metrics()

    assert cached == {"docs": [{"title": "The Hobbit"}]}
    assert fresh_metrics["cache_hits"] == 1
    assert fresh_metrics["cache_misses"] == 1
    assert fresh_metrics["cache_hit_ratio"] == 0.5
    assert removed == 1
    assert metrics["api_cache"] == 0
    assert metrics["works"] == 0
    assert metrics["database_size_bytes"] > 0


def test_search_requires_a_criterion(tmp_path: Path) -> None:
    with make_manager(tmp_path) as database:
        with pytest.raises(DataValidationError):
            database.search_works()
