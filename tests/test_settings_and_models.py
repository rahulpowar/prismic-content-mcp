"""Tests for environment settings loading and model validation rules."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from prismic_content_mcp.models import DocumentWrite, PrismicDocument
from prismic_content_mcp.prismic import (
    PrismicApiError,
    PrismicClientConfig,
    PrismicConfigurationError,
    PrismicService,
    is_trusted_prismic_url,
    load_prismic_client_config,
    sanitize_url_query_parameters,
    validate_required_asset_credentials,
    validate_required_credentials,
)


def test_load_prismic_client_config_derives_default_content_url() -> None:
    config = load_prismic_client_config(
        env={
            "PRISMIC_REPOSITORY": "demo-repo",
            "PRISMIC_WRITE_API_TOKEN": "write-token",
            "PRISMIC_MIGRATION_API_KEY": "migration-key",
        }
    )

    assert config.repository == "demo-repo"
    assert config.content_api_base_url == "https://demo-repo.cdn.prismic.io/api/v2"
    assert config.migration_api_base_url == "https://migration.prismic.io"
    assert config.asset_api_base_url == "https://asset-api.prismic.io"
    assert config.migration_min_interval_seconds == 2.5
    assert config.retry_max_attempts == 5
    assert config.max_batch_size == 50
    assert config.enforce_trusted_endpoints is False
    assert config.upload_root is None
    assert config.disable_raw_q is False


def test_load_prismic_client_config_defaults_blank_operational_values() -> None:
    config = load_prismic_client_config(
        env={
            "PRISMIC_REPOSITORY": "demo-repo",
            "PRISMIC_WRITE_API_TOKEN": "write-token",
            "PRISMIC_MIGRATION_API_KEY": "migration-key",
            "PRISMIC_MIGRATION_MIN_INTERVAL_SECONDS": "   ",
            "PRISMIC_RETRY_MAX_ATTEMPTS": "   ",
            "PRISMIC_MAX_BATCH_SIZE": "   ",
        }
    )

    assert config.migration_min_interval_seconds == 2.5
    assert config.retry_max_attempts == 5
    assert config.max_batch_size == 50


def test_load_prismic_client_config_defaults_blank_migration_base_url() -> None:
    config = load_prismic_client_config(
        env={
            "PRISMIC_REPOSITORY": "demo-repo",
            "PRISMIC_WRITE_API_TOKEN": "write-token",
            "PRISMIC_MIGRATION_API_KEY": "migration-key",
            "PRISMIC_MIGRATION_API_BASE_URL": "   ",
        }
    )

    assert config.migration_api_base_url == "https://migration.prismic.io"


def test_load_prismic_client_config_defaults_blank_asset_base_url() -> None:
    config = load_prismic_client_config(
        env={
            "PRISMIC_REPOSITORY": "demo-repo",
            "PRISMIC_WRITE_API_TOKEN": "write-token",
            "PRISMIC_MIGRATION_API_KEY": "migration-key",
            "PRISMIC_ASSET_API_BASE_URL": "   ",
        }
    )

    assert config.asset_api_base_url == "https://asset-api.prismic.io"


def test_load_prismic_client_config_respects_document_api_url_override() -> None:
    config = load_prismic_client_config(
        env={
            "PRISMIC_REPOSITORY": "demo-repo",
            "PRISMIC_WRITE_API_TOKEN": "write-token",
            "PRISMIC_MIGRATION_API_KEY": "migration-key",
            "PRISMIC_DOCUMENT_API_URL": "https://override.example.com/api/v2",
        }
    )

    assert config.content_api_base_url == "https://override.example.com/api/v2"


def test_load_prismic_client_config_reads_upload_root_and_q_safe_mode() -> None:
    config = load_prismic_client_config(
        env={
            "PRISMIC_REPOSITORY": "demo-repo",
            "PRISMIC_UPLOAD_ROOT": "/tmp/uploads",
            "PRISMIC_DISABLE_RAW_Q": "1",
        }
    )

    assert config.upload_root == "/tmp/uploads"
    assert config.disable_raw_q is True


def test_load_prismic_client_config_derives_content_url_from_cdn_host_repository() -> None:
    config = load_prismic_client_config(
        env={
            "PRISMIC_REPOSITORY": "https://demo-repo.cdn.prismic.io/api/v2",
            "PRISMIC_WRITE_API_TOKEN": "write-token",
            "PRISMIC_MIGRATION_API_KEY": "migration-key",
        }
    )

    assert config.content_api_base_url == "https://demo-repo.cdn.prismic.io/api/v2"


def test_load_prismic_client_config_strict_mode_rejects_untrusted_override() -> None:
    with pytest.raises(
        PrismicConfigurationError,
        match="PRISMIC_ENFORCE_TRUSTED_ENDPOINTS",
    ):
        load_prismic_client_config(
            env={
                "PRISMIC_REPOSITORY": "demo-repo",
                "PRISMIC_ENFORCE_TRUSTED_ENDPOINTS": "1",
                "PRISMIC_DOCUMENT_API_URL": "https://evil.example.com/api/v2",
            }
        )


def test_load_prismic_client_config_warns_on_untrusted_override(caplog) -> None:
    caplog.set_level("WARNING")

    config = load_prismic_client_config(
        env={
            "PRISMIC_REPOSITORY": "demo-repo",
            "PRISMIC_DOCUMENT_API_URL": "https://evil.example.com/api/v2",
        }
    )

    assert config.content_api_base_url == "https://evil.example.com/api/v2"
    assert "non-Prismic host" in caplog.text
    assert "PRISMIC_DOCUMENT_API_URL" in caplog.text


def test_sanitize_url_query_parameters_redacts_sensitive_keys() -> None:
    url = (
        "https://demo.cdn.prismic.io/api/v2/documents/search?"
        "access_token=abc123&foo=bar&api_key=key123&Authorization=secret"
    )
    sanitized = sanitize_url_query_parameters(url)

    assert "access_token=%5BREDACTED%5D" in sanitized
    assert "api_key=%5BREDACTED%5D" in sanitized
    assert "Authorization=%5BREDACTED%5D" in sanitized
    assert "foo=bar" in sanitized
    assert "abc123" not in sanitized
    assert "key123" not in sanitized
    assert "secret" not in sanitized


def test_is_trusted_prismic_url_matches_expected_hosts() -> None:
    assert is_trusted_prismic_url("https://migration.prismic.io") is True
    assert is_trusted_prismic_url("https://demo-repo.cdn.prismic.io/api/v2") is True
    assert is_trusted_prismic_url("https://evil.example.com") is False


def test_prismic_api_error_redacts_sensitive_query_params_in_url() -> None:
    request = httpx.Request(
        "GET",
        "https://demo.cdn.prismic.io/api/v2/documents/search?access_token=abc123&foo=bar",
    )
    response = httpx.Response(401, request=request, json={"error": "unauthorized"})

    error = PrismicApiError.from_response(response)

    assert "access_token=%5BREDACTED%5D" in error.url
    assert "foo=bar" in error.url
    assert "abc123" not in error.url


@pytest.mark.asyncio
async def test_build_content_client_derives_base_url_when_content_url_blank() -> None:
    config = PrismicClientConfig(
        repository="demo-repo",
        write_api_token="write-token",
        migration_api_key="migration-key",
        content_api_token=None,
        migration_api_base_url="https://migration.prismic.io",
        asset_api_base_url="https://asset-api.prismic.io",
        content_api_base_url="   ",
        migration_min_interval_seconds=2.5,
        retry_max_attempts=5,
        write_type_allowlist=frozenset(),
        max_batch_size=50,
        enforce_trusted_endpoints=False,
        upload_root=None,
        disable_raw_q=False,
    )

    client = PrismicService._build_content_client(config=config, timeout_seconds=5.0)
    try:
        assert str(client.base_url) == "https://demo-repo.cdn.prismic.io/api/v2/"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_repository_context_returns_non_secret_metadata() -> None:
    config = PrismicClientConfig(
        repository="demo-repo",
        write_api_token="",
        migration_api_key="",
        content_api_token="content-token",
        migration_api_base_url="https://migration.prismic.io",
        asset_api_base_url="https://asset-api.prismic.io",
        content_api_base_url="https://demo-repo.cdn.prismic.io/api/v2",
        migration_min_interval_seconds=2.5,
        retry_max_attempts=5,
        write_type_allowlist=frozenset(),
        max_batch_size=50,
        enforce_trusted_endpoints=False,
        upload_root=None,
        disable_raw_q=False,
    )
    content_client = httpx.AsyncClient(base_url="https://demo-repo.cdn.prismic.io/api/v2")

    async with PrismicService(config, content_client=content_client) as service:
        context = service.get_repository_context()

    assert context["repository"] == "demo-repo"
    assert context["content_api_base_url"] == "https://demo-repo.cdn.prismic.io/api/v2"
    assert context["migration_api_base_url"] == "https://migration.prismic.io"
    assert context["asset_api_base_url"] == "https://asset-api.prismic.io"
    assert context["has_content_api_token"] is True
    assert context["has_write_credentials"] is False
    assert context["has_asset_credentials"] is False
    assert context["endpoint_trust"]["content"]["is_trusted"] is True
    assert context["endpoint_trust"]["migration"]["is_trusted"] is True
    assert context["endpoint_trust"]["asset"]["is_trusted"] is True
    assert context["upload_root_configured"] is False
    assert context["disable_raw_q"] is False


@pytest.mark.asyncio
async def test_repository_context_reports_write_enabled_without_migration_api_key() -> None:
    config = PrismicClientConfig(
        repository="demo-repo",
        write_api_token="write-token",
        migration_api_key=None,
        content_api_token=None,
        migration_api_base_url="https://migration.prismic.io",
        asset_api_base_url="https://asset-api.prismic.io",
        content_api_base_url="https://demo-repo.cdn.prismic.io/api/v2",
        migration_min_interval_seconds=2.5,
        retry_max_attempts=5,
        write_type_allowlist=frozenset(),
        max_batch_size=50,
        enforce_trusted_endpoints=False,
        upload_root=None,
        disable_raw_q=False,
    )
    content_client = httpx.AsyncClient(base_url="https://demo-repo.cdn.prismic.io/api/v2")

    async with PrismicService(config, content_client=content_client) as service:
        context = service.get_repository_context()

    assert context["has_write_credentials"] is True


def test_validate_required_credentials_raises_for_missing_values() -> None:
    config = PrismicClientConfig(
        repository="",
        write_api_token="",
        migration_api_key="",
        content_api_token=None,
        migration_api_base_url="https://migration.prismic.io",
        asset_api_base_url="https://asset-api.prismic.io",
        content_api_base_url="https://demo.cdn.prismic.io/api/v2",
        migration_min_interval_seconds=2.5,
        retry_max_attempts=5,
        write_type_allowlist=frozenset(),
        max_batch_size=50,
        enforce_trusted_endpoints=False,
        upload_root=None,
        disable_raw_q=False,
    )

    with pytest.raises(PrismicConfigurationError) as exc:
        validate_required_credentials(config)

    message = str(exc.value)
    assert "PRISMIC_REPOSITORY" in message
    assert "PRISMIC_WRITE_API_TOKEN" in message


def test_validate_required_credentials_allows_missing_migration_api_key() -> None:
    config = PrismicClientConfig(
        repository="demo-repo",
        write_api_token="write-token",
        migration_api_key=None,
        content_api_token=None,
        migration_api_base_url="https://migration.prismic.io",
        asset_api_base_url="https://asset-api.prismic.io",
        content_api_base_url="https://demo.cdn.prismic.io/api/v2",
        migration_min_interval_seconds=2.5,
        retry_max_attempts=5,
        write_type_allowlist=frozenset(),
        max_batch_size=50,
        enforce_trusted_endpoints=False,
        upload_root=None,
        disable_raw_q=False,
    )

    validate_required_credentials(config)


def test_validate_required_asset_credentials_raises_for_missing_values() -> None:
    config = PrismicClientConfig(
        repository="",
        write_api_token="",
        migration_api_key="migration-key",
        content_api_token=None,
        migration_api_base_url="https://migration.prismic.io",
        asset_api_base_url="https://asset-api.prismic.io",
        content_api_base_url="https://demo.cdn.prismic.io/api/v2",
        migration_min_interval_seconds=2.5,
        retry_max_attempts=5,
        write_type_allowlist=frozenset(),
        max_batch_size=50,
        enforce_trusted_endpoints=False,
        upload_root=None,
        disable_raw_q=False,
    )

    with pytest.raises(PrismicConfigurationError) as exc:
        validate_required_asset_credentials(config)

    message = str(exc.value)
    assert "PRISMIC_REPOSITORY" in message
    assert "PRISMIC_WRITE_API_TOKEN" in message


def test_document_write_rejects_blank_required_fields() -> None:
    with pytest.raises(ValidationError):
        DocumentWrite.model_validate(
            {
                "title": "   ",
                "type": "page",
                "lang": "en-us",
                "data": {},
            }
        )


def test_document_write_rejects_non_dict_data() -> None:
    with pytest.raises(ValidationError):
        DocumentWrite.model_validate(
            {
                "title": "Home",
                "type": "page",
                "lang": "en-us",
                "data": [],
            }
        )


def test_prismic_document_allows_extra_fields_for_round_trip() -> None:
    document = PrismicDocument.model_validate(
        {
            "id": "abc",
            "type": "page",
            "lang": "en-us",
            "data": {"title": "Hello"},
            "first_publication_date": "2026-01-01T00:00:00+0000",
        }
    )

    assert document.id == "abc"
    assert document.model_dump()["first_publication_date"] == "2026-01-01T00:00:00+0000"
