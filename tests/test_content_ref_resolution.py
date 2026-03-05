"""Tests for resolving Content API refs before document searches."""

from __future__ import annotations

import json

import httpx
import pytest

from prismic_content_mcp.prismic import PrismicClientConfig, PrismicService


def make_config() -> PrismicClientConfig:
    return PrismicClientConfig(
        repository="demo-repo",
        write_api_token="write-token",
        migration_api_key="migration-key",
        content_api_token=None,
        migration_api_base_url="https://migration.prismic.io",
        asset_api_base_url="https://asset-api.prismic.io",
        content_api_base_url="https://demo-repo.cdn.prismic.io/api/v2",
        migration_min_interval_seconds=0.01,
        retry_max_attempts=3,
        write_type_allowlist=frozenset(),
        max_batch_size=50,
    )


@pytest.mark.asyncio
async def test_get_documents_uses_master_ref_and_caches_it() -> None:
    counts = {"root": 0, "search": 0}

    def content_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v2", "/api/v2/"):
            counts["root"] += 1
            return httpx.Response(
                200,
                json={
                    "refs": [
                        {"id": "master", "ref": "master-ref-1", "isMasterRef": True}
                    ]
                },
                request=request,
            )

        if request.url.path == "/api/v2/documents/search":
            counts["search"] += 1
            assert request.url.params.get("ref") == "master-ref-1"
            assert (
                request.url.params.get("orderings")
                == "[document.first_publication_date desc]"
            )
            assert (
                request.url.params.get("routes")
                == json.dumps(
                    [{"type": "page", "path": "/:uid"}],
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            )
            return httpx.Response(
                200,
                json={
                    "results": [],
                    "page": 1,
                    "results_per_page": 1,
                    "total_pages": 1,
                    "total_results_size": 0,
                    "next_page": None,
                },
                request=request,
            )

        raise AssertionError(f"Unexpected path: {request.url.path}")

    migration_client = httpx.AsyncClient(base_url="https://migration.prismic.io")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(
        make_config(),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        await service.get_documents(
            page=1,
            page_size=1,
            orderings="[document.first_publication_date desc]",
            routes=[{"type": "page", "path": "/:uid"}],
        )
        await service.get_documents(
            page=1,
            page_size=1,
            orderings="[document.first_publication_date desc]",
            routes=[{"type": "page", "path": "/:uid"}],
        )

    assert counts == {"root": 1, "search": 2}


@pytest.mark.asyncio
async def test_get_documents_raises_when_master_ref_missing() -> None:
    def content_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v2", "/api/v2/"):
            return httpx.Response(
                200,
                json={"refs": [{"id": "preview", "ref": "preview-ref"}]},
                request=request,
            )

        raise AssertionError(f"Unexpected path: {request.url.path}")

    migration_client = httpx.AsyncClient(base_url="https://migration.prismic.io")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(
        make_config(),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        with pytest.raises(ValueError, match="master ref"):
            await service.get_documents(page=1, page_size=1)


@pytest.mark.asyncio
async def test_get_documents_uses_explicit_ref_without_root_lookup() -> None:
    counts = {"root": 0, "search": 0}

    def content_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v2", "/api/v2/"):
            counts["root"] += 1
            raise AssertionError("Root ref lookup should not be called with explicit ref")

        if request.url.path == "/api/v2/documents/search":
            counts["search"] += 1
            assert request.url.params.get("ref") == "preview-ref-123"
            return httpx.Response(
                200,
                json={
                    "results": [],
                    "page": 1,
                    "results_per_page": 1,
                    "total_pages": 1,
                    "total_results_size": 0,
                    "next_page": None,
                },
                request=request,
            )

        raise AssertionError(f"Unexpected path: {request.url.path}")

    migration_client = httpx.AsyncClient(base_url="https://migration.prismic.io")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(
        make_config(),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        await service.get_documents(page=1, page_size=1, ref="preview-ref-123")

    assert counts == {"root": 0, "search": 1}


@pytest.mark.asyncio
async def test_get_documents_raises_for_non_serializable_routes() -> None:
    def content_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected call for path: {request.url.path}")

    migration_client = httpx.AsyncClient(base_url="https://migration.prismic.io")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(
        make_config(),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        with pytest.raises(ValueError, match="routes must be a JSON string"):
            await service.get_documents(
                page=1,
                page_size=1,
                ref="preview-ref-123",
                routes={"type": "page", "path": object()},
            )


@pytest.mark.asyncio
async def test_get_refs_returns_refs_from_root() -> None:
    counts = {"root": 0}

    def content_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v2", "/api/v2/"):
            counts["root"] += 1
            return httpx.Response(
                200,
                json={
                    "refs": [
                        {"id": "master", "ref": "master-ref-1", "isMasterRef": True},
                        {"id": "release", "ref": "release-ref-1", "isMasterRef": False},
                    ]
                },
                request=request,
            )

        raise AssertionError(f"Unexpected path: {request.url.path}")

    migration_client = httpx.AsyncClient(base_url="https://migration.prismic.io")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(
        make_config(),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        refs = await service.get_refs()

    assert counts == {"root": 1}
    assert refs[0]["id"] == "master"
    assert refs[1]["id"] == "release"


@pytest.mark.asyncio
async def test_get_types_returns_sorted_types_from_root() -> None:
    counts = {"root": 0}

    def content_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v2", "/api/v2/"):
            counts["root"] += 1
            return httpx.Response(
                200,
                json={
                    "refs": [
                        {"id": "master", "ref": "master-ref-1", "isMasterRef": True},
                    ],
                    "types": {
                        "webinar_form": "Webinar Form",
                        "page": "Page",
                    },
                },
                request=request,
            )

        raise AssertionError(f"Unexpected path: {request.url.path}")

    migration_client = httpx.AsyncClient(base_url="https://migration.prismic.io")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(
        make_config(),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        types = await service.get_types()

    assert counts == {"root": 1}
    assert types == [
        {"id": "page", "label": "Page"},
        {"id": "webinar_form", "label": "Webinar Form"},
    ]


@pytest.mark.asyncio
async def test_get_types_raises_when_types_missing() -> None:
    def content_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v2", "/api/v2/"):
            return httpx.Response(
                200,
                json={
                    "refs": [
                        {"id": "master", "ref": "master-ref-1", "isMasterRef": True}
                    ]
                },
                request=request,
            )

        raise AssertionError(f"Unexpected path: {request.url.path}")

    migration_client = httpx.AsyncClient(base_url="https://migration.prismic.io")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(
        make_config(),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        with pytest.raises(ValueError, match="missing types"):
            await service.get_types()


@pytest.mark.asyncio
async def test_get_releases_filters_out_master_ref() -> None:
    counts = {"root": 0}

    def content_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v2", "/api/v2/"):
            counts["root"] += 1
            return httpx.Response(
                200,
                json={
                    "refs": [
                        {"id": "master", "ref": "master-ref-1", "isMasterRef": True},
                        {"id": "release", "ref": "release-ref-1", "isMasterRef": False},
                        {"id": "release-2", "ref": "release-ref-2", "isMasterRef": False},
                    ]
                },
                request=request,
            )

        raise AssertionError(f"Unexpected path: {request.url.path}")

    migration_client = httpx.AsyncClient(base_url="https://migration.prismic.io")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(
        make_config(),
        migration_client=migration_client,
        content_client=content_client,
    ) as service:
        releases = await service.get_releases()

    assert counts == {"root": 1}
    assert [release["id"] for release in releases] == ["release", "release-2"]
