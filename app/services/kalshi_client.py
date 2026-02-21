from __future__ import annotations

from typing import Any
import httpx


class KalshiClient:
    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=20.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_markets(
        self,
        *,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        status: str | None = "open",
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """
        GET /markets
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status

        resp = await self._client.get("/markets", params=params)
        resp.raise_for_status()
        return resp.json()
