"""MCP tool registration. The exposed surface is intentionally read-only."""

from datetime import datetime
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from yeastar_mcp import __version__
from yeastar_mcp.models import (
    IVR,
    Agent,
    AgentCallSummary,
    AgentPerformance,
    CallActivity,
    CallDetail,
    CallPage,
    CallStats,
    Capabilities,
    ContactCallReport,
    Extension,
    ExtensionPerformance,
    IVRAnalysis,
    PBXInfo,
    Period,
    Queue,
    QueuePerformance,
    QueueWaitTimes,
    RingGroup,
    RingGroupStats,
    RoutingAnalysis,
)
from yeastar_mcp.services import MAX_CDR_RECORDS, YeastarService

TOOL_NAMES = {
    "get_pbx_info",
    "get_capabilities",
    "list_queues",
    "list_agents",
    "list_ivrs",
    "list_extensions",
    "list_ring_groups",
    "get_extension_performance",
    "get_call_stats",
    "get_call_activity",
    "get_ivr_analysis",
    "get_routing_analysis",
    "get_contact_call_stats",
    "get_ring_group_stats",
    "get_agent_call_summary",
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
RecordLimit = Annotated[int, Field(ge=1, le=MAX_CDR_RECORDS)]
ExampleLimit = Annotated[int, Field(ge=0, le=100)]


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
    async def list_ivrs() -> list[IVR]:
        """List minimal IVR identifiers needed to request aggregate IVR reports."""
        return await service.list_ivrs()

    @mcp.tool(annotations=READ_ONLY)
    async def list_extensions() -> list[Extension]:
        """List minimal extension identifiers and names for collaborator reports."""
        return await service.list_extensions()

    @mcp.tool(annotations=READ_ONLY)
    async def list_ring_groups() -> list[RingGroup]:
        """List minimal ring-group identifiers needed for ring-group reports."""
        return await service.list_ring_groups()

    @mcp.tool(annotations=READ_ONLY)
    async def get_extension_performance(
        start: datetime,
        end: datetime,
        extension_ids: list[int],
        communication_type: Literal["Inbound", "Outbound", "Internal"] | None = None,
        api_version: APIVersion = "v2.0",
    ) -> list[ExtensionPerformance]:
        """Return one call-count and talk-time row per named extension."""
        return await service.get_extension_performance(
            period=Period(start=start, end=end),
            extension_ids=extension_ids,
            communication_type=communication_type,
            api_version=api_version,
        )

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
    async def get_call_activity(
        start: datetime,
        end: datetime,
        bucket: Literal["hour", "day", "week", "month"] = "day",
        queue_ids: list[int] | None = None,
        api_version: APIVersion = "v2.0",
        max_records: RecordLimit = MAX_CDR_RECORDS,
        duration_band_seconds: int = 120,
        duration_cap_seconds: int = 600,
    ) -> CallActivity:
        """Aggregate call volume, statuses, directions, and durations into local time buckets."""
        return await service.get_call_activity(
            period=Period(start=start, end=end),
            bucket=bucket,
            queue_ids=queue_ids,
            api_version=api_version,
            max_records=max_records,
            duration_band_seconds=duration_band_seconds,
            duration_cap_seconds=duration_cap_seconds,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_ivr_analysis(
        start: datetime,
        end: datetime,
        ivr_ids: list[int],
        include_calls: bool = False,
        include_numbers: bool = False,
        max_call_examples: ExampleLimit = 20,
        keys: list[str] | None = None,
        first_keypress_only: bool = False,
        api_version: APIVersion = "v2.0",
    ) -> list[IVRAnalysis]:
        """Map IVR keypresses to destinations; detailed callers are opt-in and bounded."""
        service.ensure_raw_numbers_allowed(include_numbers)
        return await service.get_ivr_analysis(
            period=Period(start=start, end=end),
            ivr_ids=ivr_ids,
            api_version=api_version,
            include_calls=include_calls,
            include_numbers=include_numbers,
            max_call_examples=max_call_examples,
            keys=keys,
            first_keypress_only=first_keypress_only,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_routing_analysis(
        start: datetime,
        end: datetime,
        queue_ids: list[int] | None = None,
        min_segments: int = 3,
        long_routing_seconds: int = 45,
        max_examples: ExampleLimit = 50,
        max_records: RecordLimit = MAX_CDR_RECORDS,
        include_numbers: bool = False,
        api_version: APIVersion = "v2.0",
    ) -> RoutingAnalysis:
        """Aggregate routing delays, multi-leg calls, possible loops, and route changes locally."""
        service.ensure_raw_numbers_allowed(include_numbers)
        return await service.get_routing_analysis(
            period=Period(start=start, end=end),
            queue_ids=queue_ids,
            min_segments=min_segments,
            long_routing_seconds=long_routing_seconds,
            max_examples=max_examples,
            max_records=max_records,
            include_numbers=include_numbers,
            api_version=api_version,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_contact_call_stats(
        start: datetime,
        end: datetime,
        phonebook_ids: list[int] | None = None,
        include_numbers: bool = False,
        api_version: APIVersion = "v2.0",
        max_records: RecordLimit = MAX_CDR_RECORDS,
    ) -> ContactCallReport:
        """Cross directory contacts with CDR locally; return aggregates, not raw CDR rows."""
        service.ensure_raw_numbers_allowed(include_numbers)
        return await service.get_contact_call_stats(
            period=Period(start=start, end=end),
            phonebook_ids=phonebook_ids,
            include_numbers=include_numbers,
            api_version=api_version,
            max_records=max_records,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_ring_group_stats(
        start: datetime,
        end: datetime,
        ring_group_ids: list[int],
        api_version: APIVersion = "v2.0",
    ) -> list[RingGroupStats]:
        """Return ring-group totals and per-member answered-call metrics."""
        return await service.get_ring_group_stats(
            period=Period(start=start, end=end),
            ring_group_ids=ring_group_ids,
            api_version=api_version,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_agent_call_summary(
        start: datetime,
        end: datetime,
        queue_id: int,
        agent_ids: list[int] | None = None,
        api_version: APIVersion = "v2.0",
    ) -> list[AgentCallSummary]:
        """Return named agents' queue, inbound, outbound, and duration totals."""
        return await service.get_agent_call_summary(
            period=Period(start=start, end=end),
            queue_id=queue_id,
            agent_ids=agent_ids,
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
        service.ensure_raw_numbers_allowed(include_numbers)
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
        service.ensure_raw_numbers_allowed(include_numbers)
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
        call_from: str | None = None,
        call_to: str | None = None,
        routing_duration: str | None = None,
        segments: str | None = None,
        disconnected_by: str | None = None,
        page: int = 1,
        page_size: int = 100,
        include_numbers: bool = False,
        api_version: APIVersion = "v2.0",
    ) -> CallPage:
        """List normalized CDRs with bounded pagination and numbers masked by default."""
        service.ensure_raw_numbers_allowed(include_numbers)
        return await service.get_calls(
            period=Period(start=start, end=end),
            page=page,
            page_size=page_size,
            queue_ids=queue_ids,
            status=status,
            call_from=call_from,
            call_to=call_to,
            routing_duration=routing_duration,
            segments=segments,
            disconnected_by=disconnected_by,
            include_numbers=include_numbers,
            api_version=api_version,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_call_details(
        call_id: str,
        include_numbers: bool = False,
    ) -> CallDetail:
        """Return normalized v2 CDR detail and timeline; numbers are masked by default."""
        service.ensure_raw_numbers_allowed(include_numbers)
        return await service.get_call_details(call_id, include_numbers=include_numbers)

    return mcp
