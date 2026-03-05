# prismic-content-mcp

MCP server for Prismic:

- Read documents from the Prismic Content API
- List/upload media from the Prismic Asset API
- Create/update documents through the Prismic Migration API

This server writes into the Prismic Migration Release for review and publishing. It does not auto-publish.

GitHub: `https://github.com/rahulpowar/prismic-content-mcp`

## Example Prompts

Use these directly in your LLM client once this MCP is connected:

- `Show the Prismic repository context and tell me which repo this MCP is configured for.`
- `List all content types (custom types) with their IDs and labels.`
- `List repository refs and identify the master ref.`
- `List release refs and summarize each release name + ref.`
- `List the top 10 longest articles, excluding a specific type like page guides.`
- `Fetch documents using a flexible q predicate for tagged content.`
- `Filter documents by type and sort by first publication date descending.`
- `Read a document by type + uid and show its resolved URL.`
- `Read documents using an explicit ref so I can inspect draft or release content.`
- `List all media assets and include pagination cursors if present.`
- `Upload a media asset with alt text, credits, and notes.`
- `List all resource-center pages and group them by language.`
- `Show which pages are translated and which are missing locales.`
- `Show SEO metadata for a given article (title, description, OG/Twitter fields).`
- `Audit SEO fields for likely copy/paste mismatches across title/description/image.`

## What You Get

- Read tools for listing and fetching documents
- Read tools for refs, releases, and custom types
- Media tools for listing and uploading assets
- Write tools for single and batch upsert
- Safer write behavior with:
  - Rate limiting
  - Retry-on-transient errors (`429`, `503`, `504`)
  - Optional type allowlist
  - Batch size limit
- Structured upstream errors with status and response details
- Logging to stderr with secret redaction

## Requirements

- Python `3.10+`
- A Prismic repository
- For write operations only: Migration API credentials

## Install

Run without cloning (recommended):

```bash
uvx --from git+https://github.com/rahulpowar/prismic-content-mcp.git prismic-content-mcp
```

For stdio MCP client configs, use:

- `command`: `uvx`
- `args`: `["--from", "git+https://github.com/rahulpowar/prismic-content-mcp.git", "prismic-content-mcp"]`

Clone only for local development:

```bash
git clone https://github.com/rahulpowar/prismic-content-mcp.git
cd prismic-content-mcp
uv sync --frozen --extra dev
```

`uv sync --frozen` is the canonical deterministic install path for this repo
and will fail if `uv.lock` is out of date with `pyproject.toml`.

Run from a local checkout:

```bash
uv run prismic-content-mcp
```

For development/test:

```bash
uv run pytest -q
```

## Quickstart (LLM Clients)

This MCP supports both transports:

- `stdio`: best for local clients (`Claude Desktop`, `Codex`, `Claude Code`)
- `streamable-http`: required for remote/web clients (`ChatGPT`, `Claude` connectors)

Security note:

- `streamable-http` has no built-in authentication. Prefer `stdio` for local use.
- If you must use HTTP transport, bind `PRISMIC_MCP_HOST=127.0.0.1` or place the
  server behind authenticated network boundaries (reverse proxy / private network).

Use these env vars in all examples:

```bash
# Required for read tools
export PRISMIC_REPOSITORY=your-repo

# Optional for private content API access
export PRISMIC_CONTENT_API_TOKEN=your-content-token

# Required for media upload path safety (must be an existing directory)
export PRISMIC_UPLOAD_ROOT=/absolute/path/allowed-for-upload-files

# Required for media tools and migration write tools
export PRISMIC_WRITE_API_TOKEN=your-write-token

# Required only for migration write tools
export PRISMIC_MIGRATION_API_KEY=your-migration-api-key
```

### Claude Desktop (local stdio)

Edit `claude_desktop_config.json` and add:

```json
{
  "mcpServers": {
    "prismic": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/rahulpowar/prismic-content-mcp.git",
        "prismic-content-mcp"
      ],
      "env": {
        "PRISMIC_REPOSITORY": "your-repo",
        "PRISMIC_CONTENT_API_TOKEN": "your-content-token",
        "PRISMIC_WRITE_API_TOKEN": "your-write-token",
        "PRISMIC_MIGRATION_API_KEY": "your-migration-api-key"
      }
    }
  }
}
```

