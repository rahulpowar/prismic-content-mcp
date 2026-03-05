"""Tests for migration-related service methods and MCP tool handlers."""

from __future__ import annotations

import httpx
import pytest
import respx

from prismic_content_mcp.models import DocumentWrite
from prismic_content_mcp.prismic import (
    PrismicClientConfig,
    PrismicConfigurationError,
    PrismicService,
)
from prismic_content_mcp.server import (
    handle_prismic_upsert_document,
    handle_prismic_upsert_documents,
)


def make_test_config(**overrides) -> PrismicClientConfig:
    defaults = {
        "repository": "demo-repo",
        "write_api_token": "write-token",
        "migration_api_key": "migration-key",
        "content_api_token": None,
        "migration_api_base_url": "https://migration.prismic.io",
        "asset_api_base_url": "https://asset-api.prismic.io",
        "content_api_base_url": "https://demo-repo.cdn.prismic.io/api/v2",
        "migration_min_interval_seconds": 0.01,
        "retry_max_attempts": 3,
        "write_type_allowlist": frozenset(),
        "max_batch_size": 50,
        "enforce_trusted_endpoints": False,
        "upload_root": None,
        "disable_raw_q": False,
    }
    defaults.update(overrides)
    return PrismicClientConfig(**defaults)


def make_document(**overrides) -> DocumentWrite:
    payload = {
        "title": "Home",
        "type": "page",
        "lang": "en-us",
        "uid": "home",
        "data": {"title": "Home"},
    }
    payload.update(overrides)
    return DocumentWrite.model_validate(payload)


@respx.mock
@pytest.mark.asyncio
async def test_create_document_calls_post_documents_with_headers() -> None:
    route = respx.post("https://migration.prismic.io/documents").mock(
        return_value=httpx.Response(200, json={"id": "new-id"})
    )

    async with PrismicService(make_test_config()) as service:
        response = await service.create_document(make_document())

    assert route.called
    assert response["id"] == "new-id"
    request = route.calls[0].request
    assert request.headers["Repository"] == "demo-repo"
    assert request.headers["X-Api-Key"] == "migration-key"


@respx.mock
@pytest.mark.asyncio
async def test_create_document_without_migration_api_key_still_works() -> None:
    route = respx.post("https://migration.prismic.io/documents").mock(
        return_value=httpx.Response(200, json={"id": "new-id"})
    )

    async with PrismicService(make_test_config(migration_api_key=None)) as service:
        response = await service.create_document(make_document())

    assert route.called
    request = route.calls[0].request
    assert request.headers["Repository"] == "demo-repo"
    assert "X-Api-Key" not in request.headers
    assert response["id"] == "new-id"


@respx.mock
@pytest.mark.asyncio
async def test_update_document_calls_put_documents_id() -> None:
    route = respx.put("https://migration.prismic.io/documents/doc-99").mock(
        return_value=httpx.Response(200, json={"id": "doc-99"})
    )

    async with PrismicService(make_test_config()) as service:
        response = await service.update_document(
            document_id="doc-99",
            document=make_document(id="doc-99"),
        )

    assert route.called
    assert response["id"] == "doc-99"


@respx.mock
@pytest.mark.asyncio
async def test_migration_base_url_defaults_when_config_value_is_empty() -> None:
    route = respx.post("https://migration.prismic.io/documents").mock(
        return_value=httpx.Response(200, json={"id": "new-id"})
    )

    config = make_test_config(migration_api_base_url="")
    async with PrismicService(config) as service:
        response = await service.create_document(make_document())

    assert route.called
    assert response["id"] == "new-id"


