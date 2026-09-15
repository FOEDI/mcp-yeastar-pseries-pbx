"""Normalized, Yeastar-independent models returned by MCP tools."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Period(Model):
    """Inclusive naive PBX-local time range represented as ISO 8601 at the MCP edge."""

    start: datetime = Field(description="Inclusive period start in ISO 8601 format")
    end: datetime = Field(description="Inclusive period end in ISO 8601 format")

    @model_validator(mode="after")
    def end_must_follow_start(self) -> "Period":
        if self.start.utcoffset() is not None or self.end.utcoffset() is not None:
            raise ValueError("period boundaries must be naive PBX-local datetimes without offsets")
        if self.end <= self.start:
            raise ValueError("period end must be after start")
        return self


class PBXInfo(Model):
    name: str
    model: str
    firmware_version: str
    system_time: str | None = None
    uptime_seconds: int | None = None
    date_format: str | None = None
    time_format: str | None = None


class Capability(Model):
    name: str
    available: bool
    api_version: str
    endpoint: str
    note: str | None = None
    basis: Literal["probed", "firmware", "documented"]


class Capabilities(Model):
    read_only: Literal[True] = True
    firmware_version: str | None = None
    capabilities: list[Capability]


class QueueRef(Model):
    id: int | None = None
    number: str | None = None
    name: str


class Extension(Model):
    id: int
    number: str
    name: str


class RingGroup(Model):
    id: int
    number: str
    name: str


class IVR(Model):
    id: int
    number: str
    name: str


class Queue(Model):
    id: int
    number: str
    name: str
    ring_strategy: str | None = None
    agent_count: int = 0


class Agent(Model):
    id: str
    number: str
    name: str
    membership_type: Literal["static", "dynamic", "mixed", "unknown"]
    queue_memberships: list[QueueRef]


class Party(Model):
    name: str | None = None
    number: str | None = None


class RoutedDestination(Model):
    name: str | None = None
    number: str | None = None
    status: str | None = None


class CallRecord(Model):
    id: str
    started_at: datetime
    direction: str
    status: str
    caller: Party
    callee: Party
    second_participant: Party | None = None
    last_participant: Party | None = None
    queue: QueueRef | None = None
    queues: list[RoutedDestination] = Field(default_factory=list)
    ivrs: list[RoutedDestination] = Field(default_factory=list)
    ring_groups: list[RoutedDestination] = Field(default_factory=list)
    call_flows: list[RoutedDestination] = Field(default_factory=list)
    dids: list[RoutedDestination] = Field(default_factory=list)
    outbound_caller_ids: list[RoutedDestination] = Field(default_factory=list)
    segments: int = 1
    disconnected_by: str | None = None
    duration_seconds: int = 0
    routing_seconds: int = 0
    handling_seconds: int = 0
    talking_seconds: int = 0


class CallPage(Model):
    period: Period
    calls: list[CallRecord]
    total_available: int
    page: int
    page_size: int
    numbers_masked: bool


class CallCounts(Model):
    total: int = 0
    answered: int = 0
    missed: int = 0
    abandoned: int = 0
    busy: int = 0
    failed: int = 0
    voicemail: int = 0


class CallStats(Model):
    period: Period
    calls: CallCounts
    total_duration_seconds: int = 0
    total_handling_seconds: int = 0
    total_talking_seconds: int = 0
    average_duration_seconds: float = 0
    source: str


class CallActivityBucket(Model):
    start: datetime
    calls: CallCounts
    directions: dict[str, int] = Field(default_factory=dict)
    duration_bands: dict[str, int] = Field(default_factory=dict)
    total_duration_seconds: int = 0
    total_handling_seconds: int = 0
    total_talking_seconds: int = 0
    average_duration_seconds: float = 0


class CallActivity(Model):
    period: Period
    bucket: Literal["hour", "day", "week", "month"]
    buckets: list[CallActivityBucket]
    duration_band_seconds: int
    duration_cap_seconds: int
    duration_bands: dict[str, int] = Field(default_factory=dict)
    average_duration_seconds: float = 0
    total_processed: int
    source: str


class ContactRef(Model):
    id: int
    name: str
    company: str | None = None
    numbers: list[str]
    phonebooks: list[str] = Field(default_factory=list)


class ContactCallStats(Model):
    contact: ContactRef
    calls: CallCounts
    inbound_calls: int = 0
    outbound_calls: int = 0
    via_ivr_calls: int = 0
    without_ivr_calls: int = 0
    total_handling_seconds: int = 0


class ContactCallReport(Model):
    period: Period
    contacts: list[ContactCallStats]
    matched_calls: int
    unmatched_calls: int
    total_processed: int
    numbers_masked: bool
    matching_rule: str


class ExtensionPerformance(Model):
    extension: Extension
    period: Period
    communication_type: str | None = None
    calls: CallCounts
    total_holding_seconds: int = 0
    total_talking_seconds: int = 0
    average_talking_seconds: float = 0


class AgentCallSummary(Model):
    agent: Party
    queue_id: int
    period: Period
    queue_answered_calls: int = 0
    inbound_calls: int | None = None
    outbound_calls: int = 0
    outbound_answered_calls: int | None = None
    total_calls: int = 0
    total_talk_seconds: int = 0
    queue_talk_seconds: int = 0
    outbound_talk_seconds: int = 0
    average_talk_seconds: int = 0
    average_wait_seconds: int = 0
    average_hold_seconds: int = 0
    average_service_seconds: int | None = None


class RingGroupMemberStats(Model):
    agent: Party
    answered_calls: int = 0
    total_group_calls: int = 0


class RingGroupStats(Model):
    ring_group: RoutedDestination
    period: Period
    calls: CallCounts
    members: list[RingGroupMemberStats]


class RoutingCounts(Model):
    total: int = 0
    multi_segment: int = 0
    at_or_above_min_segments: int = 0
    loop_candidates: int = 0
    long_routing: int = 0
    route_changes: int = 0
    no_answer: int = 0
    abandoned: int = 0
    busy: int = 0
    failed: int = 0
    voicemail: int = 0


class RoutingAnomaly(Model):
    call: CallRecord
    reasons: list[str]


class RoutingAnalysis(Model):
    period: Period
    counts: RoutingCounts
    by_queue: dict[str, int]
    by_ivr: dict[str, int]
    by_ring_group: dict[str, int]
    examples: list[RoutingAnomaly]
    total_processed: int
    numbers_masked: bool
    source: str
    caveat: str


class IVRCall(Model):
    id: str
    started_at: datetime
    caller: Party
    key: str
    destination: RoutedDestination
    operation_seconds: int = 0


class IVRDestinationStat(Model):
    key: str
    destination: RoutedDestination
    destination_type: str | None = None
    calls: int


class IVRAnalysis(Model):
    ivr: RoutedDestination
    period: Period
    unique_calls: int
    total_keypresses: int
    press_counts: dict[str, int]
    destinations: list[IVRDestinationStat]
    calls: list[IVRCall] = Field(default_factory=list)
    total_call_details: int = 0
    numbers_masked: bool = True


class WaitingMetrics(Model):
    average_seconds: int = 0
    maximum_seconds: int = 0
    all_calls_average_seconds: int | None = None
    answered_total_seconds: int | None = None
    all_calls_total_seconds: int | None = None


class TalkingMetrics(Model):
    average_seconds: int = 0
    total_seconds: int = 0
    hold_seconds: int = 0


class Rates(Model):
    answered_percent: float | None = None
    missed_percent: float | None = None
    abandoned_percent: float | None = None
    sla_percent: float | None = None


class QueuePerformance(Model):
    queue: QueueRef
    period: Period
    calls: CallCounts
    waiting: WaitingMetrics
    talking: TalkingMetrics
    rates: Rates
    average_service_seconds: int | None = None


class WaitTimeBucket(Model):
    start: datetime | str
    calls: int
    answered: int
    average_wait_seconds: int
    all_calls_average_wait_seconds: int
    average_talk_seconds: int


class QueueWaitTimes(Model):
    queue: QueueRef
    period: Period
    buckets: list[WaitTimeBucket]


class AgentPerformance(Model):
    agent: Agent
    queue: QueueRef
    period: Period
    calls: CallCounts
    waiting: WaitingMetrics
    talking: TalkingMetrics
    missed_rate_percent: float | None = None


class CallEvent(Model):
    id: str | None = None
    name: str
    type: str
    elapsed_seconds: int = 0
    occurred_at: datetime | str | None = None


class CallLeg(Model):
    sequence: int | None = None
    started_at: datetime | None = None
    status: str
    caller: Party
    callee: Party
    duration_seconds: int = 0
    ringing_seconds: int = 0
    talking_seconds: int = 0
    hold_seconds: int = 0
    events: list[CallEvent] = Field(default_factory=list)


class CallDetail(Model):
    call: CallRecord
    timeline: list[CallLeg] = Field(default_factory=list)
    numbers_masked: bool = True
