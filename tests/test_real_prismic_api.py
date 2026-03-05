"""Opt-in integration tests that can hit real Prismic upstream APIs."""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest

from prismic_content_mcp.models import DocumentWrite
from prismic_content_mcp.prismic import PrismicConfigurationError, PrismicService, load_prismic_client_config


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip() == "1"


def _require_live_tests_enabled() -> None:
    if not _env_flag("PRISMIC_RUN_LIVE_TESTS"):
        pytest.skip("Set PRISMIC_RUN_LIVE_TESTS=1 to run live Prismic integration tests")


@pytest.mark.asyncio
async def test_live_content_api_document_listing() -> None:
    """Hit the real Content API when explicitly enabled."""

    _require_live_tests_enabled()

    config = load_prismic_client_config(validate_credentials=False)
    if not config.repository:
        pytest.skip("PRISMIC_REPOSITORY is required for live content API test")
    if not config.content_api_base_url:
        pytest.skip("PRISMIC_DOCUMENT_API_URL or PRISMIC_REPOSITORY is required")

    async with PrismicService(config) as service:
        result = await service.get_documents(page=1, page_size=1)

    assert "results" in result
    assert "page" in result
    assert "total_pages" in result


@pytest.mark.asyncio
async def test_live_migration_api_upsert_create() -> None:
    """Hit the real Migration API write flow when explicitly enabled."""

    _require_live_tests_enabled()
    if not _env_flag("PRISMIC_RUN_LIVE_WRITE_TESTS"):
        pytest.skip("Set PRISMIC_RUN_LIVE_WRITE_TESTS=1 to run live migration write test")

    write_type = os.getenv("PRISMIC_LIVE_TEST_WRITE_TYPE", "").strip()
    if not write_type:
        pytest.skip("PRISMIC_LIVE_TEST_WRITE_TYPE is required for live write test")

    write_lang = os.getenv("PRISMIC_LIVE_TEST_WRITE_LANG", "en-us").strip() or "en-us"

    try:
        config = load_prismic_client_config(validate_credentials=True)
    except PrismicConfigurationError as exc:
        pytest.skip(str(exc))

    unique_uid = f"mcp-live-{int(time.time())}-{uuid4().hex[:8]}"
    document = DocumentWrite(
        title=f"MCP Live Test {unique_uid}",
        type=write_type,
        lang=write_lang,
        uid=unique_uid,
        data={"title": [{"type": "heading1", "text": f"Live {unique_uid}"}]},
    )

    async with PrismicService(config) as service:
        result = await service.upsert_document(document)

    assert result["status"] == "created"
    assert result["id"]


@pytest.mark.asyncio
async def test_live_asset_api_list_media() -> None:
    """Hit the real Asset API list endpoint when explicitly enabled."""

    _require_live_tests_enabled()

    config = load_prismic_client_config(validate_credentials=False)
    if not config.repository:
        pytest.skip("PRISMIC_REPOSITORY is required for live asset API test")
    if not config.write_api_token:
        pytest.skip("PRISMIC_WRITE_API_TOKEN is required for live asset API test")

    async with PrismicService(config) as service:
        result = await service.get_media(limit=1)

    assert isinstance(result, list | dict)
