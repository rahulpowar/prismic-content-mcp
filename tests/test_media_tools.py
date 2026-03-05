"""Tests for Prismic Asset API service methods and MCP media handlers."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from prismic_content_mcp.prismic import PrismicClientConfig, PrismicService
from prismic_content_mcp.server import handle_prismic_add_media, handle_prismic_get_media


def make_config(**overrides) -> PrismicClientConfig:
    defaults = {
        "repository": "demo-repo",
        "write_api_token": "write-token",
        "migration_api_key": "",
        "content_api_token": None,
        "migration_api_base_url": "https://migration.prismic.io",
        "asset_api_base_url": "https://asset-api.prismic.io",
        "content_api_base_url": "https://demo-repo.cdn.prismic.io/api/v2",
        "migration_min_interval_seconds": 0.01,
        "retry_max_attempts": 3,
        "write_type_allowlist": frozenset(),
        "max_batch_size": 50,
    }
    defaults.update(overrides)
    return PrismicClientConfig(**defaults)


@respx.mock
@pytest.mark.asyncio
async def test_get_media_calls_asset_api_with_query_params_and_headers() -> None:
    route = respx.get("https://asset-api.prismic.io/assets").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "asset-1", "filename": "hero.png"}],
        )
    )

    async with PrismicService(make_config()) as service:
        payload = await service.get_media(
            asset_type="image",
            limit=25,
            cursor="asset-0",
            keyword="hero",
        )

    assert route.called
    request = route.calls[0].request
    assert request.url.params["assetType"] == "image"
    assert request.url.params["limit"] == "25"
    assert request.url.params["cursor"] == "asset-0"
    assert request.url.params["keyword"] == "hero"
    assert request.headers["Repository"] == "demo-repo"
    assert request.headers["Authorization"] == "Bearer write-token"
    assert payload[0]["id"] == "asset-1"


@respx.mock
@pytest.mark.asyncio
async def test_add_media_posts_multipart_with_optional_metadata(tmp_path: Path) -> None:
    file_path = tmp_path / "hero.png"
    file_path.write_bytes(b"png-bytes")

    route = respx.post("https://asset-api.prismic.io/assets").mock(
        return_value=httpx.Response(
            200,
            json={"id": "asset-99", "filename": "hero.png"},
        )
    )

    async with PrismicService(make_config()) as service:
        payload = await service.add_media(
            file_path=str(file_path),
            notes="hero note",
            credits="team",
            alt="hero image",
        )

    assert route.called
    request = route.calls[0].request
    assert "multipart/form-data" in request.headers["Content-Type"]
    assert b'name="file"; filename="hero.png"' in request.content
    assert b'name="notes"' in request.content
    assert b"hero note" in request.content
    assert b'name="credits"' in request.content
    assert b"team" in request.content
    assert b'name="alt"' in request.content
    assert b"hero image" in request.content
    assert payload["id"] == "asset-99"


@pytest.mark.asyncio
async def test_add_media_raises_for_missing_file() -> None:
    async with PrismicService(make_config()) as service:
        with pytest.raises(FileNotFoundError):
            await service.add_media(file_path="/path/that/does/not/exist.png")


class FakeMediaService:
    def __init__(self) -> None:
        self.get_args: dict[str, object] | None = None
        self.add_args: dict[str, object] | None = None

    async def __aenter__(self) -> "FakeMediaService":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get_media(self, **kwargs):
        self.get_args = kwargs
        return [{"id": "asset-1"}]

    async def add_media(self, **kwargs):
        self.add_args = kwargs
        return {"id": "asset-2"}


def make_media_service_factory(service: FakeMediaService):
    def factory(*, require_write_credentials: bool = False):
        assert require_write_credentials is False
        return service

    return factory


@pytest.mark.asyncio
async def test_handle_get_media_forwards_args() -> None:
    service = FakeMediaService()

    result = await handle_prismic_get_media(
        asset_type="image",
        limit=10,
        cursor="asset-1",
        keyword="hero",
        service_factory=make_media_service_factory(service),
    )

    assert service.get_args == {
        "asset_type": "image",
        "limit": 10,
        "cursor": "asset-1",
        "keyword": "hero",
    }
    assert result["media"][0]["id"] == "asset-1"


@pytest.mark.asyncio
async def test_handle_add_media_forwards_args() -> None:
    service = FakeMediaService()

    result = await handle_prismic_add_media(
        file_path="/tmp/example.png",
        notes="note",
        credits="credit",
        alt="alt",
        service_factory=make_media_service_factory(service),
    )

    assert service.add_args == {
        "file_path": "/tmp/example.png",
        "notes": "note",
        "credits": "credit",
        "alt": "alt",
    }
    assert result["media"]["id"] == "asset-2"
