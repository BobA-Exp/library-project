from pathlib import Path

import pytest

from config import Settings, get_settings

_SETTINGS_ENV_VARS = (
    "OPENLIBRARY_BASE_URL",
    "DATABASE_PATH",
    "CACHE_TTL_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",
    "REPORT_OUTPUT_DIR",
    "LOG_LEVEL",
    "LOG_FILE",
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.openlibrary_base_url == "https://openlibrary.org"
    assert settings.report_output_dir == Path("data/outputs")
