"""Utility for getting the cloudflared tunnel URL at runtime."""

import asyncio
import re
from typing import Optional

import aiohttp
from loguru import logger


class TunnelURLProvider:
    """Provider for getting tunnel URLs from cloudflared service."""

    _cached_urls: Optional[tuple[str, str]] = None
    _last_check_time: float = 0.0
    _CACHE_TTL_FOUND: float = 60.0  # 1 min when found
    _CACHE_TTL_NOT_FOUND: float = 20.0  # 20s when not found (prevents healthcheck lag)

    @classmethod
    async def get_tunnel_urls(cls) -> tuple[str, str]:
        """
        Get the tunnel URLs for external access.

        Returns:
            tuple[str, str]: (https_url, wss_url) - Both URLs include full protocol

        Raises:
            ValueError: If no tunnel URL can be determined
        """
        import time

        now = time.time()
        ttl = cls._CACHE_TTL_FOUND if cls._cached_urls else cls._CACHE_TTL_NOT_FOUND
        if cls._last_check_time > 0 and (now - cls._last_check_time) < ttl:
            if cls._cached_urls:
                return cls._cached_urls
            raise ValueError("No tunnel URL available (cached failure)")

        try:
            urls = await cls._get_cloudflared_urls()
            cls._last_check_time = time.time()
            cls._cached_urls = urls
            if urls:
                return urls
        except Exception as e:
            cls._last_check_time = time.time()
            cls._cached_urls = None
            logger.debug(f"Failed to get tunnel URL from cloudflared: {e}")

        raise ValueError(
            "No tunnel URL available. Please set BACKEND_API_ENDPOINT environment "
            "variable or ensure cloudflared service is running."
        )

    @classmethod
    async def _check_single_endpoint(
        cls, session: aiohttp.ClientSession, ep: str
    ) -> Optional[tuple[str, str]]:
        try:
            async with session.get(
                ep, timeout=aiohttp.ClientTimeout(total=0.5)
            ) as response:
                if response.status != 200:
                    return None
                text = await response.text()
                if "hostname" in text and "{" in text:
                    import json
                    try:
                        j = json.loads(text)
                        h = j.get("hostname")
                        if h:
                            h = h.replace("https://", "").replace("wss://", "")
                            return f"https://{h}", f"wss://{h}"
                    except Exception:
                        pass

                match = re.search(r'userHostname="([^"]+)"', text)
                if match:
                    hostname = match.group(1).replace("https://", "").replace("wss://", "")
                    return f"https://{hostname}", f"wss://{hostname}"

                match = re.search(r"([a-z0-9-]+\.trycloudflare\.com)", text)
                if match:
                    hostname = match.group(1).replace("https://", "").replace("wss://", "")
                    return f"https://{hostname}", f"wss://{hostname}"
        except Exception:
            pass
        return None

    @classmethod
    async def _get_cloudflared_urls(cls) -> Optional[tuple[str, str]]:
        """
        Query cloudflared metrics endpoints concurrently to get the tunnel URLs.
        """
        candidate_endpoints = [
            "http://127.0.0.1:20241/quicktunnel",
            "http://127.0.0.1:20241/metrics",
            "http://localhost:2000/metrics",
        ]

        try:
            async with aiohttp.ClientSession() as session:
                tasks = [
                    cls._check_single_endpoint(session, ep)
                    for ep in candidate_endpoints
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, tuple) and res[0]:
                        return res
        except Exception:
            pass

        return None
