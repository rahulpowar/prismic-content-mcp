"""Module entry point for `python -m prismic_content_mcp`."""

from __future__ import annotations

import logging
import os
import sys
from urllib.parse import quote, quote_plus

from .server import run_server


SECRET_ENV_KEYS = (
    "PRISMIC_WRITE_API_TOKEN",
    "PRISMIC_MIGRATION_API_KEY",
    "PRISMIC_CONTENT_API_TOKEN",
)


class SecretRedactionFilter(logging.Filter):
    """Redact known secret values from log output."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = self._build_secret_variants(secrets)

    @staticmethod
    def _build_secret_variants(secrets: list[str]) -> list[str]:
        """Build literal and URL-encoded tokens for reliable redaction."""

        variants: list[str] = []
        seen: set[str] = set()

        for secret in secrets:
            normalized = secret.strip()
            # Avoid broad accidental redaction for very short values.
            if len(normalized) < 6:
                continue

            candidates = {
                normalized,
                quote_plus(normalized, safe=""),
                quote(normalized, safe=""),
            }
            for candidate in candidates:
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                variants.append(candidate)

        return variants

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "[REDACTED]")

        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _read_secrets_from_env() -> list[str]:
    """Load configured secret values for redaction."""

    return [os.getenv(key, "").strip() for key in SECRET_ENV_KEYS]


def configure_logging() -> None:
    """Configure stderr logging only (stdout is reserved for MCP JSON-RPC in stdio)."""

    level_name = os.getenv("PRISMIC_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(SecretRedactionFilter(_read_secrets_from_env()))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # Keep verbose transport logs off stdout/stderr unless explicitly requested.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    """Entry point."""

    configure_logging()
    run_server()


if __name__ == "__main__":
    main()