Claude Desktop config file location:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\\Claude\\claude_desktop_config.json`

### Claude (claude.ai connectors)

Claude connectors use remote MCP endpoints (HTTP/SSE), not local stdio.

1. Start this server in streamable HTTP mode.
2. Expose it on a reachable HTTPS URL.
3. Add it in `claude.ai/settings/connectors`.

Run locally in HTTP mode:

```bash
export PRISMIC_MCP_TRANSPORT=streamable-http
export PRISMIC_MCP_HOST=127.0.0.1
export PRISMIC_MCP_PORT=8000
export PRISMIC_MCP_PATH=/mcp
prismic-content-mcp
```

### ChatGPT (Developer mode)

ChatGPT app connectors require remote MCP endpoints (SSE or streaming HTTP).

1. Enable Developer mode in `Settings -> Apps -> Advanced settings -> Developer mode`.
2. In Apps settings, click `Create app`.
3. Provide your remote MCP URL (for this server, streamable HTTP).
4. Enable tools and use the app in chats.

Use the same HTTP run command shown above, but exposed on an HTTPS URL reachable by ChatGPT.

### Codex

Add as a local stdio MCP server:

```bash
codex mcp add \
  --env PRISMIC_REPOSITORY=your-repo \
  --env PRISMIC_CONTENT_API_TOKEN=your-content-token \
  --env PRISMIC_WRITE_API_TOKEN=your-write-token \
  --env PRISMIC_MIGRATION_API_KEY=your-migration-api-key \
  prismic -- uvx --from git+https://github.com/rahulpowar/prismic-content-mcp.git prismic-content-mcp
```

Or add as remote HTTP MCP server:

```bash
codex mcp add prismic --url https://your-public-host/mcp
```

Verify:

```bash
codex mcp list
```

Optional agent-routing note for Codex:

If you use `~/.codex/AGENTS.md`, add an instruction like:

```text
If querying or updating content for the xyz.com website, use the Prismic MCP tools when enabled.
```

### Claude Code

Add as local stdio MCP server:

```bash
claude mcp add \
  --transport stdio \
  --env PRISMIC_REPOSITORY=your-repo \
  --env PRISMIC_CONTENT_API_TOKEN=your-content-token \
  --env PRISMIC_WRITE_API_TOKEN=your-write-token \
  --env PRISMIC_MIGRATION_API_KEY=your-migration-api-key \
  prismic -- uvx --from git+https://github.com/rahulpowar/prismic-content-mcp.git prismic-content-mcp
```

Verify:

```bash
claude mcp get prismic
```

Client docs:

- Claude Code MCP: `https://docs.anthropic.com/en/docs/claude-code/mcp`
- Claude Desktop MCP config shape/path: `https://modelcontextprotocol.io/quickstart/user`
- ChatGPT Developer mode: `https://help.openai.com/en/articles/12319417-developer-mode`
- OpenAI Apps SDK MCP server guide: `https://developers.openai.com/apps-sdk/build/mcp-server/`
- Codex CLI MCP: `https://developers.openai.com/codex/cli/mcp`

## Configuration

### Core Variables

| Variable | Default | Required | Notes |
|---|---|---|---|
| `PRISMIC_REPOSITORY` | _none_ | Read: usually yes, Write: yes | Repository name (recommended). Used to derive Content API URL if not provided. |
| `PRISMIC_DOCUMENT_API_URL` | Derived from repository | No | Optional override for Content API base URL. |
| `PRISMIC_CONTENT_API_TOKEN` | _none_ | No | Needed for private repos and often required to read non-master refs (preview/release) when API visibility is restricted. |
| `PRISMIC_DISABLE_RAW_Q` | `false` | No | When true (`1/true`), rejects raw `q` predicates; only server-generated predicates (for example `type`) are allowed. |
| `PRISMIC_WRITE_API_TOKEN` | _none_ | Media/Write | Required for media tools and Migration API write tools. |
| `PRISMIC_MIGRATION_API_KEY` | _none_ | Write only | Required only for write tools. |
| `PRISMIC_MIGRATION_API_BASE_URL` | `https://migration.prismic.io` | No | Optional Migration API override. |
| `PRISMIC_ASSET_API_BASE_URL` | `https://asset-api.prismic.io` | No | Optional Asset API override. |
| `PRISMIC_UPLOAD_ROOT` | _none_ | Media upload | Required for `prismic_add_media`; upload file paths must resolve within this directory. |
| `PRISMIC_ENFORCE_TRUSTED_ENDPOINTS` | `false` | No | When true (`1/true`), startup fails if endpoint override env vars point to non-`*.prismic.io` hosts. |

### Write Safety Controls

| Variable | Default | Required | Notes |
|---|---|---|---|
| `PRISMIC_MIGRATION_MIN_INTERVAL_SECONDS` | `2.5` | No | Minimum spacing between write requests. |
| `PRISMIC_RETRY_MAX_ATTEMPTS` | `5` | No | Max attempts for transient write failures. |
| `PRISMIC_WRITE_TYPE_ALLOWLIST` | _empty_ | No | Comma-separated list of allowed custom types for writes. |
| `PRISMIC_MAX_BATCH_SIZE` | `50` | No | Maximum documents allowed in `prismic_upsert_documents`. |

