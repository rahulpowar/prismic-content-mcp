"""Prismic HTTP clients, configuration, and shared transport utilities."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx
from aiolimiter import AsyncLimiter

from .models import DocumentWrite, PrismicDocument


DEFAULT_MIGRATION_API_BASE_URL = "https://migration.prismic.io"
DEFAULT_ASSET_API_BASE_URL = "https://asset-api.prismic.io"
DEFAULT_MIGRATION_MIN_INTERVAL_SECONDS = 2.5
DEFAULT_RETRY_MAX_ATTEMPTS = 5
DEFAULT_MAX_BATCH_SIZE = 50
TRANSIENT_RETRY_STATUS_CODES = {429, 503, 504}
CONTENT_SEARCH_ENDPOINT = "documents/search"
MIGRATION_DOCUMENTS_ENDPOINT = "documents"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_ENFORCE_TRUSTED_ENDPOINTS = False
DEFAULT_DISABLE_RAW_Q = False
SENSITIVE_QUERY_PARAM_NAMES = frozenset(
    {"access_token", "token", "api_key", "key", "authorization"}
)
MIGRATION_WRITE_FIELD_ALLOWLIST = {
    "id",
    "title",
    "type",
    "lang",
    "uid",
    "alternate_language_id",
    "data",
}

logger = logging.getLogger(__name__)


class PrismicConfigurationError(ValueError):
    """Raised when required Prismic environment configuration is missing."""


class PrismicApiError(RuntimeError):
    """Raised when an upstream Prismic API call fails."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        response_text: str,
        response_json: Any | None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.response_text = response_text
        self.response_json = response_json
        detail = response_json if response_json is not None else response_text
        super().__init__(
            f"Prismic API request failed ({status_code}) for {url} with detail: {detail}"
        )

    @classmethod
    def from_response(cls, response: httpx.Response) -> "PrismicApiError":
        """Build a structured API error from an HTTP response."""

        try:
            response_json: Any | None = response.json()
        except ValueError:
            response_json = None

        return cls(
            status_code=response.status_code,
            url=sanitize_url_query_parameters(str(response.request.url)),
            response_text=response.text,
            response_json=response_json,
        )


def _read_env(env: Mapping[str, str], key: str) -> str:
    """Read and normalize a string value from environment-like mappings."""

    return env.get(key, "").strip()


def _read_float_env(env: Mapping[str, str], key: str, default: float) -> float:
    """Read and validate a positive float setting from env-like mappings."""

    raw_value = _read_env(env, key)
    if not raw_value:
        return default

    try:
        parsed = float(raw_value)
    except ValueError as exc:
        raise PrismicConfigurationError(f"{key} must be a float") from exc

    if parsed <= 0:
        raise PrismicConfigurationError(f"{key} must be > 0")
    return parsed


def _read_int_env(env: Mapping[str, str], key: str, default: int) -> int:
    """Read and validate a positive integer setting from env-like mappings."""

    raw_value = _read_env(env, key)
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise PrismicConfigurationError(f"{key} must be an integer") from exc

    if parsed < 1:
        raise PrismicConfigurationError(f"{key} must be >= 1")
    return parsed


def _read_csv_set_env(env: Mapping[str, str], key: str) -> frozenset[str]:
    """Read comma-separated values into a normalized set."""

    raw_value = _read_env(env, key)
    if not raw_value:
        return frozenset()

    values = [item.strip() for item in raw_value.split(",")]
    normalized = {item for item in values if item}
    return frozenset(normalized)


