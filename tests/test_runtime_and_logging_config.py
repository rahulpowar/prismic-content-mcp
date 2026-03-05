"""Tests for runtime and logging environment defaults."""

from __future__ import annotations

import logging

from prismic_content_mcp.__main__ import configure_logging
from prismic_content_mcp.server import load_runtime_config


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
