import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from cli.menu import CLIApp
from config import Settings
from db.manager import DatabaseManager
from exceptions import APIError

SUBJECT_WORK = {
    "works": [
        {
            "key": "/works/OL1W",
            "title": "The Hobbit",
            "authors": [{"key": "/authors/OL1A", "name": "Tolkien"}],
            "first_publish_year": 1937,
            "subject": ["Fantasy"],
            "language": ["eng"],
        }
    ]
}

SEARCH_WORK = {
    "docs": [
        {
            "key": "/works/OL2W",
            "title": "Dune",
            "author_name": ["Frank Herbert"],
            "author_key": ["OL2A"],
            "isbn": ["9780441172719"],
            "first_publish_year": 1965,
            "language": ["eng"],
        }
    ]
}


def make_app(
    tmp_path: Path,
    responses: list[str],
    **overrides: object,
) -> tuple[CLIApp, list[str], DatabaseManager]:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "library.db",
        report_output_dir=tmp_path / "outputs",
        **overrides,
    )
    database = DatabaseManager(settings)
    lines: list[str] = []
    values = iter(responses)

    def scripted_input(_prompt: str) -> str:
        return next(values)

    app = CLIApp(
        input_func=scripted_input,
        output_func=lines.append,
        settings=settings,
        database=database,
        env_path=tmp_path / ".env",
    )
    return app, lines, database


def mock_clients(sync_payload: dict, async_payload: dict | None = None):
    sync_client = MagicMock()
    sync_client.__enter__.return_value = sync_client
    sync_client.get_subject.return_value = sync_payload
    sync_client.search.return_value = sync_payload
    async_client = AsyncMock()
    async_client.__aenter__.return_value = async_client
    async_client.get_subject.return_value = async_payload or sync_payload
    return sync_client, async_client


def test_invalid_choice_returns_to_the_menu(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["9", "0"])
    app.run()
    database.close()

    assert "Choose an option from 0 to 6." in lines
    assert "Goodbye." in lines
    assert "OPEN LIBRARY INTELLIGENCE & CATALOG AUTOMATION SYSTEM" in lines


def test_subject_ingest_compares_runtimes_and_stores_works(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["1", "Science Fiction", "1", "0"])
    sync_client, async_client = mock_clients(SUBJECT_WORK)
    with (
        patch("cli.menu.SyncLibraryClient", return_value=sync_client),
        patch("cli.menu.AsyncLibraryClient", return_value=async_client),
    ):
        app.run()

    stored = database.get_work("OL1W")
    cached = database.get_cache("subject:science_fiction:0")
    database.close()

    assert stored is not None
    assert stored["title"] == "The Hobbit"
    assert stored["author_names"] == ["Tolkien"]
    assert cached == SUBJECT_WORK
    assert "Fetching sync page 1/1" in lines
    assert "Fetched async page 1/1" in lines
    assert any(line.startswith("Sync fetch finished in ") for line in lines)
    assert any(line.startswith("Async fetch finished in ") for line in lines)
    sync_client.get_subject.assert_called_once_with(
        "science_fiction",
        limit=50,
        offset=0,
    )


def test_api_error_returns_to_the_menu(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["1", "fantasy", "1", "0"])
    sync_client, async_client = mock_clients(SUBJECT_WORK)
    sync_client.get_subject.side_effect = APIError("rate limit")
    with (
        patch("cli.menu.SyncLibraryClient", return_value=sync_client),
        patch("cli.menu.AsyncLibraryClient", return_value=async_client),
    ):
        app.run()
    database.close()

    assert "Error: rate limit" in lines
    assert "Goodbye." in lines
    async_client.get_subject.assert_not_called()


def test_local_search_does_not_call_the_api(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["2", "1", "Hobbit", "0", "0"])
    database.upsert_work(
        "OL1W",
        "The Hobbit",
        author_names=["Tolkien"],
        isbn_list=["9780140328721"],
    )
    sync_client, _async_client = mock_clients(SEARCH_WORK)
    with patch("cli.menu.SyncLibraryClient", return_value=sync_client):
        app.run()
    database.close()

    assert "Found 1 works in the local catalog." in lines
    assert any("The Hobbit" in line and "Tolkien" in line for line in lines)
    sync_client.search.assert_not_called()


def test_invalid_isbn_does_not_call_the_api(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["2", "3", "9780140328722", "0"])
    sync_client, _async_client = mock_clients(SEARCH_WORK)
    with patch("cli.menu.SyncLibraryClient", return_value=sync_client):
        app.run()
    database.close()

    assert "Error: isbn checksum is invalid" in lines
    sync_client.search.assert_not_called()


def test_search_uses_the_cache_before_the_api(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["2", "1", "Dune", "0", "0"])
    database.set_cache("search:title:dune", "search", SEARCH_WORK)
    sync_client, _async_client = mock_clients(SEARCH_WORK)
    with patch("cli.menu.SyncLibraryClient", return_value=sync_client):
        app.run()
    database.close()

    assert "Found 1 works in the API cache." in lines
    sync_client.search.assert_not_called()


def test_search_queries_the_api_after_a_cache_miss(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["2", "1", "Dune", "0", "0"])
    sync_client, _async_client = mock_clients(SEARCH_WORK)
    with patch("cli.menu.SyncLibraryClient", return_value=sync_client):
        app.run()
    stored = database.get_work("OL2W")
    database.close()

    assert stored is not None
    assert stored["title"] == "Dune"
    assert "Cache miss. Querying Open Library..." in lines
    assert "Found 1 works from Open Library." in lines
    sync_client.search.assert_called_once_with("Dune")


def test_catalog_file_is_loaded(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.csv"
    catalog.write_text(
        "work_key,title,author_names,isbn,languages,first_publish_year\n"
        "OL9W,  Dune!! ,Frank Herbert,978-0-441-17271-9,eng,1965\n",
        encoding="utf-8",
    )
    app, lines, database = make_app(tmp_path, ["3", str(catalog), "0"])
    app.run()
    stored = database.get_work("OL9W")
    database.close()

    assert stored is not None
    assert stored["title"] == "Dune"
    assert f"Stored 1 works from {catalog.name}." in lines


def test_reports_are_generated_from_the_database(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["4", "0"])
    database.upsert_work(
        "OL1W", "The Hobbit", first_publish_year=1937, languages=["eng"]
    )
    app.run()
    database.close()

    assert any(
        line.startswith("workbook: ") and line.endswith(".xlsx") for line in lines
    )
    assert any(line.startswith("summary: ") and line.endswith(".csv") for line in lines)
    assert any(line.startswith("json: ") and line.endswith(".json") for line in lines)


def test_health_menu_purges_and_updates_configuration(tmp_path: Path) -> None:
    app, lines, database = make_app(
        tmp_path,
        ["5", "1", "3", "4", "1", "2", "0", "0"],
        cache_ttl_seconds=-1,
    )
    database.set_cache("search:old", "search", {"docs": []})
    try:
        app.run()
    finally:
        os.environ.pop("LIBRARY_INSECURE_SSL", None)
        database.close()

    assert "Purged 1 expired cache entries." in lines
    assert "Updated LIBRARY_INSECURE_SSL." in lines
    assert "LIBRARY_INSECURE_SSL=True" in lines
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LIBRARY_INSECURE_SSL=1" in env_text
    assert app._settings.library_insecure_ssl is True


def test_diagnostics_run_ruff_format_and_pytest(tmp_path: Path) -> None:
    app, lines, database = make_app(tmp_path, ["6", "0"])
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="lint output",
        stderr="",
    )
    with patch("cli.menu.subprocess.run", return_value=completed) as run:
        app.run()
    database.close()

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["uv", "run", "ruff", "check", "."],
        ["uv", "format", "--check"],
        ["uv", "run", "pytest"],
    ]
    assert "Exit code: 1" in lines
    assert "lint output" in lines


def test_main_starts_the_menu(monkeypatch) -> None:
    import main

    calls: list[bool] = []

    class FakeApp:
        def run(self) -> None:
            calls.append(True)

    monkeypatch.setattr("cli.menu.CLIApp", FakeApp)
    main.main()
    assert calls == [True]
