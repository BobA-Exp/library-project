"""Shared HTTP behavior for the Open Library clients."""

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from config import Settings
from exceptions import APIError, DataValidationError, RateLimitError

logger = logging.getLogger(__name__)

USER_AGENT = "library-project/0.1 (SOFT-753)"


class RetryRequest(Exception):
    """The current attempt failed and another attempt is allowed."""

    def __init__(self, delay: float, error: APIError) -> None:
        self.delay = delay
        self.error = error
        super().__init__(str(error))


def client_kwargs(settings: Settings) -> dict[str, Any]:
    """Build the httpx client arguments, including the local TLS workaround."""
    proxy = (settings.https_proxy or settings.http_proxy or "").strip() or None
    if settings.library_insecure_ssl:
        logger.warning(
            "TLS certificate verification is DISABLED. "
            "Use only for temporary local development."
        )
    return {
        "base_url": settings.openlibrary_base_url,
        "timeout": settings.request_timeout_seconds,
        "verify": not settings.library_insecure_ssl,
        "proxy": proxy,
        "trust_env": True,
        "follow_redirects": True,
        "headers": {"User-Agent": USER_AGENT},
    }


def required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise DataValidationError(f"{label} is required")
    return text


def resource_key(value: str, label: str) -> str:
    text = required_text(value, label).rstrip("/").split("/")[-1]
    if text.endswith(".json"):
        text = text[: -len(".json")]
    text = text.strip()
    if not text:
        raise DataValidationError(f"{label} is required")
    return quote(text, safe="")


def positive_int(value: int, label: str) -> int:
    if value < 1:
        raise DataValidationError(f"{label} must be at least 1")
    return value


def non_negative_int(value: int, label: str) -> int:
    if value < 0:
        raise DataValidationError(f"{label} must be at least 0")
    return value


def retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Use Retry-After when Open Library sends it, otherwise back off."""
    if response is not None:
        wait = _retry_after_seconds(response)
        if wait is not None:
            return wait
    return float(2 ** (attempt - 1))


def transport_failure(
    error: httpx.TransportError, attempt: int, max_retries: int
) -> float:
    failure = APIError(f"Open Library request failed: {error}")
    if attempt >= max_retries:
        raise failure from error
    return retry_delay(None, attempt)


def read_payload(
    response: httpx.Response, attempt: int, max_retries: int
) -> dict[str, Any]:
    status = response.status_code
    if status == 429 or status >= 500:
        if status == 429:
            error: APIError = RateLimitError(
                f"Open Library rate limit exceeded for {response.request.url}"
            )
        else:
            error = APIError(
                f"Open Library request failed with HTTP {status}: "
                f"{response.request.url}"
            )
        if attempt >= max_retries:
            raise error
        raise RetryRequest(retry_delay(response, attempt), error)

    if status >= 400:
        raise APIError(
            f"Open Library request failed with HTTP {status}: {response.request.url}"
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise DataValidationError("Open Library returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise DataValidationError("Open Library returned a non-object response")
    return payload


def _retry_after_seconds(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After", "").strip()
    if not header:
        return None
    try:
        return max(float(header), 0.0)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(header)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max((parsed - datetime.now(UTC)).total_seconds(), 0.0)
