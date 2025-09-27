import asyncio
import time
import json
import aiohttp
from typing import Optional
from config import MOXFIELD_USER_AGENT

class _RateLimiter:
    def __init__(self, min_interval: float = 1.0):
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            wait_for = self._min_interval - (now - self._last)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last = time.monotonic()

_limiter = _RateLimiter(min_interval=1.0)
_session: Optional[aiohttp.ClientSession] = None

def _get_headers():
    if not MOXFIELD_USER_AGENT:
        raise RuntimeError("MOXFIELD_USER_AGENT not configured")
        # return {"User-Agent": "CA-DiscordBot/1.0"}  # fallback (not recommended)
    return {
        "User-Agent": MOXFIELD_USER_AGENT
    }

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

async def fetch_json(url: str, timeout: float = 10.0) -> dict:
    """
    Fetch JSON from Moxfield respecting UA and rate-limits.
    """
    await _limiter.wait()
    session = await get_session()
    headers = _get_headers()
    async with session.get(url, headers=headers, timeout=timeout) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"Fetch failed: status={resp.status}")
        try:
            return json.loads(text)
        except Exception as e:
            raise RuntimeError(f"Invalid JSON response: {e}")

async def close():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
