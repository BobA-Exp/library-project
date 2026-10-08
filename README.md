# Open Library Intelligence & Catalog Automation System

Command-line tool for researching the [Open Library](https://openlibrary.org) catalog. It fetches works by subject with both a synchronous and an asynchronous client, caches results in SQLite, cleans a local catalog file, and writes Excel, CSV, and JSON reports.

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)

## Setup

From the project folder:

```text
uv sync
copy .env.example .env
```

On macOS or Linux, use `cp .env.example .env`. The application reads `.env` from the current working directory. `.env` is gitignored.

| Variable | Purpose | Default |
| --- | --- | --- |
| `OPENLIBRARY_BASE_URL` | Open Library origin | `https://openlibrary.org` |
| `DATABASE_PATH` | SQLite catalog | `data/library.db` |
| `CACHE_TTL_SECONDS` | API cache lifetime | `3600` |
| `REQUEST_TIMEOUT_SECONDS` | HTTP timeout | `30.0` |
| `REPORT_OUTPUT_DIR` | Report folder | `data/outputs` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` | `INFO` |
| `LOG_FILE` | Log path | `logs/library.log` |
| `MAX_RETRIES` | Retries for rate limits and server errors | `3` |
| `HTTPS_PROXY` / `HTTP_PROXY` | Optional HTTP proxy | empty |
| `LIBRARY_INSECURE_SSL` | Set to `1` only on a network that breaks certificate verification | `0` |

Leave `LIBRARY_INSECURE_SSL` at `0` so TLS verification stays on. GitHub Actions does not use a proxy and keeps certificate verification enabled.

## Run

```text
uv run python main.py
```

The menu stays open after a bad choice or a failed request. Option `0` exits.

1. **Ingest Works by Subject.** Asks for a subject and a page depth, fetches each page sequentially and concurrently, prints both timings, and stores the works.
2. **Search Books by Author/Title/ISBN.** Searches the local catalog, then the API cache, then Open Library. ISBN-10 and ISBN-13 checksums are checked before any request.
3. **Process & Deduplicate Local Catalog File.** Streams a CSV or JSON file, normalizes text, drops duplicate records, and upserts the rest. `data/sample_catalog.csv` is a dirty sample.
4. **Generate Analytical Reports.** Writes `library_report_YYYYMMDD.xlsx`, `.csv`, and `.json` under `data/outputs`.
5. **System Health, Cache Maintenance & Database Administration.** Shows row counts, database size, and cache hit ratio. Purges expired cache rows and can update `.env` from the menu.
6. **Run Quality Checks & Test Suite Diagnostic.** Runs `ruff check`, `uv format --check`, and `pytest`, then prints each exit code.

## Tests and CI

```text
uv run ruff check .
uv format --check
uv run pytest
```

API tests mock `httpx` with `unittest.mock` and do not call Open Library. `.github/workflows/ci.yml` runs the same three checks on pushes to `main` and `feature/**`, and on pull requests into `main`.

## Layout

```text
library-project/
├── main.py
├── src/
│   ├── api/          # sync and async Open Library clients
│   ├── cli/          # menu
│   ├── db/           # SQLite works, authors, and API cache
│   ├── processing/   # cleaning and pandas analytics
│   └── reporting/    # Excel, CSV, and JSON export
├── tests/
├── data/
│   ├── sample_catalog.csv
│   └── outputs/
└── .github/workflows/ci.yml
```
