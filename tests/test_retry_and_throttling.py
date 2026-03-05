"""Tests for transient retries and rate limiter pacing."""

from __future__ import annotations

import time

import httpx
import pytest

from prismic_content_mcp.models import DocumentWrite
from prismic_content_mcp.prismic import (
    PrismicApiError,
    PrismicClientConfig,
    PrismicService,
)


def make_config(
    *,
    min_interval: float = 0.01,
    max_attempts: int = 3,
) -> PrismicClientConfig:
    return PrismicClientConfig(
        repository="demo-repo",
        write_api_token="write-token",
        migration_api_key="migration-key",
        content_api_token=None,
        migration_api_base_url="https://migration.prismic.io",
        asset_api_base_url="https://asset-api.prismic.io",
        content_api_base_url="https://demo-repo.cdn.prismic.io/api/v2",
        migration_min_interval_seconds=min_interval,
        retry_max_attempts=max_attempts,
        write_type_allowlist=frozenset(),
        max_batch_size=50,
    )


def make_document() -> DocumentWrite:
    return DocumentWrite.model_validate(
        {
            "title": "Doc",
            "type": "page",
            "lang": "en-us",
            "data": {"title": "Doc"},
        }
    )


@pytest.mark.asyncio
async def test_retry_transient_statuses_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(503, json={"error": "busy"}, request=request)
        return httpx.Response(200, json={"id": "doc-1"}, request=request)

    migration_client = httpx.AsyncClient(
        base_url="https://migration.prismic.io",
        transport=httpx.MockTransport(handler),
    )
    content_client = httpx.AsyncClient(base_url="https://demo-repo.cdn.prismic.io/api/v2")

    async with PrismicService(
        make_config(min_interval=0.001, max_attempts=5),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        result = await service.create_document(make_document())

    assert attempts["count"] == 3
    assert result["id"] == "doc-1"


@pytest.mark.asyncio
async def test_no_retry_on_non_transient_client_error() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(400, json={"error": "bad request"}, request=request)

    migration_client = httpx.AsyncClient(
        base_url="https://migration.prismic.io",
        transport=httpx.MockTransport(handler),
    )
    content_client = httpx.AsyncClient(base_url="https://demo-repo.cdn.prismic.io/api/v2")

    async with PrismicService(
        make_config(min_interval=0.001, max_attempts=5),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        with pytest.raises(PrismicApiError):
            await service.create_document(make_document())

    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_rate_limiter_applies_minimum_spacing_between_writes() -> None:
    timestamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timestamps.append(time.monotonic())
        return httpx.Response(200, json={"id": "ok"}, request=request)

    min_interval = 0.05
    migration_client = httpx.AsyncClient(
        base_url="https://migration.prismic.io",
        transport=httpx.MockTransport(handler),
    )
    content_client = httpx.AsyncClient(base_url="https://demo-repo.cdn.prismic.io/api/v2")

    async with PrismicService(
        make_config(min_interval=min_interval, max_attempts=2),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        await service.create_document(make_document())
        await service.create_document(make_document())

    assert len(timestamps) == 2
    assert timestamps[1] - timestamps[0] >= 0.04