def _read_bool_env(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    """Read boolean settings with common truthy/falsey string forms."""

    raw_value = _read_env(env, key)
    if not raw_value:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise PrismicConfigurationError(
        f"{key} must be one of: 1,true,yes,on,0,false,no,off"
    )


def _extract_url_host(url: str) -> str:
    """Extract normalized hostname from URL-like input."""

    normalized = url.strip()
    if not normalized:
        return ""
    parsed = urlsplit(normalized if "://" in normalized else f"//{normalized}")
    return (parsed.hostname or "").strip().lower().rstrip(".")


def is_trusted_prismic_url(url: str) -> bool:
    """Return True when URL host belongs to prismic.io."""

    host = _extract_url_host(url)
    if not host:
        return False
    return host == "prismic.io" or host.endswith(".prismic.io")


def sanitize_url_query_parameters(url: str) -> str:
    """Redact sensitive query-parameter values from a URL string."""

    if not url:
        return url

    try:
        split = urlsplit(url)
        if not split.query:
            return url

        redacted_pairs: list[tuple[str, str]] = []
        for key, value in parse_qsl(split.query, keep_blank_values=True):
            if key.strip().lower() in SENSITIVE_QUERY_PARAM_NAMES:
                redacted_pairs.append((key, "[REDACTED]"))
            else:
                redacted_pairs.append((key, value))

        new_query = urlencode(redacted_pairs, doseq=True)
        return urlunsplit(
            (
                split.scheme,
                split.netloc,
                split.path,
                new_query,
                split.fragment,
            )
        )
    except Exception:
        # Keep error surfaces resilient if URL parsing fails unexpectedly.
        return url


def build_default_document_api_url(repository: str) -> str:
    """Build Content API v2 URL from repository name."""

    normalized = repository.strip()
    if not normalized:
        return ""

    parsed = urlparse(normalized if "://" in normalized else f"//{normalized}")
    host = (parsed.netloc or parsed.path).strip().strip("/")
    if not host:
        return ""

    if host.endswith(".prismic.io"):
        return f"https://{host}/api/v2"

    return f"https://{host}.cdn.prismic.io/api/v2"


@dataclass(slots=True, frozen=True)
class PrismicClientConfig:
    """Runtime configuration for Prismic Content and Migration API clients."""

    repository: str
    write_api_token: str
    migration_api_key: str | None
    content_api_token: str | None
    migration_api_base_url: str
    asset_api_base_url: str
    content_api_base_url: str
    migration_min_interval_seconds: float
    retry_max_attempts: int
    write_type_allowlist: frozenset[str]
    max_batch_size: int
    enforce_trusted_endpoints: bool
    upload_root: str | None
    disable_raw_q: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PrismicClientConfig":
        """Load PRISMIC_* settings from environment variables."""

        source = env if env is not None else os.environ
        repository = _read_env(source, "PRISMIC_REPOSITORY")

        content_api_url = _read_env(source, "PRISMIC_DOCUMENT_API_URL")
        if not content_api_url and repository:
            content_api_url = build_default_document_api_url(repository)

        content_api_token = _read_env(source, "PRISMIC_CONTENT_API_TOKEN") or None
        upload_root = _read_env(source, "PRISMIC_UPLOAD_ROOT") or None

        return cls(
            repository=repository,
            write_api_token=_read_env(source, "PRISMIC_WRITE_API_TOKEN"),
            migration_api_key=_read_env(source, "PRISMIC_MIGRATION_API_KEY") or None,
            content_api_token=content_api_token,
            migration_api_base_url=(
                _read_env(source, "PRISMIC_MIGRATION_API_BASE_URL")
                or DEFAULT_MIGRATION_API_BASE_URL
            ),
            asset_api_base_url=(
                _read_env(source, "PRISMIC_ASSET_API_BASE_URL")
                or DEFAULT_ASSET_API_BASE_URL
            ),
            content_api_base_url=content_api_url,
            migration_min_interval_seconds=_read_float_env(
                source,
                "PRISMIC_MIGRATION_MIN_INTERVAL_SECONDS",
                DEFAULT_MIGRATION_MIN_INTERVAL_SECONDS,
            ),
            retry_max_attempts=_read_int_env(
                source,
                "PRISMIC_RETRY_MAX_ATTEMPTS",
                DEFAULT_RETRY_MAX_ATTEMPTS,
            ),
            write_type_allowlist=_read_csv_set_env(
                source,
                "PRISMIC_WRITE_TYPE_ALLOWLIST",
            ),
            max_batch_size=_read_int_env(
                source,
                "PRISMIC_MAX_BATCH_SIZE",
                DEFAULT_MAX_BATCH_SIZE,
            ),
            enforce_trusted_endpoints=_read_bool_env(
                source,
                "PRISMIC_ENFORCE_TRUSTED_ENDPOINTS",
                DEFAULT_ENFORCE_TRUSTED_ENDPOINTS,
            ),
            upload_root=upload_root,
            disable_raw_q=_read_bool_env(
                source,
                "PRISMIC_DISABLE_RAW_Q",
                DEFAULT_DISABLE_RAW_Q,
            ),
        )


def _warn_and_validate_endpoint_overrides(
    *,
    config: PrismicClientConfig,
    env: Mapping[str, str],
) -> None:
    """Warn (or fail in strict mode) on non-Prismic endpoint overrides."""

    overrides = {
        "PRISMIC_DOCUMENT_API_URL": _read_env(env, "PRISMIC_DOCUMENT_API_URL"),
        "PRISMIC_MIGRATION_API_BASE_URL": _read_env(
            env, "PRISMIC_MIGRATION_API_BASE_URL"
        ),
        "PRISMIC_ASSET_API_BASE_URL": _read_env(env, "PRISMIC_ASSET_API_BASE_URL"),
    }

    untrusted_overrides: list[str] = []
    for env_name, raw_url in overrides.items():
        if not raw_url:
            continue
        if is_trusted_prismic_url(raw_url):
            continue

        host = _extract_url_host(raw_url) or "<unknown-host>"
        logger.warning(
            "%s points to a non-Prismic host (%s). This may expose credentials to untrusted endpoints.",
            env_name,
            host,
        )
        untrusted_overrides.append(env_name)

    if config.enforce_trusted_endpoints and untrusted_overrides:
        names = ", ".join(sorted(untrusted_overrides))
        raise PrismicConfigurationError(
            "PRISMIC_ENFORCE_TRUSTED_ENDPOINTS is enabled and untrusted endpoint "
            f"overrides were detected: {names}"
        )


def validate_required_credentials(config: PrismicClientConfig) -> None:
    """Fail fast when mandatory write credentials are missing."""

    missing: list[str] = []

    if not config.repository:
        missing.append("PRISMIC_REPOSITORY")
    if not config.write_api_token:
        missing.append("PRISMIC_WRITE_API_TOKEN")

    if missing:
        required = ", ".join(missing)
        raise PrismicConfigurationError(
            f"Missing required environment variables: {required}"
        )


def validate_required_asset_credentials(config: PrismicClientConfig) -> None:
    """Fail fast when required Asset API credentials are missing."""

    missing: list[str] = []

    if not config.repository:
        missing.append("PRISMIC_REPOSITORY")
    if not config.write_api_token:
        missing.append("PRISMIC_WRITE_API_TOKEN")

    if missing:
        required = ", ".join(missing)
        raise PrismicConfigurationError(
            f"Missing required environment variables: {required}"
        )


def load_prismic_client_config(
    *,
    env: Mapping[str, str] | None = None,
    validate_credentials: bool = False,
) -> PrismicClientConfig:
    """Load configuration and optionally validate required write credentials."""

    source = env if env is not None else os.environ
    config = PrismicClientConfig.from_env(source)
    _warn_and_validate_endpoint_overrides(config=config, env=source)
    if validate_credentials:
        validate_required_credentials(config)
    return config


def _ensure_non_empty(value: str, env_name: str) -> str:
    """Ensure a required runtime value is present."""

    normalized = value.strip()
    if not normalized:
        raise PrismicConfigurationError(f"{env_name} is required")
    return normalized


def _escape_prismic_predicate_value(value: str) -> str:
    """Escape predicate value content for Prismic query strings."""

    return value.replace("\\", "\\\\").replace("\"", "\\\"")


def _at_predicate(field: str, value: str) -> str:
    """Build a Prismic `at` predicate segment."""

    escaped = _escape_prismic_predicate_value(value)
    return f"[[at({field},\"{escaped}\")]]"


def build_default_asset_origin(repository: str) -> str:
    """Build an Origin header value for Asset API requests."""

    normalized = repository.strip()
    if not normalized:
        return "https://prismic.io"

    parsed = urlparse(normalized if "://" in normalized else f"//{normalized}")
    host = (parsed.netloc or parsed.path).strip().strip("/")
    if not host:
        return "https://prismic.io"

    if host.endswith(".cdn.prismic.io"):
        host = host[: -len(".cdn.prismic.io")] + ".prismic.io"
    elif not host.endswith(".prismic.io"):
        host = f"{host}.prismic.io"

    return f"https://{host}"


class PrismicService:
    """Facade for Prismic read/write operations using async HTTP clients."""

    def __init__(
        self,
        config: PrismicClientConfig,
        *,
        timeout_seconds: float = 30.0,
        content_client: httpx.AsyncClient | None = None,
        migration_client: httpx.AsyncClient | None = None,
        asset_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._timeout_seconds = timeout_seconds
        self._owns_content_client = content_client is None
        self._owns_migration_client = False
        self._owns_asset_client = False
        self._content_ref: str | None = None
        self._content_ref_lock = asyncio.Lock()
        self._migration_limiter = AsyncLimiter(
            1,
            self.config.migration_min_interval_seconds,
        )
        self._retry_max_attempts = self.config.retry_max_attempts
        self._content_client = content_client or self._build_content_client(
            config=config,
            timeout_seconds=timeout_seconds,
        )
        self._migration_client = migration_client
        if self._migration_client is None:
            if self._has_write_credentials(config):
                self._migration_client = self._build_migration_client(
                    config=config,
                    timeout_seconds=timeout_seconds,
                )
                self._owns_migration_client = True
        else:
            self._owns_migration_client = False
        self._asset_client = asset_client
        if self._asset_client is None:
            if self._has_asset_credentials(config):
                self._asset_client = self._build_asset_client(
                    config=config,
                    timeout_seconds=timeout_seconds,
                )
                self._owns_asset_client = True
        else:
            self._owns_asset_client = False

    @staticmethod
    def _has_write_credentials(config: PrismicClientConfig) -> bool:
        """Return True when required write credentials are configured."""

        return bool(config.repository and config.write_api_token)

    @staticmethod
    def _has_asset_credentials(config: PrismicClientConfig) -> bool:
        """Return True when required Asset API credentials are configured."""

        return bool(config.repository and config.write_api_token)

    @staticmethod
    def _build_content_client(
        *, config: PrismicClientConfig, timeout_seconds: float
    ) -> httpx.AsyncClient:
        base_url = _ensure_non_empty(
            config.content_api_base_url.strip()
            or build_default_document_api_url(config.repository),
            "PRISMIC_DOCUMENT_API_URL or PRISMIC_REPOSITORY",
        )
        default_params: dict[str, Any] = {}
        if config.content_api_token:
            default_params["access_token"] = config.content_api_token

        return httpx.AsyncClient(
            base_url=base_url,
            headers={"Accept": "application/json"},
            params=default_params,
            timeout=timeout_seconds,
        )

    @staticmethod
    def _build_migration_client(
        *, config: PrismicClientConfig, timeout_seconds: float
    ) -> httpx.AsyncClient:
        validate_required_credentials(config)
        base_url = (
            config.migration_api_base_url.strip()
            or DEFAULT_MIGRATION_API_BASE_URL
        )

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Repository": config.repository,
            "Authorization": f"Bearer {config.write_api_token}",
        }
        # Legacy compatibility: include API key header only when explicitly provided.
        if config.migration_api_key:
            headers["X-Api-Key"] = config.migration_api_key

        return httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
        )

    @staticmethod
    def _build_asset_client(
        *,
        config: PrismicClientConfig,
        timeout_seconds: float,
    ) -> httpx.AsyncClient:
        validate_required_asset_credentials(config)
        base_url = config.asset_api_base_url.strip() or DEFAULT_ASSET_API_BASE_URL
        origin = build_default_asset_origin(config.repository)
        headers = {
            "Accept": "application/json",
            "Repository": config.repository,
            "Authorization": f"Bearer {config.write_api_token}",
            "Origin": origin,
        }

        return httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
        )

    @property
    def content_client(self) -> httpx.AsyncClient:
        """Configured Content API client."""

        return self._content_client

    @property
    def migration_client(self) -> httpx.AsyncClient:
        """Configured Migration API client."""

        return self._ensure_migration_client()

    @property
    def asset_client(self) -> httpx.AsyncClient:
        """Configured Asset API client."""

        return self._ensure_asset_client()

    async def __aenter__(self) -> "PrismicService":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close owned clients."""

        if self._owns_content_client:
            await self._content_client.aclose()
        if self._owns_migration_client and self._migration_client is not None:
            await self._migration_client.aclose()
        if self._owns_asset_client and self._asset_client is not None:
            await self._asset_client.aclose()

    def _ensure_migration_client(self) -> httpx.AsyncClient:
        """Return migration client, constructing it when write creds are present."""

        if self._migration_client is not None:
            return self._migration_client

        # Bubble configuration errors clearly if write operations are attempted
        # without required credentials.
        validate_required_credentials(self.config)
        self._migration_client = self._build_migration_client(
            config=self.config,
            timeout_seconds=self._timeout_seconds,
        )
        self._owns_migration_client = True
        return self._migration_client

    def _ensure_asset_client(self) -> httpx.AsyncClient:
        """Return asset client, constructing it when required credentials are present."""

        if self._asset_client is not None:
            return self._asset_client

        validate_required_asset_credentials(self.config)
        self._asset_client = self._build_asset_client(
            config=self.config,
            timeout_seconds=self._timeout_seconds,
        )
        self._owns_asset_client = True
        return self._asset_client

    @staticmethod
    def _compose_query_param(
        *,
        document_type: str | None,
        q: str | list[str] | None,
    ) -> list[str] | str | None:
        """Compose the final Content API `q` parameter with optional type filter."""

        type_predicate = (
            _at_predicate("document.type", document_type) if document_type else None
        )

        if q is None:
            return [type_predicate] if type_predicate else None

        if not type_predicate:
            return q

        if isinstance(q, list):
            return [type_predicate, *q]

        return [type_predicate, q]

    def _normalize_q_input(self, q: Any | None) -> str | list[str] | None:
        """Validate and normalize raw Content API `q` input."""

        if q is None:
            return None

        if self.config.disable_raw_q:
            raise PrismicConfigurationError(
                "Raw q predicates are disabled by PRISMIC_DISABLE_RAW_Q"
            )

        if isinstance(q, str):
            normalized = q.strip()
            if not normalized:
                raise ValueError("q must be a non-empty string or list of strings")
            return normalized

        if isinstance(q, list):
            if not q:
                raise ValueError("q list must not be empty")

            normalized_items: list[str] = []
            for item in q:
                if not isinstance(item, str):
                    raise ValueError("q must be a string or list of strings")
                normalized = item.strip()
                if not normalized:
                    raise ValueError("q entries must be non-empty strings")
                normalized_items.append(normalized)
            return normalized_items

        raise ValueError("q must be None, a string, or a list of strings")

    def _resolve_upload_root(self) -> Path:
        """Resolve and validate the configured upload root directory."""

        raw_root = self.config.upload_root
        if not raw_root:
            raise PrismicConfigurationError(
                "PRISMIC_UPLOAD_ROOT is required for media uploads"
            )

        try:
            resolved_root = Path(raw_root).expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise PrismicConfigurationError(
                "PRISMIC_UPLOAD_ROOT must point to an existing directory"
            ) from exc

        if not resolved_root.is_dir():
            raise PrismicConfigurationError(
                "PRISMIC_UPLOAD_ROOT must point to a directory"
            )

        return resolved_root

    def _resolve_upload_path(self, file_path: str) -> Path:
        """Resolve upload file and enforce it stays within upload root."""

        upload_root = self._resolve_upload_root()
        candidate = Path(_ensure_non_empty(file_path, "file_path"))
        if not candidate.is_absolute():
            candidate = upload_root / candidate

        try:
            resolved_path = candidate.expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(
                "file_path does not exist within PRISMIC_UPLOAD_ROOT"
            ) from exc
        except OSError as exc:
            raise ValueError("file_path is invalid") from exc

        try:
            resolved_path.relative_to(upload_root)
        except ValueError as exc:
            raise ValueError(
                "file_path must resolve to a location inside PRISMIC_UPLOAD_ROOT"
            ) from exc

        try:
            mode = resolved_path.stat().st_mode
        except OSError as exc:
            raise ValueError("file_path could not be inspected") from exc

        if not stat.S_ISREG(mode):
            raise ValueError("file_path must reference a regular file")

        return resolved_path

    @staticmethod
    def _encode_routes_param(routes: Any | None) -> str | None:
        """Normalize Content API `routes` param into a request-safe string."""

        if routes is None:
            return None

        if isinstance(routes, str):
            normalized = routes.strip()
            return normalized or None

        try:
            return json.dumps(routes, separators=(",", ":"), ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "routes must be a JSON string or JSON-serializable value"
            ) from exc

    @staticmethod
    async def ensure_success(response: httpx.Response) -> Any:
        """Parse JSON on success or raise API error on non-2xx responses."""

        if response.is_success:
            return response.json()

        raise PrismicApiError.from_response(response)

    @staticmethod
    def _retry_delay_seconds(attempt: int) -> float:
        """Return exponential backoff with jitter for retry attempts."""

        exponential = 0.5 * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, 0.25)
        return exponential + jitter

    async def _request_migration_json(
        self,
        *,
        method: str,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Perform migration request with rate limiting and transient retries."""

        migration_client = self._ensure_migration_client()
        for attempt in range(1, self._retry_max_attempts + 1):
            async with self._migration_limiter:
                response = await migration_client.request(
                    method,
                    endpoint,
                    json=payload,
                )

            if response.is_success:
                return response.json()

            if (
                response.status_code in TRANSIENT_RETRY_STATUS_CODES
                and attempt < self._retry_max_attempts
            ):
                await asyncio.sleep(self._retry_delay_seconds(attempt))
                continue

            raise PrismicApiError.from_response(response)

        raise RuntimeError("unreachable")

    @staticmethod
    def _extract_master_ref(payload: Mapping[str, Any]) -> str:
        """Extract the master content ref from API root payload."""

        refs = payload.get("refs")
        if not isinstance(refs, list):
            raise ValueError("Prismic Content API response missing refs")

        for ref_entry in refs:
            if (
                isinstance(ref_entry, Mapping)
                and ref_entry.get("isMasterRef") is True
                and isinstance(ref_entry.get("ref"), str)
                and ref_entry["ref"].strip()
            ):
                return ref_entry["ref"]

        for ref_entry in refs:
            if (
                isinstance(ref_entry, Mapping)
                and ref_entry.get("id") == "master"
                and isinstance(ref_entry.get("ref"), str)
                and ref_entry["ref"].strip()
            ):
                return ref_entry["ref"]

        raise ValueError("Prismic Content API response missing master ref")

    async def _fetch_content_root_payload(self) -> dict[str, Any]:
        """Fetch and validate the Content API root payload."""

        response = await self._content_client.get("")
        payload = await self.ensure_success(response)
        if not isinstance(payload, Mapping):
            raise ValueError("Prismic Content API root response must be an object")
        return dict(payload)

    async def _resolve_content_ref(self) -> str:
        """Resolve and cache the current master ref for content search requests."""

        if self._content_ref:
            return self._content_ref

        async with self._content_ref_lock:
            if self._content_ref:
                return self._content_ref

            payload = await self._fetch_content_root_payload()
            self._content_ref = self._extract_master_ref(payload)
            return self._content_ref

    async def get_refs(self) -> list[dict[str, Any]]:
        """Fetch repository-level refs from the Content API root endpoint."""

        payload = await self._fetch_content_root_payload()
        refs = payload.get("refs")
        if not isinstance(refs, list):
            raise ValueError("Prismic Content API response missing refs")

        normalized_refs: list[dict[str, Any]] = []
        for ref_entry in refs:
            if not isinstance(ref_entry, Mapping):
                raise ValueError("Prismic Content API response contains invalid ref entry")
            normalized_refs.append(dict(ref_entry))

        if self._content_ref is None:
            try:
                self._content_ref = self._extract_master_ref(payload)
            except ValueError:
                # Keep ref listing functional even if master is absent in this payload.
                pass

        return normalized_refs

    async def get_types(self) -> list[dict[str, str]]:
        """Fetch repository custom types from the Content API root endpoint."""

        payload = await self._fetch_content_root_payload()
        types = payload.get("types")
        if not isinstance(types, Mapping):
            raise ValueError("Prismic Content API response missing types")

        normalized_types: list[dict[str, str]] = []
        for raw_id, raw_label in types.items():
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise ValueError("Prismic Content API response contains invalid type id")
            if not isinstance(raw_label, str):
                raise ValueError("Prismic Content API response contains invalid type label")

            normalized_types.append({"id": raw_id.strip(), "label": raw_label})

        normalized_types.sort(key=lambda item: item["id"])

        if self._content_ref is None:
            try:
                self._content_ref = self._extract_master_ref(payload)
            except ValueError:
                # Keep type listing functional even if master is absent in this payload.
                pass

        return normalized_types

    async def get_releases(self) -> list[dict[str, Any]]:
        """Fetch repository release refs (all refs except the master ref)."""

        refs = await self.get_refs()
        return [dict(ref_entry) for ref_entry in refs if ref_entry.get("isMasterRef") is not True]

    def get_repository_context(self) -> dict[str, Any]:
        """Return non-secret repository context useful for MCP clients/agents."""

        content_api_base_url = str(self._content_client.base_url).rstrip("/")
        migration_api_base_url = (
            self.config.migration_api_base_url.strip() or DEFAULT_MIGRATION_API_BASE_URL
        )
        asset_api_base_url = self.config.asset_api_base_url.strip() or DEFAULT_ASSET_API_BASE_URL

        return {
            "repository": self.config.repository or None,
            "content_api_base_url": content_api_base_url,
            "migration_api_base_url": migration_api_base_url,
            "asset_api_base_url": asset_api_base_url,
            "has_content_api_token": bool(self.config.content_api_token),
            "has_write_credentials": self._has_write_credentials(self.config),
            "has_asset_credentials": self._has_asset_credentials(self.config),
            "endpoint_trust": {
                "content": {
                    "host": _extract_url_host(content_api_base_url) or None,
                    "is_trusted": is_trusted_prismic_url(content_api_base_url),
                },
                "migration": {
                    "host": _extract_url_host(migration_api_base_url) or None,
                    "is_trusted": is_trusted_prismic_url(migration_api_base_url),
                },
                "asset": {
                    "host": _extract_url_host(asset_api_base_url) or None,
                    "is_trusted": is_trusted_prismic_url(asset_api_base_url),
                },
            },
            "upload_root_configured": bool(self.config.upload_root),
            "disable_raw_q": self.config.disable_raw_q,
        }

    def validate_write_document(self, document: DocumentWrite) -> None:
        """Validate write constraints such as type allowlist."""

        allowlist = self.config.write_type_allowlist
        if allowlist and document.type not in allowlist:
            allowed = ", ".join(sorted(allowlist))
            raise PrismicConfigurationError(
                f"Document type '{document.type}' is not allowed. Allowed types: {allowed}"
            )

    def validate_batch_size(self, count: int) -> None:
        """Validate requested batch size against configured maximum."""

        if count > self.config.max_batch_size:
            raise PrismicConfigurationError(
                f"Batch size {count} exceeds max_batch_size={self.config.max_batch_size}"
            )

    def plan_upsert(self, document: DocumentWrite) -> dict[str, Any]:
        """Prepare request metadata and payload for create/update upsert."""

        self.validate_write_document(document)
        payload = self.to_migration_payload(document)
        if document.id:
            return {
                "id": document.id,
                "status": "updated",
                "method": "PUT",
                "endpoint": f"{MIGRATION_DOCUMENTS_ENDPOINT}/{document.id}",
                "payload": payload,
            }

        return {
            "id": "",
            "status": "created",
            "method": "POST",
            "endpoint": MIGRATION_DOCUMENTS_ENDPOINT,
            "payload": payload,
        }

    async def get_documents(
        self,
        *,
        document_type: str | None = None,
        lang: str | None = None,
        ref: str | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        q: Any | None = None,
        orderings: str | None = None,
        routes: Any | None = None,
    ) -> dict[str, Any]:
        """Read Content API documents with pagination metadata."""

        if page < 1:
            raise ValueError("page must be >= 1")

        resolved_ref = ref.strip() if ref and ref.strip() else await self._resolve_content_ref()
        normalized_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        params: dict[str, Any] = {
            "page": page,
            "pageSize": normalized_page_size,
            "ref": resolved_ref,
        }

        if lang:
            params["lang"] = lang

        normalized_q = self._normalize_q_input(q)
        query_param = self._compose_query_param(
            document_type=document_type,
            q=normalized_q,
        )
        if query_param is not None:
            params["q"] = query_param
        if orderings and orderings.strip():
            params["orderings"] = orderings.strip()
        routes_param = self._encode_routes_param(routes)
        if routes_param is not None:
            params["routes"] = routes_param

        response = await self._content_client.get(CONTENT_SEARCH_ENDPOINT, params=params)
        payload = await self.ensure_success(response)

        raw_results = payload.get("results") or []
        documents = self.normalize_documents(raw_results)
        return {
            "results": documents,
            "page": payload.get("page", page),
            "page_size": payload.get("results_per_page", normalized_page_size),
            "total_pages": payload.get("total_pages", 0),
            "total_results": payload.get("total_results_size", 0),
            "next_page": payload.get("next_page"),
        }

    async def get_document_by_id(
        self,
        *,
        document_id: str,
        lang: str | None = None,
        ref: str | None = None,
    ) -> PrismicDocument | None:
        """Fetch a single document by id from Content API."""

        lookup_id = _ensure_non_empty(document_id, "document_id")
        result = await self.get_documents(
            lang=lang,
            ref=ref,
            page=1,
            page_size=1,
            q=[_at_predicate("document.id", lookup_id)],
        )
        documents = result["results"]
        return documents[0] if documents else None

    async def get_document_by_uid(
        self,
        *,
        document_type: str,
        uid: str,
        lang: str | None = None,
        ref: str | None = None,
    ) -> PrismicDocument | None:
        """Fetch a single document by (type, uid, lang) from Content API."""

        lookup_type = _ensure_non_empty(document_type, "document_type")
        lookup_uid = _ensure_non_empty(uid, "uid")
        result = await self.get_documents(
            document_type=lookup_type,
            lang=lang,
            ref=ref,
            page=1,
            page_size=1,
            q=[_at_predicate(f"my.{lookup_type}.uid", lookup_uid)],
        )
        documents = result["results"]
        return documents[0] if documents else None

    async def get_media(
        self,
        *,
        asset_type: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        keyword: str | None = None,
    ) -> Any:
        """List media assets using the Prismic Asset API."""

        asset_client = self._ensure_asset_client()
        params: dict[str, Any] = {}

        if asset_type and asset_type.strip():
            params["assetType"] = asset_type.strip()
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be >= 1")
            params["limit"] = limit
        if cursor and cursor.strip():
            params["cursor"] = cursor.strip()
        if keyword and keyword.strip():
            params["keyword"] = keyword.strip()

        response = await asset_client.get("assets", params=params or None)
        return await self.ensure_success(response)

    async def add_media(
        self,
        *,
        file_path: str,
        notes: str | None = None,
        credits: str | None = None,
        alt: str | None = None,
    ) -> Any:
        """Upload media to Prismic Asset API with optional metadata."""

        asset_client = self._ensure_asset_client()
        resolved_path = self._resolve_upload_path(file_path)

        form_data: dict[str, str] = {}
        if notes is not None and notes.strip():
            form_data["notes"] = notes.strip()
        if credits is not None and credits.strip():
            form_data["credits"] = credits.strip()
        if alt is not None and alt.strip():
            form_data["alt"] = alt.strip()

        with resolved_path.open("rb") as file_handle:
            files = {"file": (resolved_path.name, file_handle)}
            response = await asset_client.post(
                "assets",
                data=form_data or None,
                files=files,
            )
        return await self.ensure_success(response)

    async def create_document(self, document: DocumentWrite) -> dict[str, Any]:
        """Create a document through Migration API POST /documents."""

        self.validate_write_document(document)
        payload = self.to_migration_payload(document)
        return await self._request_migration_json(
            method="POST",
            endpoint=MIGRATION_DOCUMENTS_ENDPOINT,
            payload=payload,
        )

    async def update_document(
        self,
        *,
        document_id: str,
        document: DocumentWrite,
    ) -> dict[str, Any]:
        """Update a document through Migration API PUT /documents/{id}."""

        self.validate_write_document(document)
        lookup_id = _ensure_non_empty(document_id, "document_id")
        payload = self.to_migration_payload(document)
        return await self._request_migration_json(
            method="PUT",
            endpoint=f"{MIGRATION_DOCUMENTS_ENDPOINT}/{lookup_id}",
            payload=payload,
        )

    async def upsert_document(self, document: DocumentWrite) -> dict[str, Any]:
        """Create or update based on document id presence."""

        plan = self.plan_upsert(document)
        raw = await self._request_migration_json(
            method=plan["method"],
            endpoint=plan["endpoint"],
            payload=plan["payload"],
        )
        resolved_id = plan["id"] or str(raw.get("id", ""))
        return {"id": resolved_id, "status": plan["status"], "raw": raw}

    @staticmethod
    def normalize_document(payload: Mapping[str, Any]) -> PrismicDocument:
        """Normalize a Content API document payload into a typed model."""

        return PrismicDocument.model_validate(payload)

    @staticmethod
    def normalize_documents(payloads: list[Mapping[str, Any]]) -> list[PrismicDocument]:
        """Normalize a list of Content API documents into typed models."""

        return [PrismicService.normalize_document(payload) for payload in payloads]

    @staticmethod
    def to_migration_payload(document: DocumentWrite) -> dict[str, Any]:
        """Serialize DocumentWrite with a strict allowlist for Migration API writes."""

        raw_payload = document.model_dump(mode="python", exclude_none=True)
        return {
            key: raw_payload[key]
            for key in MIGRATION_WRITE_FIELD_ALLOWLIST
            if key in raw_payload
        }
