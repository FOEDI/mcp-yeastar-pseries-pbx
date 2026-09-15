"""Business services and normalization for Yeastar analytics."""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime
from typing import Any, Protocol

from yeastar_mcp.endpoints import CDR_DETAIL_V2, PBX_INFO, QUEUES
from yeastar_mcp.models import (
    Agent,
    AgentPerformance,
    CallCounts,
    CallDetail,
    CallLeg,
    CallPage,
    CallRecord,
    CallStats,
    Capabilities,
    Capability,
    Party,
    PBXInfo,
    Period,
    Queue,
    QueuePerformance,
    QueueRef,
    QueueWaitTimes,
    Rates,
    TalkingMetrics,
    WaitingMetrics,
    WaitTimeBucket,
)


class ReadClient(Protocol):
    async def get(
        self,
        endpoint: str,
        *,
        api_version: str = "v1.0",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class YeastarService:
    """Read-only business interface independent of Yeastar field names."""

    def __init__(self, client: ReadClient) -> None:
        self.client = client
        self._pbx_info: PBXInfo | None = None

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

    async def list_queues(self) -> list[Queue]:
        payload = await self.client.get(
            QUEUES.path,
            api_version=QUEUES.version,
            params={"page": 1, "page_size": 1000, "sort_by": "number", "order_by": "asc"},
        )
        result: list[Queue] = []
        for raw in payload.get("queue_list") or []:
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
        payload = await self.client.get(
            QUEUES.path,
            api_version=QUEUES.version,
            params={"page": 1, "page_size": 1000, "sort_by": "number", "order_by": "asc"},
        )
        agents: dict[str, dict[str, Any]] = {}
        for queue in payload.get("queue_list") or []:
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
        return [
            self._normalize_queue_performance(raw, period)
            for raw in payload.get("queue_performance_list") or []
        ]

    async def get_calls(
        self,
        *,
        period: Period,
        page: int = 1,
        page_size: int = 100,
        queue_ids: list[int] | None = None,
        status: str | None = None,
        include_numbers: bool = False,
        api_version: str = "v2.0",
    ) -> CallPage:
        if page < 1 or not 1 <= page_size <= 1000:
            raise ValueError("page must be >= 1 and page_size must be between 1 and 1000")
        if api_version == "v1.0" and queue_ids:
            raise ValueError("queue filters are not supported by CDR v1")
        params: dict[str, Any] = await self._cdr_period_params(period, api_version)
        params.update({"page": page, "page_size": page_size})
        if api_version == "v2.0":
            params.update(
                {
                    "order_by": "desc",
                    "sort_by": "time",
                    "queue_list": ",".join(map(str, queue_ids or [])) or None,
                    "last_status": status,
                }
            )
        elif status is not None:
            params["status"] = status
        payload = await self.client.get("cdr/search", api_version=api_version, params=params)
        calls = [self._normalize_call(raw, include_numbers) for raw in payload.get("data") or []]
        return CallPage(
            period=period,
            calls=calls,
            total_available=int(payload.get("total_number", len(calls))),
            page=page,
            page_size=page_size,
            numbers_masked=not include_numbers,
        )

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
            params: dict[str, Any] = await self._period_params(period)
            params.update(
                {
                    "type": "extcallstatistics",
                    "ext_id_list": ",".join(map(str, extension_ids)),
                }
            )
            payload = await self.client.get(
                "call_report/list", api_version=api_version, params=params
            )
            rows = payload.get("ext_call_statistics_list") or []
            counts = CallCounts(
                total=sum(int(row.get("total_call_count", 0)) for row in rows),
                answered=sum(int(row.get("answered_calls", 0)) for row in rows),
                missed=sum(int(row.get("no_answer_calls", 0)) for row in rows),
                abandoned=sum(int(row.get("abandoned_calls", 0)) for row in rows),
                busy=sum(int(row.get("busy_calls", 0)) for row in rows),
                failed=sum(int(row.get("failed_calls", 0)) for row in rows),
                voicemail=sum(int(row.get("voicemail_calls", 0)) for row in rows),
            )

            total_talk = sum(int(row.get("total_talking_time", 0)) for row in rows)
            return CallStats(
                period=period,
                calls=counts,
                total_talk_seconds=total_talk,
                average_duration_seconds=(total_talk / counts.answered if counts.answered else 0),
                source=f"Yeastar Extension Call Statistics report ({api_version})",
            )
        counts = CallCounts()
        total_duration = 0
        total_talk = 0
        current_page = 1
        processed = 0
        total_available: int | None = None
        while total_available is None or processed < total_available:
            result_page = await self.get_calls(
                period=period,
                page=current_page,
                page_size=1000,
                queue_ids=queue_ids,
                include_numbers=False,
                api_version=api_version,
            )
            if total_available is None:
                total_available = result_page.total_available
            if not result_page.calls:
                break
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
                total_talk += call.handling_seconds
            processed += len(result_page.calls)
            current_page += 1
        counts.total = processed
        return CallStats(
            period=period,
            calls=counts,
            total_duration_seconds=total_duration,
            total_talk_seconds=total_talk,
            average_duration_seconds=(total_duration / counts.total if counts.total else 0),
            source=f"local aggregation of {processed} CDR records ({api_version})",
        )

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
        buckets = [
            WaitTimeBucket(
                start=self._queue_bucket_start(period, int(raw["time"])),
                calls=int(raw.get("total_calls", 0)),
                answered=int(raw.get("answered_calls", 0)),
                average_wait_seconds=int(raw.get("avg_wait_time", 0)),
                all_calls_average_wait_seconds=int(raw.get("all_call_avg_wait_time", 0)),
                average_talk_seconds=int(raw.get("avg_talk_time", 0)),
            )
            for raw in payload.get("queue_avg_wait_talk_time_list") or []
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
        results: list[AgentPerformance] = []
        for queue_raw in payload.get("queue_agent_performance_list") or []:
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
        rows = payload.get("unreturn_miss_call_list") or []
        if miss_call_type is None:
            rows = [row for row in rows if row.get("miss_call_type") != "abandoned"]
        else:
            rows = [row for row in rows if row.get("miss_call_type") == miss_call_type]
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
            calls.append(self._normalize_call(normalized, include_numbers))
        return CallPage(
            period=period,
            calls=calls,
            total_available=len(rows),
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
        payload = await self.client.get(
            CDR_DETAIL_V2.path,
            api_version=CDR_DETAIL_V2.version,
            params={"uid": call_id},
        )
        raw = payload.get("data") or {}
        timeline = []
        for leg in raw.get("timeline") or []:
            caller_name, caller_number = self._split_party_label(leg.get("call_from"))
            callee_name, callee_number = self._split_party_label(leg.get("call_to"))
            timeline.append(
                CallLeg(
                    sequence=leg.get("leg"),
                    started_at=self._parse_datetime(leg.get("time")),
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
                )
            )
        return CallDetail(
            call=self._normalize_call(raw.get("basic") or {}, include_numbers),
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
        is_12_hour = bool(info.time_format and "hh" in info.time_format)
        time_format = "%I:%M:%S %p" if is_12_hour else "%H:%M:%S"
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
    def _normalize_call(cls, raw: dict[str, Any], include_numbers: bool) -> CallRecord:
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
        return CallRecord(
            id=str(raw.get("uid") or raw.get("new_id") or raw.get("id")),
            started_at=cls._parse_time(raw.get("time")),
            direction=str(raw.get("call_type", "Unknown")),
            status=status,
            caller=Party(
                name=raw.get("call_from_name") or raw.get("srcname"),
                number=caller_number if include_numbers else cls._mask_number(caller_number),
            ),
            callee=Party(
                name=raw.get("call_to_name") or raw.get("dstname"),
                number=callee_number if include_numbers else cls._mask_number(callee_number),
            ),
            queue=queue,
            duration_seconds=int(raw.get("call_duration", raw.get("duration", 0)) or 0),
            waiting_seconds=int(raw.get("routing_duration", raw.get("ring_duration", 0)) or 0),
            handling_seconds=int(raw.get("handling_duration", raw.get("talk_duration", 0)) or 0),
        )

    @staticmethod
    def _parse_time(value: Any) -> datetime | str:
        if not isinstance(value, str):
            return str(value or "")
        try:
            return datetime.fromisoformat(value.replace("/", "-"))
        except ValueError:
            return value

    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime | str | None:
        if value is None:
            return None
        return cls._parse_time(value)

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
