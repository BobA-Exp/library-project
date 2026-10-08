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
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LIBRARY_INSECURE_SSL",
    "MAX_RETRIES",
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
    assert settings.library_insecure_ssl is False
    assert settings.max_retries == 3
    assert settings.https_proxy is None


def test_insecure_ssl_flag_parses_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_INSECURE_SSL", "1")
    settings = Settings(_env_file=None)
    assert settings.library_insecure_ssl is True
