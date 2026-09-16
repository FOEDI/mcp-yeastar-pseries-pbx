"""Business services and normalization for Yeastar analytics."""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from yeastar_mcp.endpoints import CDR_DETAIL_V2, PBX_INFO, QUEUES
from yeastar_mcp.models import (
    IVR,
    Agent,
    AgentCallSummary,
    AgentPerformance,
    CallActivity,
    CallActivityBucket,
    CallCounts,
    CallDetail,
    CallEvent,
    CallLeg,
    CallPage,
    CallRecord,
    CallStats,
    Capabilities,
    Capability,
    ContactCallReport,
    ContactCallStats,
    ContactRef,
    Extension,
    ExtensionPerformance,
    IVRAnalysis,
    IVRCall,
    IVRDestinationStat,
    Party,
    PBXInfo,
    Period,
    Queue,
    QueuePerformance,
    QueueRef,
    QueueWaitTimes,
    Rates,
    RingGroup,
    RingGroupMemberStats,
    RingGroupStats,
    RoutedDestination,
    RoutingAnalysis,
    RoutingAnomaly,
    RoutingCounts,
    TalkingMetrics,
    WaitingMetrics,
    WaitTimeBucket,
)

MAX_CDR_RECORDS = 100_000


class ReadClient(Protocol):
    async def aclose(self) -> None: ...

    async def get(
        self,
        endpoint: str,
        *,
        api_version: str = "v1.0",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class YeastarService:
    """Read-only business interface independent of Yeastar field names."""

    def __init__(self, client: ReadClient, *, allow_raw_numbers: bool = False) -> None:
        self.client = client
        self.allow_raw_numbers = allow_raw_numbers
        self._pbx_info: PBXInfo | None = None

    def ensure_raw_numbers_allowed(self, include_numbers: bool) -> None:
        if include_numbers and not self.allow_raw_numbers:
            raise PermissionError(
                "raw telephone numbers are disabled; set YEASTAR_ALLOW_RAW_NUMBERS=true "
                "only for an explicitly approved reporting environment"
            )

    async def get_pbx_info(self) -> PBXInfo:
        payload = await self.client.get(PBX_INFO.path, api_version=PBX_INFO.version)
        raw = payload.get("data", {})
        info = PBXInfo(
            name=str(raw.get("device_name", "PBX")),
            model=str(raw.get("model_name", "Unknown")),
            firmware_version=str(raw.get("firmware_version", "Unknown")),
            system_time=raw.get("system_time"),
            uptime_seconds=raw.get("up_time"),
            date_format=raw.get("system_date_format"),
            time_format=raw.get("system_time_format"),
        )
        self._pbx_info = info
        return info

    async def _list_queue_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        total: int | None = None
        seen_ids: set[str] = set()
        while total is None or len(rows) < total:
            payload = await self.client.get(
                QUEUES.path,
                api_version=QUEUES.version,
                params={"page": page, "page_size": 100, "sort_by": "number", "order_by": "asc"},
            )
            batch = payload.get("queue_list") or []
            total = self._validate_directory_page(
                payload,
                batch,
                expected_total=total,
                received=len(rows),
                label="queue directory",
                maximum=1000,
                seen_ids=seen_ids,
            )
            rows.extend(batch)
            page += 1
        return rows

    async def list_queues(self) -> list[Queue]:
        result: list[Queue] = []
        for raw in await self._list_queue_rows():
            agent_ids = {
                str(item.get("value"))
                for key in ("static_agent_list", "dynamic_agent_list")
                for item in (raw.get(key) or [])
            }
            result.append(
                Queue(
                    id=int(raw["id"]),
                    number=str(raw["number"]),
                    name=str(raw["name"]),
                    ring_strategy=raw.get("ring_strategy"),
                    agent_count=len(agent_ids),
                )
            )
        return result

    async def list_agents(self) -> list[Agent]:
        agents: dict[str, dict[str, Any]] = {}
        for queue in await self._list_queue_rows():
            queue_ref = QueueRef(
                id=int(queue["id"]), number=str(queue["number"]), name=str(queue["name"])
            )
            for kind, key in (("static", "static_agent_list"), ("dynamic", "dynamic_agent_list")):
                for raw in queue.get(key) or []:
                    agent_id = str(raw["value"])
                    entry = agents.setdefault(
                        agent_id,
                        {
                            "id": agent_id,
                            "number": str(raw.get("text2", "")),
                            "name": str(raw.get("text", raw.get("text2", "Unknown"))),
                            "kinds": set(),
                            "queues": [],
                        },
                    )
                    entry["kinds"].add(kind)
                    if queue_ref not in entry["queues"]:
                        entry["queues"].append(queue_ref)
        return [
            Agent(
                id=entry["id"],
                number=entry["number"],
                name=entry["name"],
                membership_type=(
                    "mixed" if len(entry["kinds"]) > 1 else next(iter(entry["kinds"]))
                ),
                queue_memberships=entry["queues"],
            )
            for entry in sorted(agents.values(), key=lambda item: item["number"])
        ]

    async def list_ivrs(self) -> list[IVR]:
        """List minimal IVR identifiers required by aggregate reports."""
        items: list[IVR] = []
        page = 1
        total: int | None = None
        seen_ids: set[str] = set()
        while total is None or len(items) < total:
            payload = await self.client.get(
                "ivr/list",
                api_version="v1.0",
                params={"page": page, "page_size": 100, "sort_by": "number", "order_by": "asc"},
            )
            rows = payload.get("data") or []
            total = self._validate_directory_page(
                payload,
                rows,
                expected_total=total,
                received=len(items),
                label="IVR directory",
                maximum=1000,
                seen_ids=seen_ids,
            )
            items.extend(
                IVR(id=int(row["id"]), number=str(row["number"]), name=str(row["name"]))
                for row in rows
            )
            if not rows:
                break
            page += 1
        return items

    async def list_extensions(self) -> list[Extension]:
        """List the minimal internal extension directory for named collaborator reports."""
        items: list[Extension] = []
        page = 1
        total: int | None = None
        seen_ids: set[str] = set()
        while total is None or len(items) < total:
            payload = await self.client.get(
                "extension/list",
                api_version="v1.0",
                params={"page": page, "page_size": 100, "sort_by": "number", "order_by": "asc"},
            )
            rows = payload.get("data") or []
            total = self._validate_directory_page(
                payload,
                rows,
                expected_total=total,
                received=len(items),
                label="extension directory",
                maximum=5000,
                seen_ids=seen_ids,
            )
            for row in rows:
                name = " ".join(
                    part
                    for part in (str(row.get("first_name") or ""), str(row.get("last_name") or ""))
                    if part
                )
                items.append(
                    Extension(
                        id=int(row["id"]),
                        number=str(row["number"]),
                        name=name or str(row.get("caller_id_name") or row["number"]),
                    )
                )
            if not rows:
                break
            page += 1
        return items

    async def list_ring_groups(self) -> list[RingGroup]:
        """List minimal ring-group identifiers required by reports."""
        items: list[RingGroup] = []
        page = 1
        total: int | None = None
        seen_ids: set[str] = set()
        while total is None or len(items) < total:
            payload = await self.client.get(
                "ringgroup/list",
                api_version="v1.0",
                params={"page": page, "page_size": 100, "sort_by": "number", "order_by": "asc"},
            )
            rows = payload.get("data") or []
            total = self._validate_directory_page(
                payload,
                rows,
                expected_total=total,
                received=len(items),
                label="ring-group directory",
                maximum=1000,
                seen_ids=seen_ids,
            )
            items.extend(
                RingGroup(id=int(row["id"]), number=str(row["number"]), name=str(row["name"]))
                for row in rows
            )
            if not rows:
                break
            page += 1
        return items

    async def get_queue_performance(
        self,
        *,
        period: Period,
        queue_ids: list[int] | None = None,
        api_version: str = "v2.0",
        abandon_time_seconds: int | None = None,
    ) -> list[QueuePerformance]:
        effective_queue_ids = queue_ids
        if not effective_queue_ids:
            effective_queue_ids = [queue.id for queue in await self.list_queues()]
        if not effective_queue_ids:
            return []
        params = await self._period_params(period)
        params.update(
            {
                "type": "queueperformance",
                "queue_id_list": ",".join(map(str, effective_queue_ids)),
                "abandon_time": abandon_time_seconds,
            }
        )
        payload = await self.client.get("call_report/list", api_version=api_version, params=params)
        rows = self._require_complete_report_rows(
            payload, "queue_performance_list", "queue performance report"
        )
        return [self._normalize_queue_performance(raw, period) for raw in rows]

    async def get_calls(
        self,
        *,
        period: Period,
        page: int = 1,
        page_size: int = 100,
        queue_ids: list[int] | None = None,
        status: str | None = None,
        call_from: str | None = None,
        call_to: str | None = None,
        routing_duration: str | None = None,
        segments: str | None = None,
        disconnected_by: str | None = None,
        include_numbers: bool = False,
        api_version: str = "v2.0",
    ) -> CallPage:
        if page < 1 or not 1 <= page_size <= 1000:
            raise ValueError("page must be >= 1 and page_size must be between 1 and 1000")
        if api_version == "v1.0" and queue_ids:
            raise ValueError("queue filters are not supported by CDR v1")
        if api_version == "v1.0" and any(
            value is not None for value in (routing_duration, segments, disconnected_by)
        ):
            raise ValueError("routing, segment, and disconnect filters require CDR v2")
        params: dict[str, Any] = await self._cdr_period_params(period, api_version)
        params.update({"page": page, "page_size": page_size})
        if api_version == "v2.0":
            params.update(
                {
                    "order_by": "desc",
                    "sort_by": "time",
                    "queue_list": ",".join(map(str, queue_ids or [])) or None,
                    "last_status": status,
                    "call_from": call_from,
                    "call_to": call_to,
                    "routing_duration": routing_duration,
                    "segments": segments,
                    "disconnected_by": disconnected_by,
                }
            )
        else:
            params.update({"status": status, "call_from": call_from, "call_to": call_to})
        payload = await self.client.get("cdr/search", api_version=api_version, params=params)
        if "total_number" not in payload:
            raise ValueError("CDR search response is missing total_number")
        raw_rows = payload.get("data") or []
        total_available = int(payload["total_number"])
        if total_available < 0:
            raise ValueError("CDR search returned a negative total_number")
        unique_rows: list[dict[str, Any]] = []
        page_ids: set[str] = set()
        for raw in raw_rows:
            call_id = str(raw.get("uid") or raw.get("new_id") or raw.get("id"))
            if call_id in page_ids:
                continue
            page_ids.add(call_id)
            unique_rows.append(raw)
        if len(unique_rows) > total_available:
            raise ValueError("CDR search returned more rows than its declared total")
        calls = [self._normalize_call(raw, include_numbers, self._pbx_info) for raw in unique_rows]
        return CallPage(
            period=period,
            calls=calls,
            total_available=total_available,
            page=page,
            page_size=page_size,
            numbers_masked=not include_numbers,
        )

    async def get_extension_performance(
        self,
        *,
        period: Period,
        extension_ids: list[int],
        communication_type: Literal["Inbound", "Outbound", "Internal"] | None = None,
        api_version: str = "v2.0",
    ) -> list[ExtensionPerformance]:
        """Return one named statistics row per extension instead of summing collaborators."""
        if not 1 <= len(extension_ids) <= 50:
            raise ValueError("extension_ids must contain between 1 and 50 IDs")
        params: dict[str, Any] = await self._period_params(period)
        requested = set(extension_ids)
        directory = {
            extension.number: extension
            for extension in await self.list_extensions()
            if extension.id in requested
        }
        if len(directory) != len(requested):
            raise ValueError("one or more extension_ids were not found in the extension directory")
        params: dict[str, Any] = await self._period_params(period)
        params.update(
            {
                "type": "extcallstatistics",
                "ext_id_list": ",".join(map(str, extension_ids)),
                "communication_type": communication_type,
            }
        )
        payload = await self.client.get("call_report/list", api_version=api_version, params=params)
        rows = self._require_complete_report_rows(
            payload, "ext_call_statistics_list", "extension call statistics report"
        )
        result: list[ExtensionPerformance] = []
        seen_numbers: set[str] = set()
        for raw in rows:
            extension_number = str(raw.get("ext_num") or "")
            extension = directory.get(extension_number)
            if extension is None:
                raise ValueError("Yeastar extension report row did not match a requested ext_num")
            if extension_number in seen_numbers:
                raise ValueError("Yeastar extension report returned duplicate ext_num rows")
            seen_numbers.add(extension_number)
            calls = CallCounts(
                total=int(raw.get("total_call_count", 0) or 0),
                answered=int(raw.get("answered_calls", 0) or 0),
                missed=int(raw.get("no_answer_calls", 0) or 0),
                abandoned=int(raw.get("abandoned_calls", 0) or 0),
                busy=int(raw.get("busy_calls", 0) or 0),
                failed=int(raw.get("failed_calls", 0) or 0),
                voicemail=int(raw.get("voicemail_calls", 0) or 0),
            )
            talking = int(raw.get("total_talking_time", 0) or 0)
            result.append(
                ExtensionPerformance(
                    extension=extension,
                    period=period,
                    communication_type=communication_type,
                    calls=calls,
                    total_holding_seconds=int(raw.get("total_holding_time", 0) or 0),
                    total_talking_seconds=talking,
                    average_talking_seconds=(talking / calls.answered if calls.answered else 0),
                )
            )
        missing_numbers = set(directory) - seen_numbers
        if missing_numbers:
            raise ValueError(
                "Yeastar extension report is missing requested ext_num rows: "
                + ", ".join(sorted(missing_numbers))
            )
        return result

    async def get_call_stats(
        self,
        *,
        period: Period,
        queue_ids: list[int] | None = None,
        extension_ids: list[int] | None = None,
        api_version: str = "v2.0",
    ) -> CallStats:
        if queue_ids and extension_ids:
            raise ValueError("queue_ids and extension_ids cannot be combined")
        if extension_ids:
            performance = await self.get_extension_performance(
                period=period,
                extension_ids=extension_ids,
                api_version=api_version,
            )
            counts = CallCounts(
                total=sum(row.calls.total for row in performance),
                answered=sum(row.calls.answered for row in performance),
                missed=sum(row.calls.missed for row in performance),
                abandoned=sum(row.calls.abandoned for row in performance),
                busy=sum(row.calls.busy for row in performance),
                failed=sum(row.calls.failed for row in performance),
                voicemail=sum(row.calls.voicemail for row in performance),
            )
            total_talk = sum(row.total_talking_seconds for row in performance)
            return CallStats(
                period=period,
                calls=counts,
                total_talking_seconds=total_talk,
                average_duration_seconds=(total_talk / counts.answered if counts.answered else 0),
                source=f"Yeastar Extension Call Statistics report ({api_version})",
            )
        counts = CallCounts()
        total_duration = 0
        total_handling = 0
        total_talking = 0
        current_page = 1
        processed = 0
        total_available: int | None = None
        seen_ids: set[str] = set()
        while total_available is None or processed < total_available:
            result_page = await self.get_calls(
                period=period,
                page=current_page,
                page_size=1000,
                queue_ids=queue_ids,
                include_numbers=False,
                api_version=api_version,
            )
            total_available = self._validate_call_page(
                result_page,
                expected_total=total_available,
                processed=processed,
                seen_ids=seen_ids,
            )
            if total_available > MAX_CDR_RECORDS:
                raise ValueError(
                    f"call statistics would process {total_available} CDRs; "
                    f"use a narrower period (limit {MAX_CDR_RECORDS})"
                )
            for call in result_page.calls:
                status = call.status.upper().replace(" ", "_")
                if status == "ANSWERED":
                    counts.answered += 1
                elif status in {"NO_ANSWER", "MISSED"}:
                    counts.missed += 1
                elif status == "ABANDONED":
                    counts.abandoned += 1
                elif status == "BUSY":
                    counts.busy += 1
                elif status == "FAILED":
                    counts.failed += 1
                elif status == "VOICEMAIL":
                    counts.voicemail += 1
                total_duration += call.duration_seconds
                total_handling += call.handling_seconds
                total_talking += call.talking_seconds
            processed += len(result_page.calls)
            current_page += 1
        counts.total = processed
        return CallStats(
            period=period,
            calls=counts,
            total_duration_seconds=total_duration,
            total_handling_seconds=total_handling,
            total_talking_seconds=total_talking,
            average_duration_seconds=(total_duration / counts.total if counts.total else 0),
            source=f"local aggregation of {processed} CDR records ({api_version})",
        )

    async def get_call_activity(
        self,
        *,
        period: Period,
        bucket: Literal["hour", "day", "week", "month"] = "day",
        queue_ids: list[int] | None = None,
        api_version: str = "v2.0",
        max_records: int = 100_000,
        duration_band_seconds: int = 120,
        duration_cap_seconds: int = 600,
    ) -> CallActivity:
        """Aggregate CDR locally into privacy-safe dashboard time buckets."""
        if not 1 <= max_records <= MAX_CDR_RECORDS:
            raise ValueError(f"max_records must be between 1 and {MAX_CDR_RECORDS}")
        if duration_band_seconds < 1 or duration_cap_seconds < duration_band_seconds:
            raise ValueError("duration bands require 1 <= band seconds <= cap seconds")
        grouped: dict[datetime, dict[str, Any]] = {}
        duration_bands: dict[str, int] = {}
        total_duration = 0
        processed = 0
        page = 1
        total_available: int | None = None
        seen_ids: set[str] = set()
        while total_available is None or processed < total_available:
            result = await self.get_calls(
                period=period,
                page=page,
                page_size=1000,
                queue_ids=queue_ids,
                include_numbers=False,
                api_version=api_version,
            )
            total_available = self._validate_call_page(
                result,
                expected_total=total_available,
                processed=processed,
                seen_ids=seen_ids,
            )
            if total_available > max_records:
                raise ValueError(
                    f"aggregation would process {total_available} CDRs; use a narrower period "
                    f"or lower the requested range (limit {max_records})"
                )
            for call in result.calls:
                key = self._activity_bucket_start(call.started_at, bucket)
                item = grouped.setdefault(
                    key,
                    {
                        "counts": CallCounts(),
                        "directions": {},
                        "bands": {},
                        "duration": 0,
                        "handling": 0,
                        "talking": 0,
                    },
                )
                counts: CallCounts = item["counts"]
                self._increment_status(counts, call.status)
                counts.total += 1
                direction = call.direction or "Unknown"
                item["directions"][direction] = item["directions"].get(direction, 0) + 1
                band = self._duration_band(
                    call.duration_seconds, duration_band_seconds, duration_cap_seconds
                )
                item["bands"][band] = item["bands"].get(band, 0) + 1
                duration_bands[band] = duration_bands.get(band, 0) + 1
                item["duration"] += call.duration_seconds
                total_duration += call.duration_seconds
                item["handling"] += call.handling_seconds
                item["talking"] += call.talking_seconds
            processed += len(result.calls)
            page += 1
        buckets = []
        for start, item in sorted(grouped.items()):
            counts = item["counts"]
            buckets.append(
                CallActivityBucket(
                    start=start,
                    calls=counts,
                    directions=item["directions"],
                    duration_bands=item["bands"],
                    total_duration_seconds=item["duration"],
                    total_handling_seconds=item["handling"],
                    total_talking_seconds=item["talking"],
                    average_duration_seconds=(
                        item["duration"] / counts.total if counts.total else 0
                    ),
                )
            )
        return CallActivity(
            period=period,
            bucket=bucket,
            buckets=buckets,
            duration_band_seconds=duration_band_seconds,
            duration_cap_seconds=duration_cap_seconds,
            duration_bands=dict(sorted(duration_bands.items())),
            average_duration_seconds=(total_duration / processed if processed else 0),
            total_processed=processed,
            source=f"local aggregation of {processed} CDR records ({api_version})",
        )

    @staticmethod
    def _duration_band(duration: int, width: int, cap: int) -> str:
        if duration >= cap:
            return f"{cap}+"
        start = (duration // width) * width
        return f"{start}-{start + width - 1}"

    @staticmethod
    def _activity_bucket_start(
        value: datetime, bucket: Literal["hour", "day", "week", "month"]
    ) -> datetime:
        if bucket == "hour":
            return value.replace(minute=0, second=0, microsecond=0)
        if bucket == "day":
            return value.replace(hour=0, minute=0, second=0, microsecond=0)
        if bucket == "week":
            day = value.replace(hour=0, minute=0, second=0, microsecond=0)
            return day - timedelta(days=day.weekday())
        return value.replace(month=value.month, day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _increment_status(counts: CallCounts, status: str) -> None:
        normalized = status.upper().replace(" ", "_")
        if normalized == "ANSWERED":
            counts.answered += 1
        elif normalized in {"NO_ANSWER", "MISSED"}:
            counts.missed += 1
        elif normalized == "ABANDONED":
            counts.abandoned += 1
        elif normalized == "BUSY":
            counts.busy += 1
        elif normalized == "FAILED":
            counts.failed += 1
        elif normalized == "VOICEMAIL":
            counts.voicemail += 1

    async def get_routing_analysis(
        self,
        *,
        period: Period,
        queue_ids: list[int] | None = None,
        min_segments: int = 3,
        long_routing_seconds: int = 45,
        max_examples: int = 50,
        include_numbers: bool = False,
        api_version: str = "v2.0",
        max_records: int = 100_000,
    ) -> RoutingAnalysis:
        """Detect documented CDR routing anomalies without returning the full CDR set."""
        if api_version != "v2.0":
            raise ValueError("routing analysis requires CDR v2")
        if min_segments < 2:
            raise ValueError("min_segments must be >= 2")
        if min(long_routing_seconds, max_examples, max_records) < 0:
            raise ValueError("thresholds and limits must not be negative")
        if max_examples > 100:
            raise ValueError("max_examples must be <= 100")
        if not 1 <= max_records <= MAX_CDR_RECORDS:
            raise ValueError(f"max_records must be between 1 and {MAX_CDR_RECORDS}")
        counts = RoutingCounts()
        by_queue: dict[str, int] = {}
        by_ivr: dict[str, int] = {}
        by_ring_group: dict[str, int] = {}
        examples: list[RoutingAnomaly] = []
        processed = 0
        page = 1
        total_available: int | None = None
        seen_ids: set[str] = set()
        while total_available is None or processed < total_available:
            result = await self.get_calls(
                period=period,
                page=page,
                page_size=1000,
                queue_ids=queue_ids,
                include_numbers=True,
                api_version=api_version,
            )
            total_available = self._validate_call_page(
                result,
                expected_total=total_available,
                processed=processed,
                seen_ids=seen_ids,
            )
            if total_available > max_records:
                raise ValueError(
                    f"routing analysis would process {total_available} CDRs; use a narrower "
                    f"period (limit {max_records})"
                )
            for call in result.calls:
                reasons: list[str] = []
                counts.total += 1
                if call.segments > 1:
                    counts.multi_segment += 1
                if call.segments >= min_segments:
                    counts.at_or_above_min_segments += 1
                    reasons.append(f"segments>={min_segments}")
                if self._has_repeated_destination(call):
                    counts.loop_candidates += 1
                    reasons.append("repeated_destination")
                if call.routing_seconds >= long_routing_seconds:
                    counts.long_routing += 1
                    reasons.append(f"routing>={long_routing_seconds}s")
                if (
                    call.second_participant
                    and call.last_participant
                    and (
                        call.second_participant.name,
                        call.second_participant.number,
                    )
                    != (call.last_participant.name, call.last_participant.number)
                ):
                    counts.route_changes += 1
                    reasons.append("second_and_last_participant_differ")
                status = call.status.upper().replace(" ", "_")
                if status in {"NO_ANSWER", "MISSED"}:
                    counts.no_answer += 1
                elif status == "ABANDONED":
                    counts.abandoned += 1
                elif status == "BUSY":
                    counts.busy += 1
                elif status == "FAILED":
                    counts.failed += 1
                elif status == "VOICEMAIL":
                    counts.voicemail += 1
                for destination in call.queues:
                    self._increment_named(
                        by_queue, destination.name or destination.number or "Unknown"
                    )
                for destination in call.ivrs:
                    self._increment_named(
                        by_ivr, destination.name or destination.number or "Unknown"
                    )
                for destination in call.ring_groups:
                    self._increment_named(
                        by_ring_group, destination.name or destination.number or "Unknown"
                    )
                if reasons and len(examples) < max_examples:
                    examples.append(
                        RoutingAnomaly(
                            call=call if include_numbers else self._mask_call_record(call),
                            reasons=reasons,
                        )
                    )
            processed += len(result.calls)
            page += 1
        return RoutingAnalysis(
            period=period,
            counts=counts,
            by_queue=dict(sorted(by_queue.items())),
            by_ivr=dict(sorted(by_ivr.items())),
            by_ring_group=dict(sorted(by_ring_group.items())),
            examples=examples,
            total_processed=processed,
            numbers_masked=not include_numbers,
            source=f"local aggregation of {processed} CDR v2 records",
            caveat=(
                "Multi-segment calls and participant changes are routing indicators, not proof of "
                "blind versus attended transfer; Yeastar CDR search does not document that label."
            ),
        )

    @classmethod
    def _mask_call_record(cls, call: CallRecord) -> CallRecord:
        data = call.model_dump()
        direction = call.direction.casefold()

        def masked_party(party: Party | None, *, external: bool) -> dict[str, Any] | None:
            if party is None:
                return None
            digits = re.sub(r"\D", "", party.number or "")
            return {
                "name": None if external or len(digits) > 6 else party.name,
                "number": cls._mask_number(party.number),
            }

        data["caller"] = masked_party(call.caller, external=direction == "inbound")
        data["callee"] = masked_party(call.callee, external=direction == "outbound")
        routed_external = direction == "outbound"
        data["second_participant"] = masked_party(call.second_participant, external=routed_external)
        data["last_participant"] = masked_party(call.last_participant, external=routed_external)
        for field in (
            "queues",
            "ivrs",
            "ring_groups",
            "call_flows",
            "dids",
            "outbound_caller_ids",
        ):
            data[field] = [
                {
                    "name": (None if len(re.sub(r"\D", "", item.number or "")) > 6 else item.name),
                    "number": cls._mask_number(item.number),
                    "status": item.status,
                }
                for item in getattr(call, field)
            ]
        return CallRecord.model_validate(data)

    @staticmethod
    def _has_repeated_destination(call: CallRecord) -> bool:
        seen: set[tuple[str | None, str | None]] = set()
        for destination in call.queues + call.ivrs + call.ring_groups + call.call_flows:
            key = (destination.name, destination.number)
            if key in seen:
                return True
            seen.add(key)
        return False

    @staticmethod
    def _validate_call_page(
        result: CallPage,
        *,
        expected_total: int | None,
        processed: int,
        seen_ids: set[str],
    ) -> int:
        total = result.total_available
        if expected_total is not None and total != expected_total:
            raise ValueError(f"CDR pagination total changed from {expected_total} to {total}")
        if not result.calls and processed < total:
            raise ValueError("CDR pagination returned a premature empty page")
        for call in result.calls:
            if call.id in seen_ids:
                raise ValueError(f"CDR pagination returned duplicate CDR ID {call.id}")
            seen_ids.add(call.id)
        if processed + len(result.calls) > total:
            raise ValueError("CDR pagination returned more rows than its declared total")
        return total

    @staticmethod
    def _validate_directory_page(
        payload: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        expected_total: int | None,
        received: int,
        label: str,
        maximum: int,
        seen_ids: set[str],
    ) -> int:
        if "total_number" not in payload:
            raise ValueError(f"{label} response is missing total_number")
        total = int(payload["total_number"])
        if total > maximum:
            raise ValueError(f"{label} exceeds the {maximum}-entry safety limit")
        if expected_total is not None and total != expected_total:
            raise ValueError(f"{label} pagination total changed from {expected_total} to {total}")
        if not rows and received < total:
            raise ValueError(f"{label} pagination returned a premature empty page")
        if received + len(rows) > total:
            raise ValueError(f"{label} pagination returned more rows than its declared total")
        for row in rows:
            if row.get("id") is None:
                raise ValueError(f"{label} row is missing a stable row ID")
            row_id = str(row["id"])
            if row_id in seen_ids:
                raise ValueError(f"{label} pagination returned duplicate stable row ID {row_id}")
            seen_ids.add(row_id)
        return total

    @staticmethod
    def _require_complete_report_rows(
        payload: dict[str, Any], row_key: str, label: str
    ) -> list[dict[str, Any]]:
        if "total_number" not in payload:
            raise ValueError(f"{label} response is missing total_number")
        rows = payload.get(row_key) or []
        total = int(payload["total_number"])
        if len(rows) != total:
            raise ValueError(
                f"{label} declared total {total} but supplied {len(rows)} non-paged rows"
            )
        return rows

    @staticmethod
    def _increment_named(target: dict[str, int], key: str) -> None:
        target[key] = target.get(key, 0) + 1

    async def get_ivr_analysis(
        self,
        *,
        period: Period,
        ivr_ids: list[int],
        api_version: str = "v2.0",
        include_calls: bool = False,
        include_numbers: bool = False,
        max_call_examples: int = 50,
        keys: list[str] | None = None,
        first_keypress_only: bool = False,
    ) -> list[IVRAnalysis]:
        """Return IVR keypress-to-destination statistics and optional bounded calls."""
        if not ivr_ids:
            raise ValueError("ivr_ids must not be empty")
        if not 0 <= max_call_examples <= 100:
            raise ValueError("max_call_examples must be between 0 and 100")
        period_params = await self._period_params(period)
        payload = await self.client.get(
            "call_report/list",
            api_version=api_version,
            params={
                **period_params,
                "type": "ivr",
                "ivr_id_list": ",".join(map(str, ivr_ids)),
            },
        )
        ivr_rows = self._require_complete_report_rows(payload, "ivr_list", "IVR report")
        analyses: list[IVRAnalysis] = []
        for raw_ivr in ivr_rows:
            ivr_number = str(raw_ivr.get("ivr_num", ""))
            detail = await self.client.get(
                "call_report/detail",
                api_version=api_version,
                params={
                    **period_params,
                    "detail_time_begin": period_params["start_time"],
                    "detail_time_end": period_params["end_time"],
                    "type": "ivr",
                    "ivr_num": ivr_number,
                },
            )
            if "total_number" in detail and int(detail["total_number"]) > 10_000:
                raise ValueError("IVR detail exceeds the 10000-row safety limit")
            rows = self._require_complete_report_rows(detail, "ivr_report_detail", "IVR detail")

            selected_rows: list[dict[str, Any]] = []
            seen_call_ids: set[str] = set()
            selected_keys = set(keys or [])
            chronologically = sorted(
                rows, key=lambda row: self._parse_time(row.get("time"), self._pbx_info)
            )
            for row in chronologically:
                call_id = str(row.get("id", ""))
                if first_keypress_only:
                    if call_id in seen_call_ids:
                        continue
                    seen_call_ids.add(call_id)
                key = str(row.get("press", "unknown"))
                if selected_keys and key not in selected_keys:
                    continue
                selected_rows.append(row)

            if selected_keys or first_keypress_only:
                press_counts: dict[str, int] = {}
                for row in selected_rows:
                    key = str(row.get("press", "unknown"))
                    press_counts[key] = press_counts.get(key, 0) + 1
            else:
                press_counts = {
                    str(key).removeprefix("press_"): int(value)
                    for key, value in (raw_ivr.get("press_count") or {}).items()
                }
            grouped: dict[tuple[str, str | None, str | None, str | None], int] = {}
            call_examples: list[IVRCall] = []
            unique_ids: set[str] = set()
            for row in selected_rows:
                call_id = str(row.get("id", ""))
                unique_ids.add(call_id)
                key = str(row.get("press", "unknown"))
                destination_name, destination_number = self._split_party_label(row.get("call_to"))
                if not destination_name and not destination_number:
                    destination_name = str(row.get("destination") or "Unknown")
                destination_type = (
                    str(row["destination_type"]) if row.get("destination_type") else None
                )
                if not include_numbers and (
                    (destination_type or "").casefold() == "external number"
                    or len(re.sub(r"\D", "", destination_number or "")) > 6
                ):
                    destination_name = None
                group_key = (key, destination_name, destination_number, destination_type)
                grouped[group_key] = grouped.get(group_key, 0) + 1
                if include_calls and len(call_examples) < max_call_examples:
                    caller_name, caller_number = self._split_party_label(row.get("call_from"))
                    call_examples.append(
                        IVRCall(
                            id=call_id,
                            started_at=self._parse_time(row.get("time"), self._pbx_info),
                            caller=Party(
                                name=caller_name if include_numbers else None,
                                number=(
                                    caller_number
                                    if include_numbers
                                    else self._mask_number(caller_number)
                                ),
                            ),
                            key=key,
                            destination=RoutedDestination(
                                name=destination_name,
                                number=(
                                    destination_number
                                    if include_numbers
                                    else self._mask_number(destination_number)
                                ),
                                status=None,
                            ),
                            operation_seconds=int(row.get("opr_duration", 0) or 0),
                        )
                    )
            destinations = [
                IVRDestinationStat(
                    key=key,
                    destination=RoutedDestination(
                        name=name,
                        number=number if include_numbers else self._mask_number(number),
                    ),
                    destination_type=destination_type,
                    calls=count,
                )
                for (key, name, number, destination_type), count in grouped.items()
            ]
            destinations.sort(key=lambda item: (-item.calls, item.key, item.destination.name or ""))
            analyses.append(
                IVRAnalysis(
                    ivr=RoutedDestination(
                        name=str(raw_ivr.get("ivr_name", "Unknown")), number=ivr_number
                    ),
                    period=period,
                    unique_calls=len(unique_ids),
                    total_keypresses=sum(press_counts.values()),
                    press_counts=press_counts,
                    destinations=destinations,
                    calls=call_examples,
                    total_call_details=len(rows),
                    numbers_masked=not include_numbers,
                )
            )
        return analyses

    async def get_capabilities(self) -> Capabilities:
        info = self._pbx_info or await self.get_pbx_info()
        reports_v2 = self._supports_v2_call_reports(info.firmware_version, info.model)
        cdr_v2 = self._supports_v2_cdr(info.firmware_version, info.model)
        specs = [
            ("pbx_info", True, "v1.0", "system/information", None, "probed"),
            (
                "queues",
                True,
                "v1.0",
                "queue/list",
                "Documented endpoint; not probed by this call",
                "documented",
            ),
            (
                "call_reports_legacy",
                True,
                "v1.0",
                "call_report/list",
                "Documented for historical pre-upgrade data; dataset presence is not probed",
                "documented",
            ),
            (
                "call_reports_current",
                reports_v2,
                "v2.0",
                "call_report/list",
                "Selected from the documented firmware data boundary",
                "firmware",
            ),
            (
                "cdr_current",
                cdr_v2,
                "v2.0",
                "cdr/search",
                "Availability inferred from model-specific minimum firmware",
                "firmware",
            ),
            (
                "cdr_details",
                cdr_v2,
                "v2.0",
                "cdr/detail",
                "Availability inferred from model-specific minimum firmware",
                "firmware",
            ),
        ]
        return Capabilities(
            firmware_version=info.firmware_version,
            capabilities=[
                Capability(
                    name=name,
                    available=available,
                    api_version=version,
                    endpoint=endpoint,
                    note=note,
                    basis=basis,
                )
                for name, available, version, endpoint, note, basis in specs
            ],
        )

    async def get_queue_wait_times(
        self,
        *,
        period: Period,
        queue_ids: list[int] | None = None,
        api_version: str = "v2.0",
    ) -> list[QueueWaitTimes]:
        effective_queue_ids = queue_ids
        if not effective_queue_ids:
            effective_queue_ids = [queue.id for queue in await self.list_queues()]
        if not effective_queue_ids:
            return []
        info = self._pbx_info or await self.get_pbx_info()
        report_time = self._report_bucket(period, info)
        payload = await self.client.get(
            "call_report/list",
            api_version=api_version,
            params={
                "type": "queueavgwaittalktime",
                "time": report_time,
                "queue_id_list": ",".join(map(str, effective_queue_ids)),
            },
        )
        rows = self._require_complete_report_rows(
            payload, "queue_avg_wait_talk_time_list", "queue wait-time report"
        )
        buckets = [
            WaitTimeBucket(
                start=self._queue_bucket_start(period, int(raw["time"])),
                calls=int(raw.get("total_calls", 0)),
                answered=int(raw.get("answered_calls", 0)),
                average_wait_seconds=int(raw.get("avg_wait_time", 0)),
                all_calls_average_wait_seconds=int(raw.get("all_call_avg_wait_time", 0)),
                average_talk_seconds=int(raw.get("avg_talk_time", 0)),
            )
            for raw in rows
        ]
        queue_name = (
            f"queue {effective_queue_ids[0]}"
            if len(effective_queue_ids) == 1
            else "selected queues"
        )
        return [
            QueueWaitTimes(
                queue=QueueRef(
                    id=effective_queue_ids[0] if len(effective_queue_ids) == 1 else None,
                    name=queue_name,
                ),
                period=period,
                buckets=buckets,
            )
        ]

    async def get_agent_performance(
        self,
        *,
        period: Period,
        queue_id: int,
        agent_ids: list[int] | None = None,
        api_version: str = "v2.0",
        abandon_time_seconds: int | None = None,
    ) -> list[AgentPerformance]:
        params: dict[str, Any] = await self._period_params(period)
        params.update(
            {
                "type": "queueagentperformance",
                "queue_id": queue_id,
                "agent_id_list": ",".join(map(str, agent_ids or [])) or None,
                "abandon_time": abandon_time_seconds,
            }
        )
        payload = await self.client.get("call_report/list", api_version=api_version, params=params)
        queue_rows = self._require_complete_report_rows(
            payload, "queue_agent_performance_list", "queue agent performance report"
        )
        results: list[AgentPerformance] = []
        for queue_raw in queue_rows:
            queue = QueueRef(
                id=queue_raw.get("queue_id", queue_id),
                number=queue_raw.get("queue_num"),
                name=str(queue_raw.get("queue", "Unknown")),
            )
            for raw in queue_raw.get("detail") or []:
                agent = Agent(
                    id=str(raw.get("agent_id", raw.get("agent_number", "unknown"))),
                    number=str(raw.get("agent_number", "")),
                    name=str(raw.get("agent_name", "Unknown")),
                    membership_type="unknown",
                    queue_memberships=[queue],
                )
                results.append(
                    AgentPerformance(
                        agent=agent,
                        queue=queue,
                        period=period,
                        calls=CallCounts(
                            total=int(raw.get("total_calls", 0)),
                            answered=int(raw.get("answered_calls", 0)),
                            missed=int(raw.get("missed_calls", 0)),
                        ),
                        waiting=WaitingMetrics(
                            average_seconds=int(raw.get("average_waiting_time", 0)),
                            maximum_seconds=int(raw.get("max_waiting_time", 0)),
                        ),
                        talking=TalkingMetrics(
                            average_seconds=int(raw.get("average_talking_time", 0)),
                            total_seconds=int(raw.get("total_talking_time", 0)),
                        ),
                        missed_rate_percent=raw.get("missed_call_rate"),
                    )
                )
        return results

    async def get_contact_call_stats(
        self,
        *,
        period: Period,
        phonebook_ids: list[int] | None = None,
        include_numbers: bool = False,
        api_version: str = "v2.0",
        max_records: int = 100_000,
    ) -> ContactCallReport:
        """Cross company contacts with CDR locally; never return the scanned raw CDR rows."""
        if api_version != "v2.0":
            raise ValueError("contact IVR classification requires CDR v2")
        if not 1 <= max_records <= MAX_CDR_RECORDS:
            raise ValueError(f"max_records must be between 1 and {MAX_CDR_RECORDS}")
        contacts_raw: list[dict[str, Any]] = []
        contact_page = 1
        contact_total: int | None = None
        seen_contact_ids: set[str] = set()
        while contact_total is None or len(contacts_raw) < contact_total:
            payload = await self.client.get(
                "company_contact/list",
                api_version="v1.0",
                params={
                    "page": contact_page,
                    "page_size": 10000,
                    "sort_by": "id",
                    "order_by": "asc",
                },
            )
            batch = payload.get("data") or []
            contact_total = self._validate_directory_page(
                payload,
                batch,
                expected_total=contact_total,
                received=len(contacts_raw),
                label="company contact directory",
                maximum=50_000,
                seen_ids=seen_contact_ids,
            )
            contacts_raw.extend(batch)
            contact_page += 1
        number_fields = (
            "business",
            "business2",
            "mobile",
            "mobile2",
            "home",
            "home2",
            "business_fax",
            "home_fax",
            "other",
        )
        contacts: dict[int, dict[str, Any]] = {}
        number_owners: dict[str, list[int]] = {}
        for raw in contacts_raw:
            books = raw.get("phonebook_list") or []
            if phonebook_ids and not any(
                int(book.get("id", -1)) in phonebook_ids for book in books
            ):
                continue
            contact_id = int(raw["id"])
            numbers = list(
                dict.fromkeys(str(raw[field]) for field in number_fields if raw.get(field))
            )
            contacts[contact_id] = {
                "ref": ContactRef(
                    id=contact_id,
                    name=(
                        str(raw.get("contact_name", "Unknown"))
                        if include_numbers
                        else f"Contact {contact_id}"
                    ),
                    company=(
                        str(raw["company"]) if include_numbers and raw.get("company") else None
                    ),
                    numbers=[
                        number if include_numbers else str(self._mask_number(number))
                        for number in numbers
                    ],
                    phonebooks=[str(book.get("name", "")) for book in books if book.get("name")],
                ),
                "numbers": numbers,
                "calls": CallCounts(),
                "inbound": 0,
                "outbound": 0,
                "via_ivr": 0,
                "without_ivr": 0,
                "handling": 0,
            }
            for number in numbers:
                normalized = self._normalize_phone_number(number)
                if normalized:
                    number_owners.setdefault(normalized, []).append(contact_id)
        processed = 0
        matched_ids: set[str] = set()
        unmatched = 0
        page = 1
        total_available: int | None = None
        seen_ids: set[str] = set()
        while total_available is None or processed < total_available:
            result = await self.get_calls(
                period=period,
                page=page,
                page_size=1000,
                include_numbers=True,
                api_version=api_version,
            )
            total_available = self._validate_call_page(
                result,
                expected_total=total_available,
                processed=processed,
                seen_ids=seen_ids,
            )
            if total_available > max_records:
                raise ValueError(
                    f"contact analysis would process {total_available} CDRs; use a narrower "
                    f"period (limit {max_records})"
                )
            for call in result.calls:
                direction = call.direction.lower()
                candidate = (
                    call.caller.number
                    if direction == "inbound"
                    else call.callee.number
                    if direction == "outbound"
                    else None
                )
                owners = number_owners.get(self._normalize_phone_number(candidate), [])
                if len(owners) != 1:
                    unmatched += 1
                    continue
                entry = contacts[owners[0]]
                counts: CallCounts = entry["calls"]
                counts.total += 1
                self._increment_status(counts, call.status)
                if direction == "inbound":
                    entry["inbound"] += 1
                else:
                    entry["outbound"] += 1
                if call.ivrs:
                    entry["via_ivr"] += 1
                else:
                    entry["without_ivr"] += 1
                entry["handling"] += call.handling_seconds
                matched_ids.add(call.id)
            processed += len(result.calls)
            page += 1
        rows = [
            ContactCallStats(
                contact=entry["ref"],
                calls=entry["calls"],
                inbound_calls=entry["inbound"],
                outbound_calls=entry["outbound"],
                via_ivr_calls=entry["via_ivr"],
                without_ivr_calls=entry["without_ivr"],
                total_handling_seconds=entry["handling"],
            )
            for entry in contacts.values()
            if entry["calls"].total
        ]
        rows.sort(key=lambda item: (-item.calls.total, item.contact.name))
        return ContactCallReport(
            period=period,
            contacts=rows,
            matched_calls=len(matched_ids),
            unmatched_calls=unmatched,
            total_processed=processed,
            numbers_masked=not include_numbers,
            matching_rule=(
                "Exact match after removing non-digits; duplicate directory numbers are treated "
                "as ambiguous and are not attributed."
            ),
        )

    @staticmethod
    def _normalize_phone_number(number: Any) -> str:
        return re.sub(r"\D", "", str(number or ""))

    async def get_agent_call_summary(
        self,
        *,
        period: Period,
        queue_id: int,
        agent_ids: list[int] | None = None,
        api_version: str = "v2.0",
    ) -> list[AgentCallSummary]:
        params: dict[str, Any] = await self._period_params(period)
        params.update(
            {
                "type": "queueagentinoutcalls",
                "queue_id": queue_id,
                "agent_id_list": ",".join(map(str, agent_ids or [])) or None,
            }
        )
        payload = await self.client.get("call_report/list", api_version=api_version, params=params)
        rows = self._require_complete_report_rows(
            payload, "queue_agent_in_out_calls_list", "queue agent call summary report"
        )
        return [
            AgentCallSummary(
                agent=Party(name=raw.get("agent_name"), number=str(raw.get("agent_num", ""))),
                queue_id=queue_id,
                period=period,
                queue_answered_calls=int(raw.get("queue_answered_calls", 0) or 0),
                inbound_calls=(
                    int(raw["inbound_calls"]) if raw.get("inbound_calls") is not None else None
                ),
                outbound_calls=int(raw.get("outbound_calls", 0) or 0),
                outbound_answered_calls=(
                    int(raw["outbound_answer_calls"])
                    if raw.get("outbound_answer_calls") is not None
                    else None
                ),
                total_calls=int(raw.get("total_calls", 0) or 0),
                total_talk_seconds=int(raw.get("total_duration", 0) or 0),
                queue_talk_seconds=int(
                    raw.get("queue_talk_duration", raw.get("inbound_duration", 0)) or 0
                ),
                outbound_talk_seconds=int(
                    raw.get("outbound_talk_duration", raw.get("outbound_duration", 0)) or 0
                ),
                average_talk_seconds=int(raw.get("average_talk_duration", 0) or 0),
                average_wait_seconds=int(raw.get("average_waiting_duration", 0) or 0),
                average_hold_seconds=int(raw.get("average_hold_duration", 0) or 0),
                average_service_seconds=(
                    int(
                        raw.get(
                            "average_answered_server_duration",
                            raw.get("average_handle_duration"),
                        )
                    )
                    if raw.get("average_answered_server_duration") is not None
                    or raw.get("average_handle_duration") is not None
                    else None
                ),
            )
            for raw in rows
        ]

    async def get_ring_group_stats(
        self,
        *,
        period: Period,
        ring_group_ids: list[int],
        api_version: str = "v2.0",
    ) -> list[RingGroupStats]:
        if not ring_group_ids:
            raise ValueError("ring_group_ids must not be empty")
        params: dict[str, Any] = await self._period_params(period)
        params.update(
            {
                "type": "ringgroupstatistics",
                "ring_group_id_list": ",".join(map(str, ring_group_ids)),
            }
        )
        payload = await self.client.get("call_report/list", api_version=api_version, params=params)
        rows = self._require_complete_report_rows(
            payload, "ring_group_statistics_list", "ring-group statistics report"
        )
        return [
            RingGroupStats(
                ring_group=RoutedDestination(
                    name=str(raw.get("group_name", "Unknown")),
                    number=str(raw.get("group_num", "")),
                ),
                period=period,
                calls=CallCounts(
                    total=int(raw.get("total_calls", 0) or 0),
                    answered=int(raw.get("answered_calls", 0) or 0),
                ),
                members=[
                    RingGroupMemberStats(
                        agent=Party(
                            name=member.get("ext_name"), number=str(member.get("ext_num", ""))
                        ),
                        answered_calls=int(member.get("answered_calls", 0) or 0),
                        total_group_calls=int(member.get("total_calls", 0) or 0),
                    )
                    for member in raw.get("member_list") or []
                ],
            )
            for raw in rows
        ]

    async def get_missed_calls(
        self,
        *,
        period: Period,
        page: int = 1,
        page_size: int = 100,
        include_numbers: bool = False,
        api_version: str = "v2.0",
        miss_call_type: str | None = None,
    ) -> CallPage:
        if page < 1 or not 1 <= page_size <= 1000:
            raise ValueError("page must be >= 1 and page_size must be between 1 and 1000")
        params: dict[str, Any] = await self._period_params(period)
        params.update({"type": "unreturnmisscall", "miss_call_type": miss_call_type})
        payload = await self.client.get("call_report/list", api_version=api_version, params=params)
        rows = self._require_complete_report_rows(
            payload, "unreturn_miss_call_list", "missed-call report"
        )
        if miss_call_type is None:
            rows = [row for row in rows if row.get("miss_call_type") != "abandoned"]
        else:
            rows = [row for row in rows if row.get("miss_call_type") == miss_call_type]
        filtered_total = len(rows)
        start_index = (page - 1) * page_size
        selected_rows = rows[start_index : start_index + page_size]
        calls = []
        for row in selected_rows:
            normalized = dict(row)
            normalized["last_status"] = {
                "no_answer": "NO ANSWER",
                "busy": "BUSY",
                "abandoned": "ABANDONED",
            }.get(str(row.get("miss_call_type")), "UNKNOWN")
            normalized["call_duration"] = row.get("ring_duration", 0)
            calls.append(self._normalize_call(normalized, include_numbers, self._pbx_info))
        return CallPage(
            period=period,
            calls=calls,
            total_available=filtered_total,
            page=page,
            page_size=page_size,
            numbers_masked=not include_numbers,
        )

    async def get_abandoned_calls(
        self,
        *,
        period: Period,
        page: int = 1,
        page_size: int = 100,
        queue_ids: list[int] | None = None,
        include_numbers: bool = False,
        api_version: str = "v2.0",
    ) -> CallPage:
        if api_version == "v1.0":
            if queue_ids:
                raise ValueError("queue filters are not supported by the v1 missed-call report")
            return await self.get_missed_calls(
                period=period,
                page=page,
                page_size=page_size,
                include_numbers=include_numbers,
                api_version=api_version,
                miss_call_type="abandoned",
            )
        return await self.get_calls(
            period=period,
            page=page,
            page_size=page_size,
            queue_ids=queue_ids,
            status="ABANDONED",
            include_numbers=include_numbers,
            api_version=api_version,
        )

    async def get_call_details(
        self,
        call_id: str,
        *,
        include_numbers: bool = False,
    ) -> CallDetail:
        info = self._pbx_info or await self.get_pbx_info()
        payload = await self.client.get(
            CDR_DETAIL_V2.path,
            api_version=CDR_DETAIL_V2.version,
            params={"uid": call_id},
        )
        raw = payload.get("data") or {}
        basic = raw.get("basic") or {}
        raw_timeline = raw.get("timeline") or []
        if len(raw_timeline) > 1000:
            raise ValueError("CDR detail timeline exceeds the 1000-leg safety limit")
        if sum(len(leg.get("event_list") or []) for leg in raw_timeline) > 10_000:
            raise ValueError("CDR detail events exceed the 10000-event safety limit")
        timeline = []
        for leg in raw_timeline:
            caller_name, caller_number = self._split_party_label(leg.get("call_from"))
            callee_name, callee_number = self._split_party_label(leg.get("call_to"))
            leg_direction = str(leg.get("call_type", basic.get("call_type", "Unknown"))).casefold()
            if not include_numbers and leg_direction == "inbound":
                caller_name = None
            if not include_numbers and leg_direction == "outbound":
                callee_name = None
            events: list[CallEvent] = []
            for event in leg.get("event_list") or []:
                events.append(
                    CallEvent(
                        id=str(event["event_id"]) if event.get("event_id") is not None else None,
                        name=str(event.get("event_name") or "unknown"),
                        type=str(event.get("event_type") or "unknown"),
                        elapsed_seconds=int(event.get("event_time", 0) or 0),
                        occurred_at=self._parse_datetime(event.get("event_ts"), info),
                    )
                )
            timeline.append(
                CallLeg(
                    sequence=leg.get("leg"),
                    started_at=self._parse_datetime(leg.get("time"), info),
                    status=str(leg.get("status", "UNKNOWN")),
                    caller=Party(
                        name=caller_name,
                        number=(
                            caller_number if include_numbers else self._mask_number(caller_number)
                        ),
                    ),
                    callee=Party(
                        name=callee_name,
                        number=(
                            callee_number if include_numbers else self._mask_number(callee_number)
                        ),
                    ),
                    duration_seconds=int(leg.get("call_duration", 0) or 0),
                    ringing_seconds=int(leg.get("ring_duration", 0) or 0),
                    talking_seconds=int(leg.get("talk_duration", 0) or 0),
                    hold_seconds=int(leg.get("hold_duration", 0) or 0),
                    events=events,
                )
            )
        return CallDetail(
            call=self._normalize_call(basic, include_numbers, info),
            timeline=timeline,
            numbers_masked=not include_numbers,
        )

    @classmethod
    def _supports_v2_call_reports(cls, firmware_version: str, model: str | None = None) -> bool:
        return cls._supports_minimum_firmware(firmware_version, model, minor=21)

    @classmethod
    def _supports_v2_cdr(cls, firmware_version: str, model: str | None = None) -> bool:
        return cls._supports_minimum_firmware(firmware_version, model, minor=23)

    @staticmethod
    def _supports_minimum_firmware(firmware_version: str, model: str | None, *, minor: int) -> bool:
        parts = firmware_version.split(".")
        try:
            numeric = tuple(int(part) for part in parts[-4:])
        except ValueError:
            return False
        model_lower = (model or "").lower()
        if "appliance" in model_lower:
            edition = 37
        elif "cloud" in model_lower:
            edition = 84
        else:
            edition = 83
        return numeric >= (edition, minor, 0, 123 if minor == 23 else 117)

    @staticmethod
    def _queue_bucket_start(period: Period, bucket: int) -> datetime:
        start, end = period.start, period.end
        if start.date() == end.date():
            return datetime(start.year, start.month, start.day, bucket)
        if start.month == 1 and start.day == 1 and end.month == 12 and end.day == 31:
            return datetime(start.year, bucket, 1)
        return datetime(start.year, start.month, bucket)

    @staticmethod
    def _report_bucket(period: Period, info: PBXInfo) -> str:
        start, end = period.start, period.end
        full_day = (
            start.time() == datetime.min.time()
            and end.time() == datetime.max.replace(microsecond=0).time()
        )
        if not full_day:
            raise ValueError(
                "Queue AVG Wait & Talk Time requires a complete day, month, or year period"
            )
        configured = info.date_format or "YYYY/MM/DD"
        separator = "-" if "-" in configured else "/"
        order = configured.replace("-", "/").split("/")

        def render(parts: dict[str, str], count: int) -> str:
            return separator.join(parts[item] for item in order[:count])

        values = {"YYYY": f"{start.year:04d}", "MM": f"{start.month:02d}", "DD": f"{start.day:02d}"}
        if (
            start.year == end.year
            and start.month == 1
            and start.day == 1
            and end.month == 12
            and end.day == 31
        ):
            return values["YYYY"]
        last_day = monthrange(start.year, start.month)[1]
        if (
            start.year == end.year
            and start.month == end.month
            and start.day == 1
            and end.day == last_day
        ):
            month_order = [item for item in order if item != "DD"]
            return separator.join(values[item] for item in month_order)
        if start.date() == end.date():
            return render(values, 3)
        raise ValueError("period must be one complete day, month, or year")

    async def _period_params(self, period: Period) -> dict[str, str]:
        info = self._pbx_info or await self.get_pbx_info()
        return {
            "start_time": self._format_for_pbx(period.start, info),
            "end_time": self._format_for_pbx(period.end, info),
        }

    async def _cdr_period_params(self, period: Period, api_version: str) -> dict[str, str]:
        values = await self._period_params(period)
        if api_version == "v2.0":
            return {"time_begin": values["start_time"], "time_end": values["end_time"]}
        return values

    @staticmethod
    def _format_for_pbx(value: datetime, info: PBXInfo) -> str:
        date_map = {
            "YYYY/MM/DD": "%Y/%m/%d",
            "MM/DD/YYYY": "%m/%d/%Y",
            "DD/MM/YYYY": "%d/%m/%Y",
            "YYYY-MM-DD": "%Y-%m-%d",
            "MM-DD-YYYY": "%m-%d-%Y",
            "DD-MM-YYYY": "%d-%m-%Y",
        }
        date_format = date_map.get(info.date_format or "", "%Y/%m/%d")
        time_spec = info.time_format or ""
        has_meridiem = bool(
            re.search(r"(?:^|\s)(?:AM|PM)$", info.system_time or "", re.IGNORECASE)
            or re.search(r"(?:^|[^A-Za-z])[aA](?:[^A-Za-z]|$)", time_spec)
        )
        time_format = "%I:%M:%S %p" if has_meridiem else "%H:%M:%S"
        return value.strftime(f"{date_format} {time_format}")

    @staticmethod
    def _normalize_queue_performance(raw: dict[str, Any], period: Period) -> QueuePerformance:
        return QueuePerformance(
            queue=QueueRef(name=str(raw.get("queue", "Unknown")), number=raw.get("queue_num")),
            period=period,
            calls=CallCounts(
                total=int(raw.get("total_calls", 0)),
                answered=int(raw.get("answered_calls", 0)),
                missed=int(raw.get("missed_calls", 0)),
                abandoned=int(raw.get("abandoned_calls", 0)),
            ),
            waiting=WaitingMetrics(
                average_seconds=int(raw.get("average_waiting_time", 0)),
                maximum_seconds=int(raw.get("max_waiting_time", 0)),
                all_calls_average_seconds=raw.get("all_call_average_waiting_time"),
                answered_total_seconds=raw.get("answered_waiting_time"),
                all_calls_total_seconds=raw.get("total_waiting_time"),
            ),
            talking=TalkingMetrics(
                average_seconds=int(raw.get("average_talking_time", 0)),
                total_seconds=int(raw.get("answered_call_time", raw.get("total_talking_time", 0))),
                hold_seconds=int(raw.get("answered_hold_time", 0)),
            ),
            rates=Rates(
                answered_percent=raw.get("answered_rate"),
                missed_percent=raw.get("missed_rate"),
                abandoned_percent=raw.get("abandoned_rate"),
                sla_percent=raw.get("sla"),
            ),
            average_service_seconds=raw.get("average_server_time", raw.get("average_handle_time")),
        )

    @classmethod
    def _normalize_call(
        cls, raw: dict[str, Any], include_numbers: bool, info: PBXInfo | None = None
    ) -> CallRecord:
        status = str(raw.get("last_status", raw.get("disposition", "UNKNOWN")))
        queue_raw = raw.get("queues")
        if isinstance(queue_raw, list):
            queue_raw = queue_raw[0] if queue_raw else None
        queue = None
        if isinstance(queue_raw, dict):
            queue = QueueRef(
                name=str(queue_raw.get("name", "Unknown")), number=queue_raw.get("number")
            )
        caller_number = raw.get("call_from_number") or raw.get("src")
        callee_number = raw.get("call_to_number") or raw.get("dst")
        direction = str(raw.get("call_type", "Unknown"))
        caller_name = raw.get("call_from_name") or raw.get("srcname")
        callee_name = raw.get("call_to_name") or raw.get("dstname")
        if not include_numbers and direction.casefold() == "inbound":
            caller_name = None
        if not include_numbers and direction.casefold() == "outbound":
            callee_name = None
        suppress_routed_name = not include_numbers and direction.casefold() == "outbound"
        second = cls._party_from_fields(
            raw, "second_participant", include_numbers, suppress_name=suppress_routed_name
        )
        last = cls._party_from_fields(
            raw, "last_participant", include_numbers, suppress_name=suppress_routed_name
        )
        routes = {
            "queues": cls._normalize_destinations(raw.get("queues"), include_numbers),
            "ivrs": cls._normalize_destinations(raw.get("ivrs"), include_numbers),
            "ring_groups": cls._normalize_destinations(raw.get("ring_groups"), include_numbers),
            "call_flows": cls._normalize_destinations(raw.get("call_flows"), include_numbers),
            "dids": cls._normalize_destinations(raw.get("dids"), include_numbers),
            "outbound_caller_ids": cls._normalize_destinations(
                raw.get("outbound_caller_ids"), include_numbers
            ),
        }
        return CallRecord(
            id=str(raw.get("uid") or raw.get("new_id") or raw.get("id")),
            started_at=cls._parse_time(raw.get("time"), info),
            direction=direction,
            status=status,
            caller=Party(
                name=caller_name,
                number=caller_number if include_numbers else cls._mask_number(caller_number),
            ),
            callee=Party(
                name=callee_name,
                number=callee_number if include_numbers else cls._mask_number(callee_number),
            ),
            second_participant=second,
            last_participant=last,
            queue=queue,
            **routes,
            segments=int(raw.get("segments", 1) or 1),
            disconnected_by=raw.get("disconnected_by"),
            duration_seconds=int(raw.get("call_duration", raw.get("duration", 0)) or 0),
            routing_seconds=int(raw.get("routing_duration", raw.get("ring_duration", 0)) or 0),
            handling_seconds=int(raw.get("handling_duration", 0) or 0),
            talking_seconds=int(raw.get("talk_duration", 0) or 0),
        )

    @classmethod
    def _party_from_fields(
        cls,
        raw: dict[str, Any],
        prefix: str,
        include_numbers: bool,
        *,
        suppress_name: bool = False,
    ) -> Party | None:
        label = raw.get(prefix)
        name = raw.get(f"{prefix}_name")
        number = raw.get(f"{prefix}_number")
        if not name and not number and label:
            name, number = cls._split_party_label(label)
        if not name and not number:
            return None
        digits = re.sub(r"\D", "", str(number or ""))
        safe_name = None if not include_numbers and (suppress_name or len(digits) > 6) else name
        return Party(
            name=str(safe_name) if safe_name else None,
            number=str(number) if include_numbers else cls._mask_number(number),
        )

    @classmethod
    def _normalize_destinations(cls, values: Any, include_numbers: bool) -> list[RoutedDestination]:
        if values in (None, ""):
            return []
        if not isinstance(values, list):
            values = [values]
        result: list[RoutedDestination] = []
        for value in values:
            status = None
            if isinstance(value, dict):
                name = value.get("name")
                number = value.get("number")
                status = value.get("status")
            else:
                name, number = cls._split_party_label(value)
            result.append(
                RoutedDestination(
                    name=str(name) if name else None,
                    number=str(number) if include_numbers else cls._mask_number(number),
                    status=str(status) if status else None,
                )
            )
        return result

    @staticmethod
    def _parse_time(value: Any, info: PBXInfo | None = None) -> datetime:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid PBX timestamp: {value!r}")
        date_formats = {
            "YYYY/MM/DD": "%Y/%m/%d",
            "MM/DD/YYYY": "%m/%d/%Y",
            "DD/MM/YYYY": "%d/%m/%Y",
            "YYYY-MM-DD": "%Y-%m-%d",
            "MM-DD-YYYY": "%m-%d-%Y",
            "DD-MM-YYYY": "%d-%m-%Y",
        }
        configured = (info.date_format if info else None) or "YYYY/MM/DD"
        time_format = (
            "%I:%M:%S %p" if info and info.time_format and "hh" in info.time_format else "%H:%M:%S"
        )
        try:
            return datetime.strptime(value, f"{date_formats[configured]} {time_format}")
        except (KeyError, ValueError) as exc:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                raise ValueError(
                    f"invalid PBX timestamp {value!r} for configured format {configured}"
                ) from exc

    @classmethod
    def _parse_datetime(cls, value: Any, info: PBXInfo | None = None) -> datetime | None:
        if value is None:
            return None
        return cls._parse_time(value, info)

    @staticmethod
    def _split_party_label(value: Any) -> tuple[str | None, str | None]:
        if value is None:
            return None, None
        label = str(value)
        if label.endswith(">") and "<" in label:
            name, _, number = label.rpartition("<")
            return name or None, number[:-1] or None
        return None, label or None

    @staticmethod
    def _mask_number(number: Any) -> str | None:
        if number is None:
            return None
        value = str(number)
        if len(value) <= 6:
            return "*" * len(value)
        return f"{value[:3]}{'*' * (len(value) - 5)}{value[-2:]}"