### Runtime + Logging

| Variable | Default | Required | Notes |
|---|---|---|---|
| `PRISMIC_MCP_TRANSPORT` | `stdio` | No | `stdio`, `http`, or `streamable-http` (`http` maps to streamable HTTP mode). |
| `PRISMIC_MCP_HOST` | `127.0.0.1` | No | HTTP/streamable-http bind host. |
| `PRISMIC_MCP_PORT` | `8000` | No | HTTP/streamable-http bind port. |
| `PRISMIC_MCP_PATH` | `/mcp` | No | Streamable HTTP path. |
| `PRISMIC_LOG_LEVEL` | `INFO` | No | Standard Python logging level. |

### Content API URL Derivation

If `PRISMIC_DOCUMENT_API_URL` is not set, the server derives it from `PRISMIC_REPOSITORY`.

Recommended:

- `PRISMIC_REPOSITORY=your-repo`

Result:

- `https://your-repo.cdn.prismic.io/api/v2`

If you prefer, you may explicitly set `PRISMIC_DOCUMENT_API_URL` and skip derivation.

Security behavior for endpoint overrides:

- Non-Prismic overrides for `PRISMIC_DOCUMENT_API_URL`,
  `PRISMIC_MIGRATION_API_BASE_URL`, or `PRISMIC_ASSET_API_BASE_URL` emit a
  startup warning.
- Set `PRISMIC_ENFORCE_TRUSTED_ENDPOINTS=1` to block startup on such overrides.

## MCP Tools

### `prismic_get_repository_context`

Return non-secret runtime context so agents know which repository this MCP
server is configured to use.

Example output shape:

```json
{
  "context": {
    "repository": "your-repo",
    "content_api_base_url": "https://your-repo.cdn.prismic.io/api/v2",
    "migration_api_base_url": "https://migration.prismic.io",
    "asset_api_base_url": "https://asset-api.prismic.io",
    "has_content_api_token": false,
    "has_write_credentials": true,
    "has_asset_credentials": true
  }
}
```

### `prismic_get_refs`

Return the repository `refs` array from Prismic Content API root (`/api/v2`).

Important:

- Refs are repository-level pointers (`master`, preview, release refs).
- They are not per-document refs.
- Use a ref value with `prismic_get_documents`/`prismic_get_document` via the
  `ref` parameter to read that content version.

Example output shape:

```json
{
  "refs": [
    {
      "id": "master",
      "ref": "aahE6hoAAE0AtrIS",
      "label": "Master",
      "isMasterRef": true
    }
  ]
}
```

### `prismic_get_types`

Return repository custom types from Prismic Content API root (`/api/v2`).

Important:

- This is based on the Content API root `types` map.
- It returns normalized entries with `id` and `label`.

Example output shape:

```json
{
  "types": [
    { "id": "blog_post", "label": "Blog Post" },
    { "id": "page", "label": "Page" }
  ]
}
```

### `prismic_get_releases`

Return only release refs from Prismic Content API root (`/api/v2`).

Important:

- This is a convenience subset of `prismic_get_refs`.
- It excludes refs where `isMasterRef` is `true`.
- Use returned release refs with read tools (`ref` parameter) to inspect
  release content.

Example output shape:

```json
{
  "releases": [
    {
      "id": "release-q1",
      "ref": "aahE6hoAAE0AtrIS",
      "label": "Q1 Release",
      "isMasterRef": false
    }
  ]
}
```

### `prismic_get_documents`

List documents with pagination.

`ref` can be used to read from an explicit Prismic content ref (for example, a
preview/draft ref). If omitted, the server resolves and uses the master ref.
Depending on repository API visibility settings, reading non-master refs may
require `PRISMIC_CONTENT_API_TOKEN`.
`q` is passed through to Prismic Content API predicates.
Treat `q` as trusted raw input only; do not forward untrusted prompt/user text
directly into `q`.
Supported `q` shapes are: `null`, string, or array of strings.
If `PRISMIC_DISABLE_RAW_Q=1`, raw `q` input is rejected.
`orderings` is passed through to Prismic Content API sort clauses.
`routes` is passed through to Prismic Content API route resolvers.

`type` is a convenience shortcut for:

- `[[at(document.type,"<type>")]]`

If both `type` and `q` are provided, the type predicate is prepended to `q`.

Input:

```json
{
  "type": "page",
  "lang": "en-us",
  "ref": "your-preview-or-release-ref",
  "page": 1,
  "page_size": 20,
  "q": null,
  "orderings": "[document.first_publication_date desc]",
  "routes": [
    { "type": "page", "path": "/:uid" },
    { "type": "homepage", "path": "/" }
  ]
}
```

Usage examples:

Filter by type via convenience mapping:

```json
{
  "type": "webinar_form",
  "page": 1,
  "page_size": 20
}
```