@pytest.mark.asyncio
async def test_read_documents_works_without_write_credentials() -> None:
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
        if request.url.path == "/api/v2/documents/search":
            assert request.url.params.get("ref") == "master-ref-1"
            return httpx.Response(
                200,
                json={
                    "results": [],
                    "page": 1,
                    "results_per_page": 1,
                    "total_pages": 0,
                    "total_results_size": 0,
                    "next_page": None,
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    config = make_test_config(write_api_token="", migration_api_key="")
    content_client = httpx.AsyncClient(
        base_url="https://demo-repo.cdn.prismic.io/api/v2",
        transport=httpx.MockTransport(content_handler),
    )

    async with PrismicService(config, content_client=content_client) as service:
        result = await service.get_documents(page=1, page_size=1)

    assert result["results"] == []
    assert result["page"] == 1


@pytest.mark.asyncio
async def test_write_fails_without_write_credentials() -> None:
    config = make_test_config(write_api_token="", migration_api_key="")

    async with PrismicService(config) as service:
        with pytest.raises(
            PrismicConfigurationError,
            match="Missing required environment variables",
        ):
            await service.create_document(make_document())


def test_migration_payload_allowlist_contains_expected_keys_only() -> None:
    payload = PrismicService.to_migration_payload(
        make_document(uid=None, alternate_language_id=None)
    )

    assert set(payload.keys()) == {"title", "type", "lang", "data"}


class FakeWriteService:
    def __init__(self) -> None:
        self.validated_batch_size: int | None = None
        self.upsert_calls = 0

    async def __aenter__(self) -> "FakeWriteService":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def validate_batch_size(self, count: int) -> None:
        self.validated_batch_size = count

    def plan_upsert(self, document: DocumentWrite):
        if document.uid == "bad":
            raise ValueError("bad document")
        return {
            "id": document.id or "",
            "status": "updated" if document.id else "created",
            "method": "PUT" if document.id else "POST",
            "endpoint": "documents" if not document.id else f"documents/{document.id}",
            "payload": {},
        }

    async def upsert_document(self, document: DocumentWrite):
        self.upsert_calls += 1
        if document.uid == "bad":
            raise ValueError("bad document")
        if document.uid == "explode":
            raise RuntimeError("unexpected crash")
        return {
            "id": document.id or f"gen-{self.upsert_calls}",
            "status": "updated" if document.id else "created",
            "raw": {"ok": True},
        }


class LeakyFakeWriteService(FakeWriteService):
    async def upsert_document(self, document: DocumentWrite):
        raise ValueError("token=super-secret-token")


def make_write_service_factory(service: FakeWriteService):
    def factory(*, require_write_credentials: bool = False):
        assert require_write_credentials is True
        return service

    return factory


@pytest.mark.asyncio
async def test_handle_upsert_document_dry_run_returns_plan() -> None:
    service = FakeWriteService()

    result = await handle_prismic_upsert_document(
        document=make_document(id="doc-1"),
        dry_run=True,
        service_factory=make_write_service_factory(service),
    )

    assert result["status"] == "updated"
    assert result["dry_run"] is True
    assert result["would_call"]["method"] == "PUT"


@pytest.mark.asyncio
async def test_handle_upsert_documents_returns_batch_summary() -> None:
    service = FakeWriteService()
    docs = [
        make_document(uid="good-1"),
        make_document(id="doc-2", uid="good-2"),
        make_document(uid="bad"),
    ]

    result = await handle_prismic_upsert_documents(
        documents=docs,
        fail_fast=False,
        dry_run=False,
        service_factory=make_write_service_factory(service),
    )

    assert service.validated_batch_size == 3
    assert result["summary"] == {"created": 1, "updated": 1, "failed": 1}
    assert len(result["results"]) == 3
    assert result["results"][2]["ok"] is False
    assert result["results"][2]["error"]["type"] == "ValueError"
    assert result["results"][2]["error"]["message"] == "Input validation failed"


@pytest.mark.asyncio
async def test_handle_upsert_documents_fail_fast_reraises_recoverable_error() -> None:
    service = FakeWriteService()
    docs = [
        make_document(uid="good-1"),
        make_document(uid="bad"),
    ]

    with pytest.raises(ValueError, match="bad document"):
        await handle_prismic_upsert_documents(
            documents=docs,
            fail_fast=True,
            dry_run=False,
            service_factory=make_write_service_factory(service),
        )


@pytest.mark.asyncio
async def test_handle_upsert_documents_unexpected_error_bubbles() -> None:
    service = FakeWriteService()
    docs = [make_document(uid="explode")]

    with pytest.raises(RuntimeError, match="unexpected crash"):
        await handle_prismic_upsert_documents(
            documents=docs,
            fail_fast=False,
            dry_run=False,
            service_factory=make_write_service_factory(service),
        )


@pytest.mark.asyncio
async def test_handle_upsert_documents_error_payload_omits_raw_secret() -> None:
    service = LeakyFakeWriteService()
    docs = [make_document(uid="bad")]

    result = await handle_prismic_upsert_documents(
        documents=docs,
        fail_fast=False,
        dry_run=False,
        service_factory=make_write_service_factory(service),
    )

    assert result["summary"]["failed"] == 1
    error_payload = result["results"][0]["error"]
    assert error_payload["message"] == "Input validation failed"
    assert "super-secret-token" not in str(error_payload)
