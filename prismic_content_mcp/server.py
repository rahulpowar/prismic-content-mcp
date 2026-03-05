"""FastMCP server construction and runtime orchestration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Literal

import httpx
from mcp.server.fastmcp import FastMCP

from .models import DocumentWrite
from .prismic import (
    PrismicApiError,
    PrismicConfigurationError,
    PrismicService,
    load_prismic_client_config,
    sanitize_url_query_parameters,
)


TransportMode = Literal["stdio", "streamable-http"]
RECOVERABLE_BATCH_EXCEPTIONS = (
    PrismicApiError,
    PrismicConfigurationError,
    ValueError,
    httpx.HTTPError,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeConfig:
    """Runtime options for running the MCP server."""

    transport: TransportMode = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"


def _build_service(*, require_write_credentials: bool = False) -> PrismicService:
    """Create a Prismic service instance from environment configuration."""

    config = load_prismic_client_config(validate_credentials=require_write_credentials)
    return PrismicService(config)


def _document_reference(document: DocumentWrite, index: int) -> str:
    """Return a stable-ish input reference for batch results."""

    if document.id:
        return document.id
    if document.uid:
        return f"{document.type}:{document.uid}"
    return f"index:{index}"


def _is_public_bind_host(host: str) -> bool:
    """Return True for wildcard hosts that expose network listeners."""

    normalized = host.strip().lower()
    return normalized in {"0.0.0.0", "::", "[::]"}


def _warn_streamable_http_exposure(config: RuntimeConfig) -> None:
    """Emit explicit security guidance when running HTTP transport."""

    if config.transport != "streamable-http":
        return

    if _is_public_bind_host(config.host):
        logger.warning(
            "PRISMIC_MCP_TRANSPORT=streamable-http is running on host %s. "
            "This exposes MCP tools over the network without built-in auth. "
            "Use localhost, network isolation, and/or authenticated reverse proxy.",
            config.host,
        )
        return

    logger.warning(
        "PRISMIC_MCP_TRANSPORT=streamable-http has no built-in authentication. "
        "Keep host bound to localhost or place behind authenticated network boundaries."
    )


def _safe_batch_error(exc: Exception) -> dict[str, Any]:
    """Convert known per-item exceptions into non-sensitive error payloads."""

    if isinstance(exc, PrismicApiError):
        return {
            "type": "PrismicApiError",
            "message": "Prismic API request failed",
            "status_code": exc.status_code,
            "url": exc.url,
        }

    if isinstance(exc, PrismicConfigurationError):
        return {
            "type": "PrismicConfigurationError",
            "message": str(exc),
        }

    if isinstance(exc, ValueError):
        return {
            "type": "ValueError",
            "message": "Input validation failed",
        }

    if isinstance(exc, httpx.HTTPError):
        request_url = (
            sanitize_url_query_parameters(str(exc.request.url))
            if exc.request is not None
            else None
        )
        payload: dict[str, Any] = {
            "type": exc.__class__.__name__,
            "message": "HTTP transport error while calling upstream API",
        }
        if request_url:
            payload["url"] = request_url
        return payload

    return {
        "type": exc.__class__.__name__,
        "message": "Unexpected error",
    }


def load_runtime_config() -> RuntimeConfig:
    """Load server runtime config from environment variables."""

    transport_raw = os.getenv("PRISMIC_MCP_TRANSPORT", "").strip().lower() or "stdio"
    if transport_raw in {"http", "streamable-http"}:
        transport: TransportMode = "streamable-http"
    elif transport_raw == "stdio":
        transport = "stdio"
    else:
        raise ValueError(
            "PRISMIC_MCP_TRANSPORT must be one of: stdio, http, streamable-http"
        )

    port_raw = os.getenv("PRISMIC_MCP_PORT", "8000").strip()
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError("PRISMIC_MCP_PORT must be an integer") from exc

    return RuntimeConfig(
        transport=transport,
        host=os.getenv("PRISMIC_MCP_HOST", "127.0.0.1").strip(),
        port=port,
        path=os.getenv("PRISMIC_MCP_PATH", "/mcp").strip() or "/mcp",
    )


ServiceFactory = Callable[..., PrismicService]


async def handle_prismic_get_repository_context(
    *,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Return active Prismic repository/context metadata for this MCP runtime."""

    async with service_factory() as service:
        context = service.get_repository_context()

    return {"context": context}