Equivalent explicit predicate in `q`:

```json
{
  "q": ["[[at(document.type,\"webinar_form\")]]"],
  "page": 1,
  "page_size": 20
}
```

Multiple predicates (example: type + tag) using explicit `q`:

```json
{
  "q": [
    "[[at(document.type,\"webinar_form\")]]",
    "[[at(document.tags,\"news\")]]"
  ],
  "lang": "en-us"
}
```

Sort by first publication date descending:

```json
{
  "q": ["[[at(document.type,\"blog\")]]"],
  "orderings": "[document.first_publication_date desc]",
  "page": 1,
  "page_size": 20
}
```

Sort by last publication date ascending:

```json
{
  "type": "chapter",
  "orderings": "[document.last_publication_date]",
  "page": 1,
  "page_size": 20
}
```

Use an explicit preview ref:

```json
{
  "type": "blog",
  "ref": "ZxY123...previewRef",
  "page": 1,
  "page_size": 20
}
```

Resolve `url` values with route resolvers:

```json
{
  "type": "page",
  "routes": [
    { "type": "homepage", "path": "/" },
    { "type": "page", "path": "/:uid" },
    { "type": "blog_post", "path": "/blog/:uid" }
  ],
  "page": 1,
  "page_size": 20
}
```

### `prismic_get_document`

Fetch one document by:

- `id`, or
- `type + uid` (optional `lang`)
- optional `ref` to read a specific preview/release ref instead of master
- depending on repository API visibility settings, non-master refs may require
  `PRISMIC_CONTENT_API_TOKEN`

### `prismic_get_media`

List media assets from the Prismic Asset API (`GET /assets`).

This tool maps directly to native Asset API query parameters:

- `asset_type` -> `assetType`
- `limit` -> `limit`
- `cursor` -> `cursor`
- `keyword` -> `keyword`

Requires:

- `PRISMIC_REPOSITORY`
- `PRISMIC_WRITE_API_TOKEN`

Example input:

```json
{
  "asset_type": "image",
  "limit": 25,
  "keyword": "hero"
}
```

### `prismic_add_media`

Upload a media file to the Prismic Asset API (`POST /assets`) using
`multipart/form-data`.

Inputs:

- `file_path` (required): local filesystem path to the file to upload
- `notes` (optional)
- `credits` (optional)
- `alt` (optional)

Requires:

- `PRISMIC_REPOSITORY`
- `PRISMIC_WRITE_API_TOKEN`
- `PRISMIC_UPLOAD_ROOT` (file must resolve inside this directory; symlink and traversal escapes are blocked)

Example input:

```json
{
  "file_path": "/absolute/path/to/hero.png",
  "notes": "Homepage hero image",
  "credits": "Design Team",
  "alt": "Person presenting on stage"
}
```

### `prismic_upsert_document`

Create/update one document in Migration API.

Important behavior:

- Writes to Prismic Migration workflow (Migration UI/release flow), not directly to Content API master visibility.
- A successful upsert can exist in Migration UI but still not appear in `prismic_get_document(s)` master reads until release/publish workflow makes it readable.
- To read back migrated content before publish, get a release ref via `prismic_get_releases` (or `prismic_get_refs`) and query read tools with `ref=<release_ref>`. Supply `PRISMIC_CONTENT_API_TOKEN` when repo/API settings require authenticated reads.
- Supports `dry_run=true` to validate request shape without writing.

### `prismic_upsert_documents`

Batch create/update documents in Migration API.

Important behavior:

- Same visibility caveat as single upsert: Migration success does not guarantee immediate master-read visibility.
- Read-back pattern is the same as single upsert: use explicit `ref` with read tools (plus `PRISMIC_CONTENT_API_TOKEN` when required by repo settings).
- Supports `dry_run` and `fail_fast`.

## Error and Safety Behavior

- Read tools do not require write credentials.
- Write tools fail fast if write credentials are missing.
- Write retries only on `429`, `503`, `504` with exponential backoff + jitter.
- Non-retryable `4xx` errors fail immediately.
- Batch upsert enforces `PRISMIC_MAX_BATCH_SIZE`.
- Logs are written to stderr only, with token redaction.

## Testing

Run default tests:

```bash
python3 -m pytest -q
```

Run live upstream read tests:

```bash
PRISMIC_RUN_LIVE_TESTS=1 python3 -m pytest -q tests/test_real_prismic_api.py
```

Run live upstream write test (writes into Migration Release):

```bash
PRISMIC_RUN_LIVE_TESTS=1 \
PRISMIC_RUN_LIVE_WRITE_TESTS=1 \
PRISMIC_LIVE_TEST_WRITE_TYPE=page \
python3 -m pytest -q tests/test_real_prismic_api.py
```
