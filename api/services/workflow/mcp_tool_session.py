"""Single unit that knows the MCP protocol + credentials.

Wraps the vendored Pipecat ``MCPClient`` for connection/session, builds
streamable-HTTP params from a Dograh credential, exposes namespaced
``FunctionSchema``s, and proxies tool calls. Connection failures degrade
(``available = False``) instead of raising — the call must survive a
dead MCP server.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from loguru import logger
from mcp.client.session_group import StreamableHttpParameters
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.mcp_service import MCPClient

from api.services.workflow.tools.mcp_tool import namespace_function_name
from api.utils.credential_auth import build_auth_header

if TYPE_CHECKING:
    from api.db.models import ExternalCredentialModel


def build_streamable_http_params(
    *,
    url: str,
    credential: Optional["ExternalCredentialModel"],
    timeout_secs: int,
    sse_read_timeout_secs: int,
) -> StreamableHttpParameters:
    """Build Pipecat/MCP streamable-HTTP params, injecting the auth header
    from an ExternalCredentialModel (reuses the http_api credential path)."""
    headers: Optional[Dict[str, str]] = None
    if credential is not None:
        auth = build_auth_header(credential)
        headers = auth or None
    return StreamableHttpParameters(
        url=url,
        headers=headers,
        timeout=timedelta(seconds=timeout_secs),
        sse_read_timeout=timedelta(seconds=sse_read_timeout_secs),
    )


class McpToolSession:
    """One live MCP server connection for the duration of a call."""

    def __init__(
        self,
        *,
        tool_uuid: str,
        tool_name: str,
        url: str,
        credential: Optional["ExternalCredentialModel"],
        tools_filter: List[str],
        timeout_secs: int,
        sse_read_timeout_secs: int,
    ) -> None:
        self._tool_uuid = tool_uuid
        self._tool_name = tool_name
        self._url = url
        self._credential = credential
        # An empty list is intentionally treated as "no filter (expose all
        # tools)" — Pipecat's MCPClient applies a filter only when this is a
        # non-empty list, so [] and None are equivalent ("all tools").
        self._tools_filter = tools_filter or None
        self._timeout_secs = timeout_secs
        self._sse_read_timeout_secs = sse_read_timeout_secs

        self._client: Optional[MCPClient] = None
        self._session: Any = None  # mcp.ClientSession (read once after start)
        self._schemas: List[FunctionSchema] = []
        # namespaced LLM name -> original MCP tool name
        self._name_map: Dict[str, str] = {}
        self.available: bool = False

    async def start(self) -> None:
        """Connect, initialize, and cache the tool list. Never raises —
        on any failure the session is marked unavailable."""
        try:
            params = build_streamable_http_params(
                url=self._url,
                credential=self._credential,
                timeout_secs=self._timeout_secs,
                sse_read_timeout_secs=self._sse_read_timeout_secs,
            )
            self._client = MCPClient(params, tools_filter=self._tools_filter)
            await self._client.start()
            # Single, isolated touch of Pipecat internals (vendored submodule).
            self._session = self._client._active_session
            tools_schema = await self._client.get_tools_schema()

            fallback = self._tool_uuid[:8] if self._tool_uuid else "server"
            for fs in tools_schema.standard_tools:
                ns_name = namespace_function_name(
                    self._tool_name, fs.name, fallback=fallback
                )
                self._name_map[ns_name] = fs.name
                self._schemas.append(
                    FunctionSchema(
                        name=ns_name,
                        description=fs.description,
                        properties=fs.properties,
                        required=fs.required,
                    )
                )
            self.available = True
            logger.info(
                f"MCP session ready for tool '{self._tool_name}' "
                f"({self._tool_uuid}): {sorted(self._name_map)}"
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except asyncio.CancelledError as e:
            # Empirically, a dead/unreachable MCP server does NOT surface as a
            # plain Exception here. The real failure is httpx.ConnectError, but
            # anyio's streamablehttp_client task group, while tearing down that
            # ConnectError, re-surfaces it to our frame as an *internal*
            # cancel-scope CancelledError carrying the signature message
            # "Cancelled via cancel scope <id>". A genuine *external*
            # cancellation (call teardown / shutdown) is a bare CancelledError
            # (empty args) or one with an application-chosen message. Type, MRO,
            # context chain, and asyncio task.cancelling() are all identical
            # between the two, so the anyio scope-signature message is the only
            # reliable discriminator. Re-raise genuine external cancellation to
            # preserve structured concurrency; degrade only on the anyio
            # connect-teardown artifact.
            msg = "" if not e.args else str(e.args[0] or "")
            if not msg.startswith("Cancelled via cancel scope"):
                raise
            await self._degrade(e)
        except Exception as e:  # noqa: BLE001 — see _degrade docstring
            # Defensive: if a future Pipecat/httpx version surfaces the connect
            # failure directly (e.g. httpx.ConnectError) instead of via the
            # anyio cancel-scope artifact above, still degrade gracefully.
            await self._degrade(e)

    async def _degrade(self, e: BaseException) -> None:
        """Mark this session unavailable and tear down any dangling client so
        start() leaves self._client either fully usable or None. The contract
        requires graceful degradation on any *connect* failure (never raising
        for a dead MCP server) while genuine external cancellation /
        KeyboardInterrupt / SystemExit are re-raised by the caller."""
        self.available = False
        self._schemas = []
        self._name_map = {}
        # Self-contained cleanup: _client.start() may have succeeded before a
        # later step (e.g. get_tools_schema()) failed, leaving an open client.
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            finally:
                self._client = None
                self._session = None
        logger.warning(
            f"MCP session unavailable for tool '{self._tool_name}' "
            f"({self._tool_uuid}) at {self._url}: {e!r}. "
            f"Call proceeds without these tools."
        )

    @property
    def call_timeout_secs(self) -> float:
        """Pipecat function-call timeout for this server's tools. Slightly
        longer than the transport read timeout so a slow MCP call surfaces
        as a structured tool error (handled in the handler) rather than a
        hard pipeline timeout."""
        return float(self._sse_read_timeout_secs) + 5.0

    def function_schemas(
        self, allowed_raw_names: Optional[Set[str]] = None
    ) -> List[FunctionSchema]:
        """Return cached FunctionSchemas, optionally filtered by raw MCP tool name.

        ``allowed_raw_names=None`` returns all schemas. An empty set returns none.
        Raw names are the pre-namespace MCP tool names (e.g. ``echo``, not
        ``mcp__slug__echo``).
        """
        if allowed_raw_names is None:
            return list(self._schemas)
        return [
            s for s in self._schemas if self._name_map.get(s.name) in allowed_raw_names
        ]

    def discovered_tools(self) -> List[Dict[str, str]]:
        """Raw MCP tool catalog for UI/cache: ``[{name, description}]``
        using the *raw* server names (not the namespaced LLM names).
        Empty if the session is unavailable."""
        out: List[Dict[str, str]] = []
        for s in self._schemas:
            raw = self._name_map.get(s.name)
            if raw is None:
                continue
            out.append({"name": raw, "description": s.description or ""})
        return out

    async def call(self, namespaced_name: str, arguments: Dict[str, Any]) -> str:
        """Invoke an MCP tool by its namespaced LLM name. Returns a string
        (flattened text content). Raises if the session is unavailable so
        the caller can map it to a structured error for the LLM."""
        if not self.available or self._session is None:
            raise RuntimeError(f"MCP session unavailable for {namespaced_name}")
        original = self._name_map.get(namespaced_name)
        if original is None:
            raise RuntimeError(f"Unknown MCP function {namespaced_name}")
        result = await self._session.call_tool(original, arguments=arguments)
        text = ""
        for content in getattr(result, "content", []) or []:
            if getattr(content, "text", None):
                text += content.text
        return text or "Sorry, the MCP tool returned no content."

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as e:
                logger.warning(f"Error closing MCP session {self._tool_uuid}: {e}")
            finally:
                self._client = None
                self._session = None


async def discover_mcp_tools(
    *,
    url: str,
    credential: Optional["ExternalCredentialModel"],
    timeout_secs: int,
    sse_read_timeout_secs: int,
) -> List[Dict[str, str]]:
    """Open an ephemeral MCP session, list its tools, close it. Returns
    ``[{name, description}]`` (raw names). Never raises — on any connect
    failure returns ``[]``."""
    session = McpToolSession(
        tool_uuid="discover",
        tool_name="discover",
        url=url,
        credential=credential,
        tools_filter=[],
        timeout_secs=timeout_secs,
        sse_read_timeout_secs=sse_read_timeout_secs,
    )
    await session.start()
    try:
        if not session.available:
            return []
        return session.discovered_tools()
    finally:
        await session.close()
