from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api.async_client import AsyncLibraryClient
from api.common import USER_AGENT
from api.sync_client import SyncLibraryClient
from config import Settings
from exceptions import APIError, DataValidationError, RateLimitError


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openlibrary_base_url": "https://openlibrary.org",
        "request_timeout_seconds": 30.0,
        "http_proxy": None,
        "https_proxy": None,
        "library_insecure_ssl": False,
        "max_retries": 3,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_response(
    status: int,
    json_body: object | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    url: str = "https://openlibrary.org/search.json",
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if json_body is not None:
        return httpx.Response(status, json=json_body, headers=headers, request=request)
    return httpx.Response(
        status,
        content=content if content is not None else b"",
        headers=headers,
        request=request,
    )


def test_search_returns_json_object() -> None:
    mock_client = MagicMock()
    mock_client.get.return_value = make_response(
        200, {"docs": [{"title": "The Hobbit"}]}
    )
    settings = make_settings()

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        with SyncLibraryClient(settings) as client:
            payload = client.search("tolkien", page=2)

    assert payload["docs"][0]["title"] == "The Hobbit"
    mock_client.get.assert_called_once_with(
        "/search.json",
        params={"q": "tolkien", "page": 2},
    )
    mock_client.close.assert_called_once()


def test_work_and_author_paths_accept_open_library_keys() -> None:
    mock_client = MagicMock()
    mock_client.get.return_value = make_response(200, {"key": "/works/OL123W"})
    settings = make_settings()

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        client = SyncLibraryClient(settings)
        assert client.get_work("/works/OL123W.json")["key"] == "/works/OL123W"
        client.get_author_works("OL456A")

    assert mock_client.get.call_args_list[0].args == ("/works/OL123W.json",)
    assert mock_client.get.call_args_list[1].args == ("/authors/OL456A/works.json",)


def test_subject_pages_request_sequential_offsets() -> None:
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        make_response(200, {"offset": 0}),
        make_response(200, {"offset": 50}),
    ]

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        pages = SyncLibraryClient(make_settings()).get_subject_pages(
            "fantasy",
            page_depth=2,
            limit=50,
        )

    assert [page["offset"] for page in pages] == [0, 50]
    params = [call.kwargs["params"] for call in mock_client.get.call_args_list]
    assert params == [{"limit": 50, "offset": 0}, {"limit": 50, "offset": 50}]


def test_client_error_is_not_retried() -> None:
    mock_client = MagicMock()
    mock_client.get.return_value = make_response(404, {"error": "not found"})

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        client = SyncLibraryClient(make_settings())
        with pytest.raises(APIError):
            client.get_work("OL0W")

    assert mock_client.get.call_count == 1


def test_rate_limit_uses_retry_after_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []
    monkeypatch.setattr("api.sync_client.time.sleep", delays.append)
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        make_response(429, {"error": "slow"}, headers={"Retry-After": "5"}),
        make_response(200, {"docs": []}),
    ]

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        payload = SyncLibraryClient(make_settings()).search("tolkien")

    assert payload == {"docs": []}
    assert delays == [5.0]


def test_server_error_then_success_uses_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []
    monkeypatch.setattr("api.sync_client.time.sleep", delays.append)
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        make_response(503, {"error": "unavailable"}),
        make_response(200, {"docs": [{"title": "Dune"}]}),
    ]

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        payload = SyncLibraryClient(make_settings()).search("dune")

    assert payload["docs"][0]["title"] == "Dune"
    assert delays == [1.0]


def test_rate_limit_error_after_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.sync_client.time.sleep", lambda _delay: None)
    mock_client = MagicMock()
    mock_client.get.return_value = make_response(429, {"error": "slow"})

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        client = SyncLibraryClient(make_settings(max_retries=2))
        with pytest.raises(RateLimitError):
            client.search("tolkien")

    assert mock_client.get.call_count == 2


def test_transport_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.sync_client.time.sleep", lambda _delay: None)
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        httpx.ConnectError("connection failed"),
        make_response(200, {"docs": []}),
    ]

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        payload = SyncLibraryClient(make_settings()).search("tolkien")

    assert payload == {"docs": []}
    assert mock_client.get.call_count == 2


def test_invalid_json_raises_data_validation_error() -> None:
    mock_client = MagicMock()
    mock_client.get.return_value = make_response(200, content=b"not-json")

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        client = SyncLibraryClient(make_settings())
        with pytest.raises(DataValidationError):
            client.search("tolkien")


def test_empty_query_does_not_call_open_library() -> None:
    mock_client = MagicMock()

    with patch("api.sync_client.httpx.Client", return_value=mock_client):
        client = SyncLibraryClient(make_settings())
        with pytest.raises(DataValidationError):
            client.search("   ")

    mock_client.get.assert_not_called()


def test_insecure_ssl_and_proxy_are_passed_to_httpx() -> None:
    with patch("api.sync_client.httpx.Client") as factory:
        SyncLibraryClient(
            make_settings(
                library_insecure_ssl=True,
                https_proxy="http://proxy.example:9090",
            )
        )

    kwargs = factory.call_args.kwargs
    assert kwargs["verify"] is False
    assert kwargs["proxy"] == "http://proxy.example:9090"
    assert kwargs["trust_env"] is True
    assert kwargs["headers"]["User-Agent"] == USER_AGENT


@pytest.mark.asyncio
async def test_async_subject_pages_fetch_offsets_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.async_client.asyncio.sleep", AsyncMock())
    mock_client = AsyncMock()
    mock_client.get.side_effect = [
        make_response(200, {"offset": 0}),
        make_response(200, {"offset": 50}),
    ]

    with patch("api.async_client.httpx.AsyncClient", return_value=mock_client):
        pages = await AsyncLibraryClient(make_settings()).get_subject_pages(
            "fantasy",
            page_depth=2,
            limit=50,
        )

    assert [page["offset"] for page in pages] == [0, 50]
    params = [call.kwargs["params"] for call in mock_client.get.call_args_list]
    assert params == [{"limit": 50, "offset": 0}, {"limit": 50, "offset": 50}]


@pytest.mark.asyncio
async def test_async_insecure_ssl_disables_verification() -> None:
    with patch("api.async_client.httpx.AsyncClient") as factory:
        AsyncLibraryClient(make_settings(library_insecure_ssl=True))

    assert factory.call_args.kwargs["verify"] is False
