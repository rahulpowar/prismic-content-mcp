"""Tests for runtime and logging environment defaults."""

from __future__ import annotations

import logging

from prismic_content_mcp.__main__ import SecretRedactionFilter, configure_logging
from prismic_content_mcp.server import (
    RuntimeConfig,
    _warn_streamable_http_exposure,
    load_runtime_config,
)


def test_runtime_config_defaults_transport_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("PRISMIC_MCP_TRANSPORT", raising=False)

    config = load_runtime_config()

    assert config.transport == "stdio"


def test_runtime_config_defaults_transport_when_env_blank(monkeypatch) -> None:
    monkeypatch.setenv("PRISMIC_MCP_TRANSPORT", "   ")

    config = load_runtime_config()

    assert config.transport == "stdio"


def test_configure_logging_defaults_to_info_when_env_blank(monkeypatch) -> None:
    monkeypatch.setenv("PRISMIC_LOG_LEVEL", "  ")

    root_logger = logging.getLogger()
    prior_handlers = list(root_logger.handlers)
    prior_level = root_logger.level

    try:
        configure_logging()
        assert logging.getLogger().level == logging.INFO
    finally:
        root_logger.handlers.clear()
        for handler in prior_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(prior_level)


def test_secret_redaction_filter_preserves_args_when_no_redaction() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    filter_ = SecretRedactionFilter(["secret-token"])

    assert filter_.filter(record) is True
    assert record.msg == "hello %s"
    assert record.args == ("world",)
    assert record.getMessage() == "hello world"


def test_secret_redaction_filter_redacts_encoded_and_clears_args() -> None:
    token = "abc123=xyz"
    encoded = "abc123%3Dxyz"
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=f"url token={encoded}",
        args=(),
        exc_info=None,
    )
    filter_ = SecretRedactionFilter([token])

    assert filter_.filter(record) is True
    assert "[REDACTED]" in record.msg
    assert encoded not in record.msg
    assert record.args == ()


def test_warn_streamable_http_exposure_for_public_bind(caplog) -> None:
    caplog.set_level(logging.WARNING)

    _warn_streamable_http_exposure(
        RuntimeConfig(transport="streamable-http", host="0.0.0.0", port=8000, path="/mcp")
    )

    assert "without built-in auth" in caplog.text
    assert "0.0.0.0" in caplog.text
