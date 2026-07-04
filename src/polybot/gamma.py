"""Read-only client for public Polymarket Gamma API data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


class GammaClient:
    """Small wrapper around public Gamma API endpoints."""

    def __init__(self, base_url: str = GAMMA_BASE_URL, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch_events(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        extra_params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        params = {"limit": limit, "offset": offset}
        if extra_params:
            params.update(extra_params)
        return _coerce_list(self._get_json("/events", params=params))

    def fetch_markets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        extra_params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        params = {"limit": limit, "offset": offset}
        if extra_params:
            params.update(extra_params)
        return _coerce_list(self._get_json("/markets", params=params))

    def public_search(self, query: str, *, limit: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query}
        if limit is not None:
            params["limit"] = limit
        payload = self._get_json("/public-search", params=params)
        return payload if isinstance(payload, dict) else {"results": payload}

    def _get_json(self, path: str, *, params: Mapping[str, Any]) -> Any:
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "polybot-paper-research/0.1"},
        ) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()


def _coerce_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("markets", "events", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []

