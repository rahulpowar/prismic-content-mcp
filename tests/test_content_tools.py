"""Tests for content-related MCP tool handlers."""

from __future__ import annotations

import pytest

from prismic_content_mcp.models import PrismicDocument
from prismic_content_mcp.server import (
    handle_prismic_get_document,
    handle_prismic_get_documents,
    handle_prismic_get_repository_context,
    handle_prismic_get_releases,
    handle_prismic_get_refs,
    handle_prismic_get_types,
)


class FakeContentService:
    def __init__(self) -> None:
        self.last_get_documents_args: dict[str, object] | None = None
        self.id_lookup: str | None = None
        self.id_ref: str | None = None
        self.uid_lookup: tuple[str, str] | None = None
        self.uid_ref: str | None = None
        self.refs_requested = False
        self.types_requested = False
        self.releases_requested = False
        self.context_requested = False

    async def __aenter__(self) -> "FakeContentService":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get_documents(self, **kwargs):
        self.last_get_documents_args = kwargs
        return {
            "results": [
                PrismicDocument(
                    id="doc-1",
                    type="page",
                    lang="en-us",
                    uid="home",
                    data={"title": "Home"},
                )
            ],
            "page": 1,
            "page_size": 20,
            "total_pages": 1,
            "total_results": 1,
            "next_page": None,
        }

    async def get_document_by_id(
        self,
        *,
        document_id: str,
        lang: str | None = None,
        ref: str | None = None,
    ):
        self.id_lookup = document_id
        self.id_ref = ref
        return PrismicDocument(
            id=document_id,
            type="page",
            lang=lang or "en-us",
            data={"title": "ById"},
        )

    async def get_document_by_uid(
        self,
        *,
        document_type: str,
        uid: str,
        lang: str | None = None,
        ref: str | None = None,
    ):
        self.uid_lookup = (document_type, uid)
        self.uid_ref = ref
        return PrismicDocument(
            id="uid-1",
            type=document_type,
            uid=uid,
            lang=lang or "en-us",
            data={"title": "ByUid"},
        )

    async def get_refs(self):
        self.refs_requested = True
        return [
            {"id": "master", "ref": "master-ref-1", "label": "Master", "isMasterRef": True}
        ]

    async def get_types(self):
        self.types_requested = True
        return [
            {"id": "page", "label": "Page"},
            {"id": "blog_post", "label": "Blog Post"},
        ]

    async def get_releases(self):
        self.releases_requested = True
        return [
            {
                "id": "release-q1",
                "ref": "release-ref-1",
                "label": "Q1 Release",
                "isMasterRef": False,
            }
        ]

    def get_repository_context(self):
        self.context_requested = True
        return {
            "repository": "demo-repo",
            "content_api_base_url": "https://demo-repo.cdn.prismic.io/api/v2",
            "migration_api_base_url": "https://migration.prismic.io",
            "has_content_api_token": False,
            "has_write_credentials": True,
        }


def make_content_service_factory(service: FakeContentService):
    def factory(*, require_write_credentials: bool = False):
        assert require_write_credentials is False
        return service

    return factory


@pytest.mark.asyncio
async def test_handle_get_refs_returns_refs_array() -> None:
    service = FakeContentService()

    result = await handle_prismic_get_refs(
        service_factory=make_content_service_factory(service),
    )

    assert service.refs_requested is True
    assert result["refs"][0]["id"] == "master"
    assert result["refs"][0]["ref"] == "master-ref-1"


@pytest.mark.asyncio
async def test_handle_get_releases_returns_release_refs() -> None:
    service = FakeContentService()

    result = await handle_prismic_get_releases(
        service_factory=make_content_service_factory(service),
    )

    assert service.releases_requested is True
    assert result["releases"][0]["id"] == "release-q1"
    assert result["releases"][0]["isMasterRef"] is False


@pytest.mark.asyncio
async def test_handle_get_types_returns_types_array() -> None:
    service = FakeContentService()

    result = await handle_prismic_get_types(
        service_factory=make_content_service_factory(service),
    )

    assert service.types_requested is True
    assert result["types"] == [
        {"id": "page", "label": "Page"},
        {"id": "blog_post", "label": "Blog Post"},
    ]


@pytest.mark.asyncio
async def test_handle_get_repository_context_returns_context() -> None:
    service = FakeContentService()

    result = await handle_prismic_get_repository_context(
        service_factory=make_content_service_factory(service),
    )

    assert service.context_requested is True
    assert result["context"]["repository"] == "demo-repo"
    assert result["context"]["content_api_base_url"] == "https://demo-repo.cdn.prismic.io/api/v2"
    assert result["context"]["has_write_credentials"] is True


@pytest.mark.asyncio
async def test_handle_get_documents_forwards_filters_and_serializes_results() -> None:
    service = FakeContentService()

    result = await handle_prismic_get_documents(
        type="page",
        lang="en-us",
        ref="preview-ref-123",
        page=2,
        page_size=50,
        q=["[[at(document.tags,\"news\")]]"],
        orderings="[document.first_publication_date desc]",
        routes=[{"type": "page", "path": "/:uid"}],
        service_factory=make_content_service_factory(service),
    )

    assert service.last_get_documents_args == {
        "document_type": "page",
        "lang": "en-us",
        "ref": "preview-ref-123",
        "page": 2,
        "page_size": 50,
        "q": ["[[at(document.tags,\"news\")]]"],
        "orderings": "[document.first_publication_date desc]",
        "routes": [{"type": "page", "path": "/:uid"}],
    }
    assert result["results"][0]["id"] == "doc-1"
    assert result["summary"] if False else True


@pytest.mark.asyncio
async def test_handle_get_document_by_id() -> None:
    service = FakeContentService()

    result = await handle_prismic_get_document(
        id="abc123",
        lang="fr-fr",
        ref="preview-ref-123",
        service_factory=make_content_service_factory(service),
    )

    assert service.id_lookup == "abc123"
    assert service.id_ref == "preview-ref-123"
    assert result["document"]["id"] == "abc123"
    assert result["document"]["lang"] == "fr-fr"


@pytest.mark.asyncio
async def test_handle_get_document_by_type_uid() -> None:
    service = FakeContentService()

    result = await handle_prismic_get_document(
        type="blog_post",
        uid="launch",
        lang="en-us",
        ref="preview-ref-123",
        service_factory=make_content_service_factory(service),
    )

    assert service.uid_lookup == ("blog_post", "launch")
    assert service.uid_ref == "preview-ref-123"
    assert result["document"]["type"] == "blog_post"
    assert result["document"]["uid"] == "launch"


@pytest.mark.asyncio
async def test_handle_get_document_requires_valid_selector() -> None:
    service = FakeContentService()

    with pytest.raises(ValueError):
        await handle_prismic_get_document(
            lang="en-us",
            service_factory=make_content_service_factory(service),
        )