async def handle_prismic_get_refs(
    *,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Return repository-level refs from Prismic Content API root (`/api/v2`)."""

    async with service_factory() as service:
        refs = await service.get_refs()

    return {"refs": refs}


async def handle_prismic_get_types(
    *,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Return repository custom types from Prismic Content API root (`/api/v2`)."""

    async with service_factory() as service:
        types = await service.get_types()

    return {"types": types}


async def handle_prismic_get_releases(
    *,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Return release refs (non-master refs) from Prismic Content API root."""

    async with service_factory() as service:
        releases = await service.get_releases()

    return {"releases": releases}


async def handle_prismic_get_custom_types(
    *,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Return full Custom Type models from Prismic Custom Types API."""

    async with service_factory(require_write_credentials=True) as service:
        custom_types = await service.get_all_custom_type_models()

    return {"custom_types": custom_types}


async def handle_prismic_get_custom_type(
    *,
    custom_type_id: str,
    include_schema_summary: bool = True,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Return one Custom Type model with optional normalized schema summary."""

    async with service_factory(require_write_credentials=True) as service:
        custom_type = await service.get_custom_type_model(custom_type_id=custom_type_id)
        schema = (
            service.summarize_custom_type_schema(custom_type)
            if include_schema_summary
            else None
        )

    response: dict[str, Any] = {"custom_type": custom_type}
    if schema is not None:
        response["schema"] = schema
    return response


async def handle_prismic_insert_custom_type(
    *,
    custom_type: dict[str, Any],
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Insert a Custom Type model via Prismic Custom Types API."""

    async with service_factory(require_write_credentials=True) as service:
        result = await service.insert_custom_type_model(custom_type=custom_type)

    return result


async def handle_prismic_update_custom_type(
    *,
    custom_type: dict[str, Any],
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Update a Custom Type model via Prismic Custom Types API."""

    async with service_factory(require_write_credentials=True) as service:
        result = await service.update_custom_type_model(custom_type=custom_type)

    return result


async def handle_prismic_get_shared_slices(
    *,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Return all Shared Slice models from Prismic Custom Types API."""

    async with service_factory(require_write_credentials=True) as service:
        shared_slices = await service.get_all_shared_slice_models()

    return {"shared_slices": shared_slices}


async def handle_prismic_get_shared_slice(
    *,
    slice_id: str,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Return one Shared Slice model by ID from Prismic Custom Types API."""

    async with service_factory(require_write_credentials=True) as service:
        shared_slice = await service.get_shared_slice_model(slice_id=slice_id)

    return {"shared_slice": shared_slice}


async def handle_prismic_insert_shared_slice(
    *,
    shared_slice: dict[str, Any],
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Insert a Shared Slice model via Prismic Custom Types API."""

    async with service_factory(require_write_credentials=True) as service:
        result = await service.insert_shared_slice_model(shared_slice=shared_slice)

    return result


async def handle_prismic_update_shared_slice(
    *,
    shared_slice: dict[str, Any],
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Update a Shared Slice model via Prismic Custom Types API."""

    async with service_factory(require_write_credentials=True) as service:
        result = await service.update_shared_slice_model(shared_slice=shared_slice)

    return result


async def handle_prismic_get_documents(
    *,
    type: str | None = None,
    lang: str | None = None,
    ref: str | None = None,
    page: int = 1,
    page_size: int = 20,
    q: Any | None = None,
    orderings: str | None = None,
    routes: Any | None = None,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Read documents from the Prismic Content API.

    Query behavior:
    - `ref` overrides the default master ref resolution (useful for previews/drafts).
    - `q` is passed directly to the Content API `q` parameter.
      Treat `q` as trusted input only (do not forward untrusted prompt text).
    - If `PRISMIC_DISABLE_RAW_Q=1`, raw `q` is rejected and only server-built
      predicates (for example via `type`) are allowed.
    - `orderings` is passed directly to the Content API `orderings` parameter.
    - `routes` is passed to the Content API `routes` parameter (route resolvers).
    - `type` is a convenience mapping to `[[at(document.type,"<type>")]]`.
    - If both are provided, the type predicate is prepended to `q`.

    Effective merge behavior:
    - only `type`: q => [type_predicate]
    - only `q`: q => q (unchanged)
    - `type` + list q: q => [type_predicate, *q]
    - `type` + scalar q: q => [type_predicate, q]
    """

    async with service_factory() as service:
        result = await service.get_documents(
            document_type=type,
            lang=lang,
            ref=ref,
            page=page,
            page_size=page_size,
            q=q,
            orderings=orderings,
            routes=routes,
        )

    return {
        **result,
        "results": [doc.model_dump(mode="python") for doc in result["results"]],
    }


async def handle_prismic_get_document(
    *,
    id: str | None = None,
    type: str | None = None,
    uid: str | None = None,
    lang: str | None = None,
    ref: str | None = None,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Fetch a single document by id or by (type, uid, lang) with optional ref."""

    async with service_factory() as service:
        if id:
            document = await service.get_document_by_id(document_id=id, lang=lang, ref=ref)
        elif type and uid:
            document = await service.get_document_by_uid(
                document_type=type,
                uid=uid,
                lang=lang,
                ref=ref,
            )
        else:
            raise ValueError("Provide id OR (type and uid)")

    return {
        "document": document.model_dump(mode="python") if document else None,
    }


async def handle_prismic_get_media(
    *,
    asset_type: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    keyword: str | None = None,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """List assets from the Prismic Asset API (`GET /assets`)."""

    async with service_factory() as service:
        media = await service.get_media(
            asset_type=asset_type,
            limit=limit,
            cursor=cursor,
            keyword=keyword,
        )

    return {"media": media}


async def handle_prismic_add_media(
    *,
    file_path: str,
    notes: str | None = None,
    credits: str | None = None,
    alt: str | None = None,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Upload media to Prismic Asset API (`POST /assets`)."""

    async with service_factory() as service:
        media = await service.add_media(
            file_path=file_path,
            notes=notes,
            credits=credits,
            alt=alt,
        )

    return {"media": media}


async def handle_prismic_upsert_document(
    *,
    document: DocumentWrite,
    dry_run: bool = False,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Create or update a single document in the Migration API."""

    async with service_factory(require_write_credentials=True) as service:
        if dry_run:
            plan = service.plan_upsert(document)
            return {
                "id": plan["id"],
                "status": plan["status"],
                "dry_run": True,
                "would_call": {
                    "method": plan["method"],
                    "endpoint": plan["endpoint"],
                },
            }

        return await service.upsert_document(document)


async def handle_prismic_upsert_documents(
    *,
    documents: list[DocumentWrite],
    fail_fast: bool = False,
    dry_run: bool = False,
    service_factory: ServiceFactory = _build_service,
) -> dict[str, Any]:
    """Batch create/update documents in the Migration API."""

    created = 0
    updated = 0
    failed = 0
    results: list[dict[str, Any]] = []

    async with service_factory(require_write_credentials=True) as service:
        service.validate_batch_size(len(documents))
        for index, document in enumerate(documents):
            input_ref = _document_reference(document, index)
            try:
                if dry_run:
                    plan = service.plan_upsert(document)
                    status = plan["status"]
                    resolved_id = plan["id"]
                else:
                    response = await service.upsert_document(document)
                    status = response["status"]
                    resolved_id = response["id"]

                if status == "created":
                    created += 1
                else:
                    updated += 1
                results.append(
                    {
                        "input_ref": input_ref,
                        "ok": True,
                        "id": resolved_id,
                        "error": None,
                        "dry_run": dry_run,
                    }
                )
            except RECOVERABLE_BATCH_EXCEPTIONS as exc:
                failed += 1
                results.append(
                    {
                        "input_ref": input_ref,
                        "ok": False,
                        "id": None,
                        "error": _safe_batch_error(exc),
                        "dry_run": dry_run,
                    }
                )
                if fail_fast:
                    raise

    return {
        "results": results,
        "summary": {
            "created": created,
            "updated": updated,
            "failed": failed,
        },
    }


def create_server(*, name: str = "prismic-content-mcp") -> FastMCP:
    """Create the FastMCP server instance and register Prismic tools."""

    server = FastMCP(name)

    @server.tool(name="prismic_get_repository_context")
    async def prismic_get_repository_context() -> dict[str, Any]:
        """Get active repository context for this MCP server.

        Returns repository and API base URL metadata (no secrets) so agents can
        identify which Prismic repository they are operating on.
        Recommended first call in a session to confirm repository and auth
        posture before running read/write workflows.
        """

        return await handle_prismic_get_repository_context()

    @server.tool(name="prismic_get_refs")
    async def prismic_get_refs() -> dict[str, Any]:
        """Get repository refs from Content API root.

        Refs are repository-level version pointers (for example `master`,
        preview, or release refs), not per-document refs.
        Use returned `ref` values with `prismic_get_documents` or
        `prismic_get_document` to read content for that version pointer.
        Efficiency tip: call once, cache the chosen `ref`, and pass it explicitly
        to subsequent read calls to avoid per-call implicit master-ref resolution.
        """

        return await handle_prismic_get_refs()

    @server.tool(name="prismic_get_types")
    async def prismic_get_types() -> dict[str, Any]:
        """Get repository custom types from Content API root.

        Returns content type metadata from the Content API `types` map as
        normalized entries with `id` and `label`.
        Typical sequencing: call once, then iterate type ids with
        `prismic_get_documents(type=..., page_size=1)` when you only need counts
        or existence checks.
        """

        return await handle_prismic_get_types()

    @server.tool(name="prismic_get_releases")
    async def prismic_get_releases() -> dict[str, Any]:
        """Get release refs from Content API root.

        Returns non-master refs only, equivalent to filtering repository refs by
        `isMasterRef != true`.
        Use these refs with read tools (`ref` parameter) to inspect release
        content through Content API.
        Note: querying documents with a release `ref` returns a content snapshot
        at that ref, not only the release "planned items" shown in Prismic UI.
        Efficiency tip: pick the release `ref` once and reuse it across all read
        queries in the same analysis.
        """

        return await handle_prismic_get_releases()

    @server.tool(name="prismic_get_custom_types")
    async def prismic_get_custom_types() -> dict[str, Any]:
        """List full Custom Type models from Prismic Custom Types API.

        Uses `GET /customtypes` on `https://customtypes.prismic.io` (override via
        `PRISMIC_CUSTOM_TYPES_API_BASE_URL`).
        Requires:
        - `PRISMIC_REPOSITORY`
        - `PRISMIC_WRITE_API_TOKEN`
        """

        return await handle_prismic_get_custom_types()

    @server.tool(name="prismic_get_custom_type")
    async def prismic_get_custom_type(
        custom_type_id: str,
        include_schema_summary: bool = True,
    ) -> dict[str, Any]:
        """Get one Custom Type model and schema summary by ID.

        Uses `GET /customtypes/{id}`.
        When `include_schema_summary=true`, returns:
        - tab/field breakdown
        - field `config` for each field
        - `required` flags where present in field config
        - shared slice choices configured inside Slices fields

        This is the recommended verification call after create/update operations.
        """

        return await handle_prismic_get_custom_type(
            custom_type_id=custom_type_id,
            include_schema_summary=include_schema_summary,
        )

    @server.tool(name="prismic_insert_custom_type")
    async def prismic_insert_custom_type(custom_type: dict[str, Any]) -> dict[str, Any]:
        """Insert a new Custom Type model.

        Uses `POST /customtypes/insert`.
        Pass a full Custom Type JSON model (including `id`, `label`, `repeatable`,
        and `json` tabs/fields).
        """

        return await handle_prismic_insert_custom_type(custom_type=custom_type)

    @server.tool(name="prismic_update_custom_type")
    async def prismic_update_custom_type(custom_type: dict[str, Any]) -> dict[str, Any]:
        """Update an existing Custom Type model.

        Uses `POST /customtypes/update`.
        Pass the full updated Custom Type JSON model.
        Typical sequence:
        1) `prismic_get_custom_type`
        2) edit model JSON
        3) `prismic_update_custom_type`
        4) `prismic_get_custom_type` to verify schema
        """

        return await handle_prismic_update_custom_type(custom_type=custom_type)

    @server.tool(name="prismic_get_shared_slices")
    async def prismic_get_shared_slices() -> dict[str, Any]:
        """List all Shared Slice models from Prismic Custom Types API.

        Uses `GET /slices`.
        """

        return await handle_prismic_get_shared_slices()

    @server.tool(name="prismic_get_shared_slice")
    async def prismic_get_shared_slice(slice_id: str) -> dict[str, Any]:
        """Get one Shared Slice model by ID.

        Uses `GET /slices/{id}`.
        """

        return await handle_prismic_get_shared_slice(slice_id=slice_id)

    @server.tool(name="prismic_insert_shared_slice")
    async def prismic_insert_shared_slice(shared_slice: dict[str, Any]) -> dict[str, Any]:
        """Insert a new Shared Slice model.

        Uses `POST /slices/insert`.
        Pass a full Shared Slice JSON model.
        """

        return await handle_prismic_insert_shared_slice(shared_slice=shared_slice)

    @server.tool(name="prismic_update_shared_slice")
    async def prismic_update_shared_slice(shared_slice: dict[str, Any]) -> dict[str, Any]:
        """Update an existing Shared Slice model.

        Uses `POST /slices/update`.
        Pass the full updated Shared Slice JSON model.
        """

        return await handle_prismic_update_shared_slice(shared_slice=shared_slice)

    @server.tool(name="prismic_get_documents")
    async def prismic_get_documents(
        type: str | None = None,
        lang: str | None = None,
        ref: str | None = None,
        page: int = 1,
        page_size: int = 20,
        q: Any | None = None,
        orderings: str | None = None,
        routes: Any | None = None,
    ) -> dict[str, Any]:
        """List documents with optional Prismic predicate filtering.

        Use `ref` to read from an explicit Prismic content ref (for example
        preview/draft refs). When omitted, master ref is used.
        Depending on repository API visibility settings, reading non-master refs
        may require `PRISMIC_CONTENT_API_TOKEN`.
        Use `q` for explicit Content API predicates (for example
        `[[at(document.tags,"news")]]`). `type` is a convenience shortcut
        for `[[at(document.type,"<type>")]]` and is merged into `q`.
        Use `orderings` for native Content API sort clauses (for example
        `[document.first_publication_date desc]`).
        Use `routes` for Content API route resolvers to populate the `url` field
        (for example `[{"type":"page","path":"/:uid"}]`).
        Note: there is no documented Content API `q` predicate for "published
        status". A release `ref` query returns a version snapshot, not only
        release-delta documents.
        Efficiency tips:
        - For large scans: call `prismic_get_refs` once and pass `ref` explicitly.
        - For counts/existence checks: set `page_size=1` and read `total_results`.
        - Only pass `routes` when you need populated `url` fields.
        - Paginate with `page` + `next_page` for full exports.
        Codex js_repl tip: `codex.tool(...)` wraps tool output; read payload from
        `result.Ok.structuredContent`.
        """
        return await handle_prismic_get_documents(
            type=type,
            lang=lang,
            ref=ref,
            page=page,
            page_size=page_size,
            q=q,
            orderings=orderings,
            routes=routes,
        )

    @server.tool(name="prismic_get_document")
    async def prismic_get_document(
        id: str | None = None,
        type: str | None = None,
        uid: str | None = None,
        lang: str | None = None,
        ref: str | None = None,
    ) -> dict[str, Any]:
        """Get one document by id or by type+uid with optional explicit ref.

        Use `ref` to read a specific preview/release version pointer. Depending
        on repository API visibility settings, non-master refs may require
        `PRISMIC_CONTENT_API_TOKEN`.
        The document payload has no explicit `status` field (for example
        published/draft/in-release). To determine publish state on master, use
        this sequence:
        1) Call `prismic_get_refs` and capture the `master` ref.
        2) Call `prismic_get_document(..., ref=<master_ref>)`.
        3) Call `prismic_get_document(..., ref=<release_ref>)` as needed.
        4) Interpret results:
           - exists on master: published on master
           - missing on master but exists on release ref: not published on master
             (release-only content)
        Prefer this over `prismic_get_documents` when you already have an exact
        id or type+uid target.
        """

        return await handle_prismic_get_document(
            id=id,
            type=type,
            uid=uid,
            lang=lang,
            ref=ref,
        )

    @server.tool(name="prismic_get_media")
    async def prismic_get_media(
        asset_type: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """List media assets from Prismic Asset API.

        This maps directly to `GET /assets` query parameters:
        `assetType`, `limit`, `cursor`, and `keyword`.
        Requires `PRISMIC_REPOSITORY` and `PRISMIC_WRITE_API_TOKEN`.
        """

        return await handle_prismic_get_media(
            asset_type=asset_type,
            limit=limit,
            cursor=cursor,
            keyword=keyword,
        )

    @server.tool(name="prismic_add_media")
    async def prismic_add_media(
        file_path: str,
        notes: str | None = None,
        credits: str | None = None,
        alt: str | None = None,
    ) -> dict[str, Any]:
        """Upload media via Prismic Asset API.

        Uploads `file_path` using `multipart/form-data` to `POST /assets`.
        Optional metadata maps to Asset API fields: `notes`, `credits`, `alt`.
        Requires `PRISMIC_REPOSITORY` and `PRISMIC_WRITE_API_TOKEN`.
        Security: `PRISMIC_UPLOAD_ROOT` must be set; upload paths must resolve
        within that directory (traversal and symlink escapes are blocked).
        """

        return await handle_prismic_add_media(
            file_path=file_path,
            notes=notes,
            credits=credits,
            alt=alt,
        )

    @server.tool(name="prismic_upsert_document")
    async def prismic_upsert_document(
        document: DocumentWrite,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create/update one document in the Prismic Migration API.

        Important behavior:
        - This writes to the Migration workflow (Migration UI/release flow).
        - New/updated documents may not be visible via Content API master reads
          immediately (or at all) until they are included in the readable release
          flow/published in Prismic.
        - To read back migrated content before publish, fetch a release ref via
          `prismic_get_releases`/`prismic_get_refs`, then query read tools with
          that `ref` (and provide `PRISMIC_CONTENT_API_TOKEN` when required).
        - Use `dry_run=true` to validate payload/endpoint choice without writing.
        """

        return await handle_prismic_upsert_document(
            document=document,
            dry_run=dry_run,
        )

    @server.tool(name="prismic_upsert_documents")
    async def prismic_upsert_documents(
        documents: list[DocumentWrite],
        fail_fast: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Batch create/update documents in the Prismic Migration API.

        Important behavior:
        - This writes to the Migration workflow (Migration UI/release flow).
        - Batch-created/updated documents may not be visible via Content API
          master reads until release/publish workflow makes them readable.
        - For read-back before publish, use a release ref with read tools
          (`ref` parameter), plus `PRISMIC_CONTENT_API_TOKEN` if repo settings
          require authenticated reads.
        - Supports `dry_run` and `fail_fast` for safer execution.
        """

        return await handle_prismic_upsert_documents(
            documents=documents,
            fail_fast=fail_fast,
            dry_run=dry_run,
        )

    return server


def run_server(config: RuntimeConfig | None = None) -> None:
    """Run the server in stdio or streamable-http mode."""

    effective_config = config or load_runtime_config()
    _warn_streamable_http_exposure(effective_config)
    server = create_server()

    if effective_config.transport == "stdio":
        server.run()
        return

    # Match the mount path expected by inspector/editor clients.
    server.settings.streamable_http_path = effective_config.path
    server.run(
        transport="streamable-http",
        host=effective_config.host,
        port=effective_config.port,
        stateless_http=True,
        json_response=True,
    )
