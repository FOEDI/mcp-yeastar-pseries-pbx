"""MCP tool registration. The exposed surface is intentionally read-only."""

from datetime import datetime
from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from yeastar_mcp import __version__
from yeastar_mcp.models import (
    Agent,
    AgentPerformance,
    CallDetail,
    CallPage,
    CallStats,
    Capabilities,
    PBXInfo,
    Period,
    Queue,
    QueuePerformance,
    QueueWaitTimes,
)
from yeastar_mcp.services import YeastarService

TOOL_NAMES = {
    "get_pbx_info",
    "get_capabilities",
    "list_queues",
    "list_agents",
    "get_call_stats",
    "get_queue_performance",
    "get_queue_wait_times",
    "get_agent_performance",
    "get_missed_calls",
    "get_abandoned_calls",
    "get_calls",
    "get_call_details",
}

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
APIVersion = Literal["v1.0", "v2.0"]


def create_server(service: YeastarService) -> MCPServer:
    """Create an in-process-testable stdio MCP server."""
    mcp = MCPServer(
        "yeastar-mcp",
        version=__version__,
        instructions=(
            "Read-only analytics for one Yeastar P-Series PBX. Prefer aggregate tools. "
            "Call-level tools mask telephone numbers unless include_numbers=true is explicit."
        ),
    )

    @mcp.tool(annotations=READ_ONLY)
    async def get_pbx_info() -> PBXInfo:
        """Return normalized model, firmware, clock, and uptime information."""
        return await service.get_pbx_info()

    @mcp.tool(annotations=READ_ONLY)
    async def get_capabilities() -> Capabilities:
        """Return the detected read-only API versions and supported analytics capabilities."""
        return await service.get_capabilities()

    @mcp.tool(annotations=READ_ONLY)
    async def list_queues() -> list[Queue]:
        """List normalized queues without exposing Yeastar-specific field names."""
        return await service.list_queues()

    @mcp.tool(annotations=READ_ONLY)
    async def list_agents() -> list[Agent]:
        """List queue agents and their memberships, deduplicated across queues."""
        return await service.list_agents()

    @mcp.tool(annotations=READ_ONLY)
    async def get_call_stats(
        start: datetime,
        end: datetime,
        queue_ids: list[int] | None = None,
        extension_ids: list[int] | None = None,
        api_version: APIVersion = "v2.0",
    ) -> CallStats:
        """Aggregate call counts; use Yeastar reports when extension IDs are supplied."""
        return await service.get_call_stats(
            period=Period(start=start, end=end),
            queue_ids=queue_ids,
            extension_ids=extension_ids,
            api_version=api_version,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_queue_performance(
        start: datetime,
        end: datetime,
        queue_ids: list[int] | None = None,
        api_version: APIVersion = "v2.0",
        abandon_time_seconds: int | None = None,
    ) -> list[QueuePerformance]:
        """Return aggregate Queue Performance report metrics for an inclusive PBX-local period."""
        return await service.get_queue_performance(
            period=Period(start=start, end=end),
            queue_ids=queue_ids,
            api_version=api_version,
            abandon_time_seconds=abandon_time_seconds,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_queue_wait_times(
        start: datetime,
        end: datetime,
        queue_ids: list[int] | None = None,
        api_version: APIVersion = "v2.0",
    ) -> list[QueueWaitTimes]:
        """Return Queue AVG Wait & Talk Time for one complete day, month, or year."""
        return await service.get_queue_wait_times(
            period=Period(start=start, end=end), queue_ids=queue_ids, api_version=api_version
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_agent_performance(
        start: datetime,
        end: datetime,
        queue_id: int,
        agent_ids: list[int] | None = None,
        api_version: APIVersion = "v2.0",
        abandon_time_seconds: int | None = None,
    ) -> list[AgentPerformance]:
        """Return normalized Agent Performance metrics for one queue and period."""
        return await service.get_agent_performance(
            period=Period(start=start, end=end),
            queue_id=queue_id,
            agent_ids=agent_ids,
            api_version=api_version,
            abandon_time_seconds=abandon_time_seconds,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_missed_calls(
        start: datetime,
        end: datetime,
        page: int = 1,
        page_size: int = 100,
        include_numbers: bool = False,
        api_version: APIVersion = "v2.0",
    ) -> CallPage:
        """List unreturned missed calls; telephone numbers are masked by default."""
        return await service.get_missed_calls(
            period=Period(start=start, end=end),
            page=page,
            page_size=page_size,
            include_numbers=include_numbers,
            api_version=api_version,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_abandoned_calls(
        start: datetime,
        end: datetime,
        queue_ids: list[int] | None = None,
        page: int = 1,
        page_size: int = 100,
        include_numbers: bool = False,
        api_version: APIVersion = "v2.0",
    ) -> CallPage:
        """List abandoned CDRs; telephone numbers are masked by default."""
        return await service.get_abandoned_calls(
            period=Period(start=start, end=end),
            page=page,
            page_size=page_size,
            queue_ids=queue_ids,
            include_numbers=include_numbers,
            api_version=api_version,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_calls(
        start: datetime,
        end: datetime,
        queue_ids: list[int] | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
        include_numbers: bool = False,
        api_version: APIVersion = "v2.0",
    ) -> CallPage:
        """List normalized CDRs with bounded pagination and numbers masked by default."""
        return await service.get_calls(
            period=Period(start=start, end=end),
            page=page,
            page_size=page_size,
            queue_ids=queue_ids,
            status=status,
            include_numbers=include_numbers,
            api_version=api_version,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_call_details(
        call_id: str,
        include_numbers: bool = False,
    ) -> CallDetail:
        """Return normalized v2 CDR detail and timeline; numbers are masked by default."""
        return await service.get_call_details(call_id, include_numbers=include_numbers)

    return mcp
