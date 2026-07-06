import pytest
from unittest.mock import patch, AsyncMock
from api.utils.url_validation import validate_public_url


class TestValidatePublicUrl:
    @pytest.mark.asyncio
    async def test_valid_https_url(self):
        """Public HTTPS URL should pass validation."""
        with patch('socket.getaddrinfo') as mock_getaddrinfo:
            # Mock a valid public IP resolution
            mock_getaddrinfo.return_value = [
                (2, 1, 6, '', ('93.184.216.34', 443))  # example.com actual IP
            ]
            await validate_public_url("https://api.example.com/v1")

    @pytest.mark.asyncio
    async def test_localhost_rejected(self):
        """localhost URLs must be blocked."""
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_public_url("http://localhost:8080/mcp")

    @pytest.mark.asyncio
    async def test_127_0_0_1_rejected(self):
        """Loopback IP must be blocked."""
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_public_url("http://127.0.0.1:8080/mcp")

    @pytest.mark.asyncio
    async def test_private_10_range_rejected(self):
        """10.x.x.x must be blocked."""
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_public_url("http://10.0.0.1/mcp")

    @pytest.mark.asyncio
    async def test_private_172_range_rejected(self):
        """172.16.x.x must be blocked."""
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_public_url("http://172.16.0.1/mcp")

    @pytest.mark.asyncio
    async def test_private_192_range_rejected(self):
        """192.168.x.x must be blocked."""
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_public_url("http://192.168.1.1/mcp")

    @pytest.mark.asyncio
    async def test_cloud_metadata_rejected(self):
        """169.254.x.x (cloud metadata) must be blocked."""
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_public_url("http://169.254.169.254/latest/meta-data/")

    @pytest.mark.asyncio
    async def test_zero_ip_rejected(self):
        """0.0.0.0 must be blocked."""
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_public_url("http://0.0.0.0/mcp")

    @pytest.mark.asyncio
    async def test_invalid_scheme_rejected(self):
        """Non-HTTP schemes must be blocked."""
        with pytest.raises(ValueError, match="scheme"):
            await validate_public_url("ftp://public.example.com/file")

    @pytest.mark.asyncio
    async def test_invalid_url_rejected(self):
        """Malformed URLs must be rejected."""
        with pytest.raises(ValueError):
            await validate_public_url("not-a-url")
