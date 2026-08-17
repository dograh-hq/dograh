"""Utility for getting the cloudflared tunnel URL at runtime."""

import asyncio
import re
from typing import Optional

import aiohttp
from loguru import logger


class TunnelURLProvider:
    """Provider for getting tunnel URLs from cloudflared service."""

    METRICS_URLS = (
        "http://cloudflared:2000/metrics",
        "http://localhost:2000/metrics",
        "http://127.0.0.1:2000/metrics",
    )

    @classmethod
    async def get_tunnel_urls(cls) -> tuple[str, str]:
        """
        Get the tunnel URLs for external access.

        Returns:
            tuple[str, str]: (https_url, wss_url) - Both URLs include full protocol

        Raises:
            ValueError: If no tunnel URL can be determined
        """

        try:
            # Try to get URL from cloudflared metrics
            urls = await cls._get_cloudflared_urls()
            if urls:
                return urls
        except Exception as e:
            logger.warning(f"Failed to get tunnel URL from cloudflared: {e}")

        raise ValueError(
            "No tunnel URL available. Please set BACKEND_API_ENDPOINT environment "
            "variable or ensure cloudflared service is running."
        )

    @classmethod
    async def _get_cloudflared_urls(cls) -> Optional[tuple[str, str]]:
        """
        Query cloudflared metrics endpoint to get the tunnel URLs.

        Returns:
            Optional[tuple[str, str]]: (https_url, wss_url) with full protocols, or None if not found
        """
        async with aiohttp.ClientSession() as session:
            for metrics_url in cls.METRICS_URLS:
                try:
                    async with session.get(
                        metrics_url, timeout=aiohttp.ClientTimeout(total=1)
                    ) as response:
                        if response.status != 200:
                            logger.warning(
                                f"Cloudflared metrics returned status {response.status} from {metrics_url}"
                            )
                            continue

                        text = await response.text()

                        # Look for the tunnel URL in metrics
                        # Cloudflared exposes this in the userHostname metric
                        match = re.search(r'userHostname="([^"]+)"', text)
                        if match:
                            hostname = match.group(1)
                            # Remove https:// or wss:// if present
                            hostname = hostname.replace("https://", "").replace(
                                "wss://", ""
                            )
                            return "https://" + hostname, "wss://" + hostname

                        # Alternative: Look for trycloudflare.com domain
                        match = re.search(r"([a-z0-9-]+\.trycloudflare\.com)", text)
                        if match:
                            hostname = match.group(1)
                            hostname = hostname.replace("https://", "").replace(
                                "wss://", ""
                            )
                            return f"https://{hostname}", f"wss://{hostname}"

                        logger.warning(
                            f"Could not find tunnel URL in cloudflared metrics from {metrics_url}"
                        )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Timeout connecting to cloudflared metrics endpoint {metrics_url}"
                    )
                    continue
                except aiohttp.ClientError as e:
                    logger.warning(
                        f"Error connecting to cloudflared metrics endpoint {metrics_url}: {e}"
                    )
                    continue
                except Exception as e:
                    logger.error(
                        f"Unexpected error getting cloudflared URL from {metrics_url}: {e}"
                    )
                    continue

        return None
