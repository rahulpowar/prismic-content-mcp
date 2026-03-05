"""Tests for Prismic Custom Types API service methods."""

from __future__ import annotations

import httpx
import pytest
import respx

from prismic_content_mcp.prismic import PrismicClientConfig, PrismicService


def make_config(**overrides: object) -> PrismicClientConfig:
    defaults = {
        "repository": "demo-repo",
        "write_api_token": "write-token",
        "migration_api_key": None,
        "content_api_token": None,
        "migration_api_base_url": "https://migration.prismic.io",
        "asset_api_base_url": "https://asset-api.prismic.io",
        "custom_types_api_base_url": "https://customtypes.prismic.io",
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


@respx.mock
@pytest.mark.asyncio
async def test_get_all_custom_types_calls_expected_endpoint_and_headers() -> None:
    route = respx.get("https://customtypes.prismic.io/customtypes").mock(
        return_value=httpx.Response(200, json=[{"id": "page", "label": "Page", "json": {}}])
    )

    async with PrismicService(make_config()) as service:
        payload = await service.get_all_custom_type_models()

    assert route.called
    request = route.calls[0].request
    assert request.headers["Repository"] == "demo-repo"
    assert request.headers["Authorization"] == "Bearer write-token"
    assert payload[0]["id"] == "page"


@respx.mock
@pytest.mark.asyncio
async def test_insert_and_update_custom_type_use_expected_paths() -> None:
    insert_route = respx.post("https://customtypes.prismic.io/customtypes/insert").mock(
        return_value=httpx.Response(201)
    )
    update_route = respx.post("https://customtypes.prismic.io/customtypes/update").mock(
        return_value=httpx.Response(204)
    )
    custom_type = {
        "id": "landing_page",
        "label": "Landing Page",
        "repeatable": True,
        "json": {"Main": {}},
    }

    async with PrismicService(make_config()) as service:
        inserted = await service.insert_custom_type_model(custom_type=custom_type)
        updated = await service.update_custom_type_model(custom_type=custom_type)

    assert insert_route.called
    assert update_route.called
    assert inserted["status"] == "created"
    assert inserted["id"] == "landing_page"
    assert updated["status"] == "updated"
    assert updated["id"] == "landing_page"


@respx.mock
@pytest.mark.asyncio
async def test_get_and_update_shared_slice_use_expected_paths() -> None:
    get_route = respx.get("https://customtypes.prismic.io/slices/hero_banner").mock(
        return_value=httpx.Response(200, json={"id": "hero_banner", "name": "Hero Banner"})
    )
    update_route = respx.post("https://customtypes.prismic.io/slices/update").mock(
        return_value=httpx.Response(204)
    )
    shared_slice = {
        "id": "hero_banner",
        "name": "Hero Banner",
        "variations": [],
    }

    async with PrismicService(make_config()) as service:
        fetched = await service.get_shared_slice_model(slice_id="hero_banner")
        updated = await service.update_shared_slice_model(shared_slice=shared_slice)

    assert get_route.called
    assert update_route.called
    assert fetched["id"] == "hero_banner"
    assert updated["status"] == "updated"


@pytest.mark.asyncio
async def test_insert_custom_type_requires_string_id() -> None:
    async with PrismicService(make_config()) as service:
        with pytest.raises(ValueError, match="custom_type.id"):
            await service.insert_custom_type_model(custom_type={"id": None, "json": {}})


def test_summarize_custom_type_schema_includes_required_and_slice_choices() -> None:
    custom_type = {
        "id": "page",
        "label": "Page",
        "repeatable": True,
        "json": {
            "Main": {
                "title": {
                    "type": "StructuredText",
                    "config": {"label": "Title", "required": True},
                },
                "slices": {
                    "type": "Slices",
                    "config": {
                        "choices": {
                            "hero_banner": {
                                "type": "SharedSlice",
                                "fieldset": "Hero Banner",
                                "description": "Hero section",
                                "variations": [
                                    {"id": "default", "name": "Default"}
                                ],
                            }
                        }
                    },
                },
            }
        },
    }

    schema = PrismicService.summarize_custom_type_schema(custom_type)

    assert schema["id"] == "page"
    assert schema["field_count"] == 2
    assert schema["shared_slice_count"] == 1
    title_field = schema["tabs"][0]["fields"][0]
    slices_field = schema["tabs"][0]["fields"][1]
    assert title_field["required"] is True
    assert slices_field["shared_slices"][0]["id"] == "hero_banner"
