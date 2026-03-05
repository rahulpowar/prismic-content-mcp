"""Smoke tests validating async and HTTP mocking test harness setup."""

from __future__ import annotations

import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_async_harness_smoke() -> None:
    """Ensure pytest-asyncio is active and can run async tests."""

    await httpx.AsyncClient().aclose()


@respx.mock
@pytest.mark.asyncio
async def test_respx_mocking_smoke() -> None:
    """Ensure respx route mocking works with async HTTP clients."""

    route = respx.get("https://example.test/health").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    async with httpx.AsyncClient() as client:
        response = await client.get("https://example.test/health")

    assert route.called
    assert response.status_code == 200
    assert response.json() == {"ok": True}
