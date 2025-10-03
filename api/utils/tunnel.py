"""Utility for getting the cloudflared tunnel URL at runtime."""

import asyncio
import os
import re
from typing import Optional

import aiohttp
from loguru import logger


class TunnelURLProvider:
    """Provider for getting the tunnel URL from cloudflared or environment."""

    @classmethod
    async def get_tunnel_url(cls) -> str:
        """
        Get the tunnel URL for external access.

        Priority:
        1. BACKEND_API_ENDPOINT environment variable (if set)
        2. Query cloudflared metrics endpoint
        3. Raise error if neither available

        Returns:
            str: The tunnel domain (without protocol)

        Raises:
            ValueError: If no tunnel URL can be determined
        """
        # First priority: Check environment variable
        env_endpoint = os.getenv("BACKEND_API_ENDPOINT")
        if env_endpoint:
            logger.debug(f"Using BACKEND_API_ENDPOINT from environment: {env_endpoint}")
            return env_endpoint

        # Second priority: Query cloudflared
        try:
            # Try to get URL from cloudflared metrics
            url = await cls._get_cloudflared_url()
            if url:
                logger.info(f"Retrieved tunnel URL from cloudflared: {url}")
                return url
        except Exception as e:
            logger.warning(f"Failed to get tunnel URL from cloudflared: {e}")

        raise ValueError(
            "No tunnel URL available. Please set BACKEND_API_ENDPOINT environment "
            "variable or ensure cloudflared service is running."
        )

    @classmethod
    async def _get_cloudflared_url(cls) -> Optional[str]:
        """
        Query cloudflared metrics endpoint to get the tunnel URL.

        Returns:
            Optional[str]: The tunnel domain (without protocol), or None if not found
        """
        try:
            # Try to connect to cloudflared metrics endpoint
            # The service name in docker-compose is 'cloudflared'
            metrics_url = "http://cloudflared:2000/metrics"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    metrics_url, timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status != 200:
                        logger.warning(
                            f"Cloudflared metrics returned status {response.status}"
                        )
                        return None

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
                        return hostname

                    # Alternative: Look for trycloudflare.com domain
                    match = re.search(r"([a-z0-9-]+\.trycloudflare\.com)", text)
                    if match:
                        return match.group(1)

                    logger.warning("Could not find tunnel URL in cloudflared metrics")
                    return None

        except asyncio.TimeoutError:
            logger.warning("Timeout connecting to cloudflared metrics endpoint")
            return None
        except aiohttp.ClientError as e:
            logger.warning(f"Error connecting to cloudflared: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting cloudflared URL: {e}")
            return None
