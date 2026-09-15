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


class CallRecord(Model):
    id: str
    started_at: datetime | str
    direction: str
    status: str
    caller: Party
    callee: Party
    queue: QueueRef | None = None
    duration_seconds: int = 0
    waiting_seconds: int = 0
    handling_seconds: int = 0


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
    total_talk_seconds: int = 0
    average_duration_seconds: float = 0
    source: str


class WaitingMetrics(Model):
    average_seconds: int = 0
    maximum_seconds: int = 0
    all_calls_average_seconds: int | None = None


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


class CallLeg(Model):
    sequence: int | None = None
    started_at: datetime | str | None = None
    status: str
    caller: Party
    callee: Party
    duration_seconds: int = 0
    ringing_seconds: int = 0
    talking_seconds: int = 0
    hold_seconds: int = 0


class CallDetail(Model):
    call: CallRecord
    timeline: list[CallLeg] = Field(default_factory=list)
    numbers_masked: bool = True
