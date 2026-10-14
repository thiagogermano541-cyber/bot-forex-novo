import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from ..schemas import Quote, Snapshot


class Provider(Protocol):
    async def snapshot(self) -> Snapshot: ...
    def stream(self) -> AsyncIterator[Quote]: ...


async def get_json(client: httpx.AsyncClient, url: str, **kwargs):
    for attempt in range(3):
        try:
            response = await client.get(url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    response.raise_for_status()
                await asyncio.sleep(min(float(response.headers.get("retry-after", 2 ** attempt)), 15))
                continue
            response.raise_for_status()
            return response.json()
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("Provider unavailable")
