"""Asynchronous Open Library REST client."""

import asyncio
import logging
from typing import Any

import httpx

from config import Settings, get_settings
from exceptions import APIError

from .common import (
    RetryRequest,
    client_kwargs,
    non_negative_int,
    positive_int,
    read_payload,
    required_text,
    resource_key,
    transport_failure,
)

logger = logging.getLogger(__name__)


class AsyncLibraryClient:
    """Asynchronous client for the Open Library read APIs."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = httpx.AsyncClient(**client_kwargs(self._settings))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncLibraryClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def search(self, query: str, page: int = 1) -> dict[str, Any]:
        query = required_text(query, "query")
        page = positive_int(page, "page")
        return await self._request("/search.json", {"q": query, "page": page})

    async def get_work(self, work_id: str) -> dict[str, Any]:
        key = resource_key(work_id, "work_id")
        return await self._request(f"/works/{key}.json")

    async def get_author_works(self, author_id: str) -> dict[str, Any]:
        key = resource_key(author_id, "author_id")
        return await self._request(f"/authors/{key}/works.json")

    async def get_subject(
        self,
        subject: str,
        limit: int,
        offset: int = 0,
    ) -> dict[str, Any]:
        subject = required_text(subject, "subject")
        limit = positive_int(limit, "limit")
        offset = non_negative_int(offset, "offset")
        return await self._request(
            f"/subjects/{resource_key(subject, 'subject')}.json",
            {"limit": limit, "offset": offset},
        )

    async def get_subject_pages(
        self,
        subject: str,
        page_depth: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        page_depth = positive_int(page_depth, "page_depth")
        limit = positive_int(limit, "limit")
        return list(
            await asyncio.gather(
                *[
                    self.get_subject(subject, limit=limit, offset=index * limit)
                    for index in range(page_depth)
                ]
            )
        )

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempts = max(self._settings.max_retries, 1)
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.get(path, params=params)
            except httpx.TransportError as error:
                delay = transport_failure(error, attempt, attempts)
                logger.warning(
                    "Open Library request failed (attempt %s/%s): %s",
                    attempt,
                    attempts,
                    error,
                )
                await asyncio.sleep(delay)
                continue
            try:
                return read_payload(response, attempt, attempts)
            except RetryRequest as retry:
                logger.warning(
                    "Open Library request failed (attempt %s/%s): %s",
                    attempt,
                    attempts,
                    retry.error,
                )
                await asyncio.sleep(retry.delay)
        raise APIError("Open Library request failed")
