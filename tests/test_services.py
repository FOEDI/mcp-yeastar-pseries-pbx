from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from yeastar_mcp.models import Extension, PBXInfo, Period
from yeastar_mcp.services import YeastarService


def test_lowercase_hh_without_meridiem_parses_live_pbx_24_hour_timestamp() -> None:
    info = PBXInfo(
        name="PBX",
        model="P-Series Software Edition",
        firmware_version="83.24.0.73",
        system_time="16/09/2026 10:17:50",
        date_format="DD/MM/YYYY",
        time_format="hh:mm:ss",
    )
    parsed = YeastarService._parse_time("16/09/2026 10:17:50", info)
    assert parsed == datetime(2026, 9, 16, 10, 17, 50)


def test_lowercase_hh_without_meridiem_uses_live_pbx_24_hour_format() -> None:
    info = PBXInfo(
        name="PBX",
        model="P-Series Software Edition",
        firmware_version="83.24.0.73",
        system_time="16/09/2026 13:44:49",
        date_format="DD/MM/YYYY",
        time_format="hh:mm:ss",
    )
    formatted = YeastarService._format_for_pbx(datetime(2026, 9, 16, 13, 45, 22), info)
    assert formatted == "16/09/2026 13:45:22"


def test_explicit_meridiem_in_live_pbx_clock_uses_12_hour_format() -> None:
    info = PBXInfo(
        name="PBX",
        model="P-Series Software Edition",
        firmware_version="83.24.0.73",
        system_time="09/16/2026 01:44:49 PM",
        date_format="MM/DD/YYYY",
        time_format="hh:mm:ss",
    )
    formatted = YeastarService._format_for_pbx(datetime(2026, 9, 16, 13, 45, 22), info)
    assert formatted == "09/16/2026 01:45:22 PM"


@pytest.fixture
def period() -> Period:
    return Period(start=datetime(2026, 9, 1), end=datetime(2026, 9, 30, 23, 59, 59))


@pytest.mark.asyncio
async def test_pbx_info_omits_persistent_serial_identifier(fixture_json) -> None:
    client = AsyncMock()
    client.get.return_value = fixture_json("pbx_info.json")
    info = await YeastarService(client).get_pbx_info()
    assert not hasattr(info, "serial_number")


@pytest.mark.asyncio
async def test_list_queues_and_agents_are_normalized(fixture_json) -> None:
    client = AsyncMock()
    client.get.return_value = fixture_json("queues.json")
    service = YeastarService(client)
    queues = await service.list_queues()
    agents = await service.list_agents()
    assert queues[0].name == "SAV"
    assert {agent.number for agent in agents} == {"1000", "1004"}
    assert agents[0].queue_memberships


@pytest.mark.asyncio
async def test_list_ivrs_exposes_only_id_number_and_name(fixture_json) -> None:
    client = AsyncMock()
    client.get.return_value = fixture_json("ivr_list.json")
    ivrs = await YeastarService(client).list_ivrs()
    assert [(ivr.id, ivr.number, ivr.name) for ivr in ivrs] == [
        (12, "6500", "Main IVR"),
        (13, "6501", "After Hours"),
    ]
    assert client.get.await_args.kwargs["api_version"] == "v1.0"
    assert client.get.await_args.args[0] == "ivr/list"


@pytest.mark.asyncio
async def test_list_extensions_exposes_minimal_internal_directory(fixture_json) -> None:
    client = AsyncMock()
    client.get.return_value = fixture_json("extensions.json")
    extensions = await YeastarService(client).list_extensions()
    assert [(ext.id, ext.number, ext.name) for ext in extensions] == [
        (73, "1000", "Leo Ball"),
        (74, "1004", "Agent One"),
    ]
    assert client.get.await_args.args[0] == "extension/list"


@pytest.mark.asyncio
async def test_list_ring_groups_exposes_minimal_routing_directory(fixture_json) -> None:
    client = AsyncMock()
    client.get.return_value = fixture_json("ring_groups.json")
    groups = await YeastarService(client).list_ring_groups()
    assert [(group.id, group.number, group.name) for group in groups] == [
        (21, "6300", "Support Backup"),
        (22, "6301", "Sales Backup"),
    ]
    assert client.get.await_args.args[0] == "ringgroup/list"


@pytest.mark.asyncio
async def test_directory_pagination_fails_on_premature_empty_page(fixture_json) -> None:
    first = fixture_json("ivr_list.json")
    first["total_number"] = 3
    client = AsyncMock()
    client.get.side_effect = [first, {"total_number": 3, "data": []}]
    with pytest.raises(ValueError, match="premature empty page"):
        await YeastarService(client).list_ivrs()


@pytest.mark.asyncio
async def test_directory_pagination_fails_when_total_changes(fixture_json) -> None:
    first = fixture_json("ivr_list.json")
    first["total_number"] = 3
    second = {"total_number": 4, "data": [{"id": 14, "number": "6502", "name": "Other"}]}
    client = AsyncMock()
    client.get.side_effect = [first, second]
    with pytest.raises(ValueError, match="total changed"):
        await YeastarService(client).list_ivrs()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "fixture_name", "row_key"),
    [
        ("list_queues", "queues.json", "queue_list"),
        ("list_ivrs", "ivr_list.json", "data"),
        ("list_extensions", "extensions.json", "data"),
        ("list_ring_groups", "ring_groups.json", "data"),
    ],
)
async def test_directory_pagination_requires_declared_total(
    method: str, fixture_name: str, row_key: str, fixture_json
) -> None:
    payload = fixture_json(fixture_name)
    payload.pop("total_number")
    client = AsyncMock()
    client.get.return_value = payload
    with pytest.raises(ValueError, match="missing total_number"):
        await getattr(YeastarService(client), method)()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "fixture_name", "row_key"),
    [
        ("list_queues", "queues.json", "queue_list"),
        ("list_ivrs", "ivr_list.json", "data"),
        ("list_extensions", "extensions.json", "data"),
        ("list_ring_groups", "ring_groups.json", "data"),
    ],
)
async def test_directory_pagination_rejects_duplicate_ids_across_pages(
    method: str, fixture_name: str, row_key: str, fixture_json
) -> None:
    payload = fixture_json(fixture_name)
    row = payload[row_key][0]
    first = {"total_number": 2, row_key: [row]}
    second = {"total_number": 2, row_key: [dict(row)]}
    client = AsyncMock()
    client.get.side_effect = [first, second]
    with pytest.raises(ValueError, match="duplicate stable row ID"):
        await getattr(YeastarService(client), method)()


@pytest.mark.asyncio
async def test_company_contact_pagination_requires_total_and_rejects_duplicate_pages(
    period: Period, fixture_json
) -> None:
    row = fixture_json("company_contacts.json")["data"][0]
    missing_total = AsyncMock()
    missing_total.get.return_value = {"data": [row]}
    with pytest.raises(ValueError, match="missing total_number"):
        await YeastarService(missing_total).get_contact_call_stats(period=period)

    duplicate = AsyncMock()
    duplicate.get.side_effect = [
        {"total_number": 2, "data": [row]},
        {"total_number": 2, "data": [dict(row)]},
    ]
    with pytest.raises(ValueError, match="duplicate stable row ID"):
        await YeastarService(duplicate).get_contact_call_stats(period=period)


@pytest.mark.asyncio
async def test_queue_performance_matches_llm_friendly_shape(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("queue_performance.json")]
    service = YeastarService(client)
    result = await service.get_queue_performance(period=period, queue_ids=[3])
    assert result[0].queue.name == "SAV"
    assert result[0].calls.total == 1200
    assert result[0].waiting.average_seconds == 28
    assert result[0].period == period
    request = client.get.await_args_list[-1]
    assert request.kwargs["api_version"] == "v2.0"
    assert request.kwargs["params"]["type"] == "queueperformance"


@pytest.mark.asyncio
async def test_cdr_search_requires_declared_total(period: Period, fixture_json) -> None:
    payload = fixture_json("cdr_v2.json")
    payload.pop("total_number")
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), payload]
    with pytest.raises(ValueError, match="CDR search response is missing total_number"):
        await YeastarService(client).get_calls(period=period)


@pytest.mark.asyncio
async def test_cdr_search_rejects_more_unique_rows_than_declared(
    period: Period, fixture_json
) -> None:
    payload = fixture_json("cdr_v2.json")
    payload["total_number"] = 1
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), payload]
    with pytest.raises(ValueError, match="more rows than its declared total"):
        await YeastarService(client).get_calls(period=period)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs", "fixture_name", "row_key"),
    [
        (
            "get_queue_performance",
            {"queue_ids": [3]},
            "queue_performance.json",
            "queue_performance_list",
        ),
        (
            "get_call_stats",
            {"extension_ids": [73]},
            "extension_call_stats.json",
            "ext_call_statistics_list",
        ),
        (
            "get_queue_wait_times",
            {"queue_ids": [3]},
            "queue_wait_times.json",
            "queue_avg_wait_talk_time_list",
        ),
        (
            "get_agent_performance",
            {"queue_id": 3},
            "agent_performance.json",
            "queue_agent_performance_list",
        ),
        (
            "get_agent_call_summary",
            {"queue_id": 3},
            "agent_call_summary.json",
            "queue_agent_in_out_calls_list",
        ),
        (
            "get_ring_group_stats",
            {"ring_group_ids": [7]},
            "ring_group_stats.json",
            "ring_group_statistics_list",
        ),
        ("get_missed_calls", {}, "missed_calls.json", "unreturn_miss_call_list"),
    ],
)
async def test_complete_call_report_lists_require_declared_consistent_total(
    method: str, kwargs: dict, fixture_name: str, row_key: str, period: Period, fixture_json
) -> None:
    missing = fixture_json(fixture_name)
    missing.pop("total_number", None)
    directory = [fixture_json("extensions.json")] if method == "get_call_stats" else []
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), *directory, missing]
    with pytest.raises(ValueError, match="missing total_number"):
        await getattr(YeastarService(client), method)(period=period, **kwargs)

    inconsistent = fixture_json(fixture_name)
    inconsistent["total_number"] = len(inconsistent[row_key]) + 1
    directory = [fixture_json("extensions.json")] if method == "get_call_stats" else []
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), *directory, inconsistent]
    with pytest.raises(ValueError, match="declared total"):
        await getattr(YeastarService(client), method)(period=period, **kwargs)


def test_raw_number_disclosure_requires_server_side_opt_in() -> None:
    restricted = YeastarService(AsyncMock())
    with pytest.raises(PermissionError, match="YEASTAR_ALLOW_RAW_NUMBERS"):
        restricted.ensure_raw_numbers_allowed(True)
    restricted.ensure_raw_numbers_allowed(False)
    YeastarService(AsyncMock(), allow_raw_numbers=True).ensure_raw_numbers_allowed(True)


@pytest.mark.asyncio
async def test_calls_mask_numbers_by_default(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("cdr_v2.json")]
    service = YeastarService(client)
    result = await service.get_calls(period=period)
    assert result.calls[0].caller.number == "+33*******89"
    assert result.calls[0].caller.name is None
    assert result.calls[0].callee.number == "****"
    assert result.calls[0].callee.name == "SAV"
    assert result.calls[0].second_participant.name == "Agent One"
    assert result.calls[0].second_participant.number == "****"
    assert result.calls[0].last_participant.name == "Support Two"
    assert result.calls[0].segments == 3
    assert result.calls[0].disconnected_by == "External"
    assert result.calls[0].ivrs[0].name == "Main IVR"
    assert result.calls[0].ring_groups[0].status == "NO ANSWER"
    assert result.calls[0].call_flows[0].name == "Business Hours"
    assert result.calls[0].dids[0].number == "+33*******00"
    assert result.total_available == 2


def test_masking_suppresses_external_participant_names_but_keeps_internal_agents() -> None:
    external = YeastarService._party_from_fields(  # type: ignore[attr-defined]
        {
            "second_participant_name": "External Person",
            "second_participant_number": "+33123456789",
        },
        "second_participant",
        False,
    )
    internal = YeastarService._party_from_fields(  # type: ignore[attr-defined]
        {"second_participant_name": "Agent One", "second_participant_number": "1004"},
        "second_participant",
        False,
    )
    assert external is not None and external.name is None
    assert internal is not None and internal.name == "Agent One"


def test_v1_talk_duration_is_not_mislabeled_as_handling_duration() -> None:
    call = YeastarService._normalize_call(  # type: ignore[attr-defined]
        {
            "id": "legacy-1",
            "time": "2026/09/15 10:00:00",
            "call_type": "Internal",
            "src": "1000",
            "dst": "1004",
            "talk_duration": 42,
        },
        False,
    )
    assert call.talking_seconds == 42
    assert call.handling_seconds == 0


def test_masked_outbound_second_and_last_participant_names_are_suppressed() -> None:
    raw = {
        "uid": "outbound-1",
        "time": "2026/09/15 10:00:00",
        "call_type": "Outbound",
        "call_from_name": "Agent One",
        "call_from_number": "1004",
        "call_to_name": "External Person",
        "call_to_number": "+33123456789",
        "second_participant_name": "External Person",
        "second_participant_number": "1234",
        "last_participant_name": "External Redirect",
        "last_participant_number": "5678",
    }
    call = YeastarService._normalize_call(raw, False)  # type: ignore[attr-defined]
    assert call.second_participant is not None and call.second_participant.name is None
    assert call.last_participant is not None and call.last_participant.name is None


@pytest.mark.asyncio
async def test_call_detail_masks_each_leg_using_that_legs_call_type(fixture_json) -> None:
    payload = fixture_json("cdr_detail_v2.json")
    payload["data"]["timeline"].append(
        {
            "leg": 2,
            "time": "2026/09/15 10:04:00",
            "call_type": "Outbound",
            "status": "ANSWERED",
            "call_from": "Agent One<1004>",
            "call_to": "External Person<+33123456789>",
        }
    )
    client = AsyncMock()
    client.get.return_value = payload
    detail = await YeastarService(client).get_call_details("call-1")
    assert detail.timeline[1].caller.name == "Agent One"
    assert detail.timeline[1].callee.name is None


@pytest.mark.asyncio
async def test_get_calls_rejects_page_before_first(period: Period) -> None:
    with pytest.raises(ValueError, match="page must be >= 1"):
        await YeastarService(AsyncMock()).get_calls(period=period, page=0)


@pytest.mark.asyncio
async def test_v1_calls_omit_v2_parameters_and_reject_queue_filters(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), {"total_number": 0, "data": []}]
    service = YeastarService(client)
    await service.get_calls(period=period, api_version="v1.0")
    params = client.get.await_args_list[-1].kwargs["params"]
    assert "order_by" not in params and "queue_list" not in params
    with pytest.raises(ValueError, match="queue filters are not supported by CDR v1"):
        await service.get_calls(period=period, api_version="v1.0", queue_ids=[3])


@pytest.mark.asyncio
async def test_v2_calls_forward_documented_route_filters(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("cdr_v2.json")]
    await YeastarService(client).get_calls(
        period=period,
        call_from="1004",
        call_to="+33123450000",
        routing_duration=">=45",
        segments="3",
        disconnected_by="External",
    )
    params = client.get.await_args_list[-1].kwargs["params"]
    assert params["call_from"] == "1004"
    assert params["call_to"] == "+33123450000"
    assert params["routing_duration"] == ">=45"
    assert params["segments"] == "3"
    assert params["disconnected_by"] == "External"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("date_format", "timestamp"),
    [
        ("YYYY/MM/DD", "2026/09/15 10:03:44"),
        ("MM/DD/YYYY", "09/15/2026 10:03:44"),
        ("DD/MM/YYYY", "15/09/2026 10:03:44"),
    ],
)
async def test_cdr_response_timestamps_follow_pbx_date_format(
    period: Period, fixture_json, date_format: str, timestamp: str
) -> None:
    info = fixture_json("pbx_info.json")
    info["data"]["system_date_format"] = date_format
    cdr = fixture_json("cdr_v2.json")
    cdr["data"] = [cdr["data"][0]]
    cdr["total_number"] = 1
    cdr["data"][0]["time"] = timestamp
    client = AsyncMock()
    client.get.side_effect = [info, cdr]
    calls = await YeastarService(client).get_calls(period=period)
    assert calls.calls[0].started_at == datetime(2026, 9, 15, 10, 3, 44)


@pytest.mark.asyncio
async def test_invalid_cdr_timestamp_fails_instead_of_being_silently_skipped(
    period: Period, fixture_json
) -> None:
    cdr = fixture_json("cdr_v2.json")
    cdr["data"][0]["time"] = "not-a-pbx-time"
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), cdr]
    with pytest.raises(ValueError, match="invalid PBX timestamp"):
        await YeastarService(client).get_call_activity(period=period)


@pytest.mark.asyncio
async def test_call_stats_aggregate_locally_without_returning_cdr(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("cdr_v2.json")]
    service = YeastarService(client)
    result = await service.get_call_stats(period=period)
    assert result.calls.total == 2
    assert result.calls.answered == 1
    assert result.calls.abandoned == 1
    assert result.total_handling_seconds == 292
    assert not hasattr(result, "total_talk_seconds")
    assert not hasattr(result, "records")


@pytest.mark.asyncio
async def test_queue_wait_times_uses_documented_month_bucket(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("queue_wait_times.json")]
    service = YeastarService(client)
    result = await service.get_queue_wait_times(period=period, queue_ids=[3])
    assert result[0].buckets[0].average_wait_seconds == 28
    request = client.get.await_args_list[-1]
    assert request.kwargs["params"]["type"] == "queueavgwaittalktime"
    assert request.kwargs["params"]["time"] == "2026/09"
    assert [bucket.start for bucket in result[0].buckets] == [
        datetime(2026, 9, 9),
        datetime(2026, 9, 10),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("period", "expected"),
    [
        (
            Period(start=datetime(2026, 9, 15), end=datetime(2026, 9, 15, 23, 59, 59)),
            [datetime(2026, 9, 15, 9), datetime(2026, 9, 15, 10)],
        ),
        (
            Period(start=datetime(2026, 1, 1), end=datetime(2026, 12, 31, 23, 59, 59)),
            [datetime(2026, 9, 1), datetime(2026, 10, 1)],
        ),
    ],
)
async def test_queue_wait_times_normalize_day_and_year_buckets(
    period: Period, expected: list[datetime], fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("queue_wait_times.json")]
    result = await YeastarService(client).get_queue_wait_times(period=period, queue_ids=[3])
    assert [bucket.start for bucket in result[0].buckets] == expected


@pytest.mark.asyncio
async def test_agent_performance_normalizes_nested_details(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("agent_performance.json")]
    service = YeastarService(client)
    result = await service.get_agent_performance(period=period, queue_id=3)
    assert result[0].agent.number == "1004"
    assert result[0].calls.answered == 225
    assert result[0].agent.membership_type == "unknown"


@pytest.mark.asyncio
async def test_missed_calls_are_masked_and_abandoned_can_be_filtered(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("missed_calls.json")]
    service = YeastarService(client)
    missed = await service.get_missed_calls(period=period)
    assert missed.total_available == 2
    assert missed.calls[0].caller.number == "+33*******89"
    assert [call.status for call in missed.calls] == ["NO ANSWER", "BUSY"]


@pytest.mark.asyncio
async def test_missed_calls_filtered_total_has_no_phantom_page(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("missed_calls.json")]
    missed = await YeastarService(client).get_missed_calls(period=period, page=2, page_size=2)
    assert missed.total_available == 2
    assert missed.calls == []


@pytest.mark.asyncio
async def test_v1_abandoned_calls_use_documented_missed_call_report(
    period: Period, fixture_json
) -> None:
    report = fixture_json("missed_calls.json")
    report["unreturn_miss_call_list"] = [
        row for row in report["unreturn_miss_call_list"] if row["miss_call_type"] == "abandoned"
    ]
    report["total_number"] = 1
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), report]
    service = YeastarService(client)
    abandoned = await service.get_abandoned_calls(period=period, api_version="v1.0")
    assert abandoned.total_available == 1
    assert abandoned.calls[0].status == "ABANDONED"
    request = client.get.await_args_list[-1]
    assert request.kwargs["params"]["miss_call_type"] == "abandoned"
    with pytest.raises(ValueError, match="queue filters are not supported"):
        await service.get_abandoned_calls(period=period, api_version="v1.0", queue_ids=[3])


@pytest.mark.asyncio
async def test_call_detail_is_normalized_and_masked(fixture_json) -> None:
    client = AsyncMock()
    client.get.return_value = fixture_json("cdr_detail_v2.json")
    service = YeastarService(client)
    detail = await service.get_call_details("20260915100344A5466")
    assert detail.call.caller.number == "+33*******89"
    assert detail.timeline[0].status == "ANSWERED"
    assert detail.timeline[0].caller.number == "+33*******89"
    assert detail.timeline[0].caller.name is None
    assert detail.timeline[0].callee.number == "****"
    assert detail.timeline[0].duration_seconds == 300
    assert detail.timeline[0].talking_seconds == 292
    assert detail.timeline[0].started_at == datetime(2026, 9, 15, 10, 3, 44)
    assert [event.name for event in detail.timeline[0].events] == ["tried_contact", "hangup"]
    assert detail.timeline[0].events[0].type == "dial"
    assert detail.timeline[0].events[0].elapsed_seconds == 0
    assert detail.timeline[0].events[1].elapsed_seconds == 300
    assert not hasattr(detail.timeline[0].events[0], "raw_content")
    assert not hasattr(detail.timeline[0].events[0], "from_feature")
    assert not hasattr(detail.timeline[0].events[0], "to_feature")
    assert not hasattr(detail.timeline[0].events[0], "endpoint")
    assert not hasattr(detail.timeline[0].events[0], "operation")


@pytest.mark.asyncio
async def test_cdr_detail_timeline_has_a_fixed_safety_limit(fixture_json) -> None:
    payload = fixture_json("cdr_detail_v2.json")
    payload["data"]["timeline"] = payload["data"]["timeline"] * 1001
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), payload]
    with pytest.raises(ValueError, match="timeline exceeds the 1000-leg safety limit"):
        await YeastarService(client).get_call_details("call-1")


@pytest.mark.asyncio
async def test_call_stats_pages_through_all_cdr_without_returning_records(
    period: Period, fixture_json
) -> None:
    first = fixture_json("cdr_v2.json")
    first["total_number"] = 1001
    template = first["data"][0]
    first["data"] = [{**template, "uid": f"call-{index}"} for index in range(1000)]
    last = fixture_json("cdr_v2.json")
    last["total_number"] = 1001
    last["data"] = last["data"][1:]
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), first, last]
    service = YeastarService(client)
    result = await service.get_call_stats(period=period)
    assert result.calls.total == 1001
    assert result.calls.answered == 1000
    assert result.calls.abandoned == 1
    assert client.get.await_args_list[-1].kwargs["params"]["page"] == 2


@pytest.mark.asyncio
async def test_call_stats_has_a_fixed_complete_scan_limit(period: Period, fixture_json) -> None:
    cdr = fixture_json("cdr_v2.json")
    cdr["total_number"] = 100_001
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), cdr]
    with pytest.raises(ValueError, match="call statistics would process 100001 CDRs"):
        await YeastarService(client).get_call_stats(period=period)


@pytest.mark.asyncio
async def test_cdr_page_deduplicates_rows_before_returning_or_counting(
    period: Period, fixture_json
) -> None:
    cdr = fixture_json("cdr_v2.json")
    cdr["total_number"] = 1
    cdr["data"] = [cdr["data"][0], dict(cdr["data"][0])]
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), cdr]
    result = await YeastarService(client).get_call_stats(period=period)
    assert result.calls.total == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_page", "message"),
    [
        ({"total_number": 2, "data": []}, "premature empty page"),
        ({"total_number": 3, "data": []}, "total changed"),
    ],
)
async def test_cdr_pagination_fails_closed(
    period: Period, fixture_json, second_page: dict, message: str
) -> None:
    first = fixture_json("cdr_v2.json")
    first["total_number"] = 2
    first["data"] = [first["data"][0]]
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), first, second_page]
    with pytest.raises(ValueError, match=message):
        await YeastarService(client).get_call_stats(period=period)


@pytest.mark.asyncio
async def test_cdr_pagination_rejects_duplicate_ids_across_pages(
    period: Period, fixture_json
) -> None:
    first = fixture_json("cdr_v2.json")
    first["total_number"] = 2
    first["data"] = [first["data"][0]]
    second = fixture_json("cdr_v2.json")
    second["total_number"] = 2
    second["data"] = [second["data"][0]]
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), first, second]
    with pytest.raises(ValueError, match="duplicate CDR ID"):
        await YeastarService(client).get_call_stats(period=period)


@pytest.mark.asyncio
async def test_extension_call_stats_use_aggregate_call_report(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("extensions.json"),
        fixture_json("extension_call_stats.json"),
    ]
    service = YeastarService(client)
    stats = await service.get_call_stats(period=period, extension_ids=[73])
    assert stats.calls.total == 62
    assert stats.calls.answered == 40
    assert stats.calls.missed == 10
    assert stats.calls.abandoned == 2
    assert stats.total_talking_seconds == 588
    assert stats.total_handling_seconds == 0
    assert stats.source == "Yeastar Extension Call Statistics report (v2.0)"
    request = client.get.await_args_list[-1]
    assert request.kwargs["params"]["type"] == "extcallstatistics"
    assert request.kwargs["params"]["ext_id_list"] == "73"


@pytest.mark.asyncio
async def test_call_stats_fails_when_requested_extension_row_is_missing(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("extensions.json"),
        fixture_json("extension_call_stats.json"),
    ]
    with pytest.raises(ValueError, match="missing requested ext_num"):
        await YeastarService(client).get_call_stats(period=period, extension_ids=[73, 74])


@pytest.mark.asyncio
async def test_extension_performance_returns_one_named_row_per_collaborator(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("extensions.json"),
        fixture_json("extension_call_stats.json"),
    ]
    rows = await YeastarService(client).get_extension_performance(
        period=period, extension_ids=[73], communication_type="Inbound"
    )
    assert rows[0].extension == Extension(id=73, number="1000", name="Leo Ball")
    assert rows[0].calls.total == 62
    assert rows[0].total_talking_seconds == 588
    assert rows[0].average_talking_seconds == 14.7
    assert rows[0].communication_type == "Inbound"
    assert client.get.await_args.kwargs["params"]["communication_type"] == "Inbound"


@pytest.mark.asyncio
async def test_extension_performance_maps_rows_only_by_documented_extension_number(
    period: Period, fixture_json
) -> None:
    report = fixture_json("extension_call_stats.json")
    report["ext_call_statistics_list"][0].update({"ext_id": 999, "ext_num": "1000"})
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("extensions.json"),
        report,
    ]
    rows = await YeastarService(client).get_extension_performance(period=period, extension_ids=[73])
    assert rows[0].extension == Extension(id=73, number="1000", name="Leo Ball")


@pytest.mark.asyncio
async def test_extension_performance_fails_if_a_requested_extension_is_absent_from_report(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("extensions.json"),
        fixture_json("extension_call_stats.json"),
    ]
    with pytest.raises(ValueError, match="missing requested ext_num"):
        await YeastarService(client).get_extension_performance(
            period=period, extension_ids=[73, 74]
        )


@pytest.mark.asyncio
async def test_call_stats_rejects_queue_and_extension_filters_together(period: Period) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        await YeastarService(AsyncMock()).get_call_stats(
            period=period, queue_ids=[3], extension_ids=[73]
        )


@pytest.mark.asyncio
async def test_call_activity_aggregates_cdr_into_hour_buckets(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("cdr_v2.json")]
    activity = await YeastarService(client).get_call_activity(period=period, bucket="hour")
    assert activity.total_processed == 2
    assert len(activity.buckets) == 1
    assert activity.buckets[0].start == datetime(2026, 9, 15, 10)
    assert activity.buckets[0].calls.total == 2
    assert activity.buckets[0].calls.answered == 1
    assert activity.buckets[0].calls.abandoned == 1
    assert activity.buckets[0].total_duration_seconds == 365
    assert activity.buckets[0].total_handling_seconds == 292
    assert not hasattr(activity.buckets[0], "total_talk_seconds")
    assert activity.buckets[0].average_duration_seconds == 182.5
    assert activity.buckets[0].duration_bands == {"0-119": 1, "240-359": 1}
    assert activity.average_duration_seconds == 182.5
    assert activity.duration_bands == {"0-119": 1, "240-359": 1}


def test_call_activity_week_bucket_starts_on_monday() -> None:
    assert YeastarService._activity_bucket_start(datetime(2026, 9, 16, 14, 30), "week") == datetime(
        2026, 9, 14
    )


@pytest.mark.asyncio
async def test_call_activity_refuses_incomplete_aggregation(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("cdr_v2.json")]
    with pytest.raises(ValueError, match="narrower period"):
        await YeastarService(client).get_call_activity(period=period, max_records=1)


@pytest.mark.asyncio
async def test_routing_analysis_flags_segments_delays_and_route_changes(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("cdr_v2.json")]
    result = await YeastarService(client).get_routing_analysis(
        period=period,
        min_segments=3,
        long_routing_seconds=30,
        max_examples=10,
    )
    assert result.total_processed == 2
    assert result.counts.multi_segment == 1
    assert result.counts.at_or_above_min_segments == 1
    assert result.counts.loop_candidates == 0
    assert result.counts.long_routing == 1
    assert result.counts.route_changes == 1
    assert result.counts.voicemail == 0
    assert result.by_ivr == {"Main IVR": 1}
    assert result.by_queue == {"SAV": 2}
    assert {reason for item in result.examples for reason in item.reasons} >= {
        "segments>=3",
        "routing>=30s",
    }
    assert result.numbers_masked is True


@pytest.mark.asyncio
async def test_routing_analysis_computes_from_raw_then_masks_examples(
    period: Period, fixture_json
) -> None:
    cdr = fixture_json("cdr_v2.json")
    row = cdr["data"][0]
    row.update(
        {
            "call_type": "Outbound",
            "second_participant_name": "External A",
            "second_participant_number": "1234",
            "last_participant_name": "External B",
            "last_participant_number": "1234",
            "queues": [
                {"name": "SAV", "number": "6402"},
                {"name": "SAV", "number": "6402"},
            ],
        }
    )
    cdr["data"] = [row]
    cdr["total_number"] = 1
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), cdr]
    result = await YeastarService(client).get_routing_analysis(period=period, min_segments=3)
    assert result.counts.route_changes == 1
    assert result.counts.loop_candidates == 1
    assert result.examples[0].call.second_participant.name is None
    assert result.examples[0].call.last_participant.name is None


@pytest.mark.asyncio
async def test_routing_analysis_refuses_incomplete_scan(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("cdr_v2.json")]
    with pytest.raises(ValueError, match="narrower period"):
        await YeastarService(client).get_routing_analysis(period=period, max_records=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["get_call_activity", "get_routing_analysis", "get_contact_call_stats"]
)
async def test_mcp_controlled_record_limits_have_a_fixed_upper_bound(
    method: str, period: Period
) -> None:
    with pytest.raises(ValueError, match="must be between 1 and 100000"):
        await getattr(YeastarService(AsyncMock()), method)(period=period, max_records=100_001)


@pytest.mark.asyncio
async def test_routing_analysis_rejects_cdr_v1(period: Period) -> None:
    with pytest.raises(ValueError, match="requires CDR v2"):
        await YeastarService(AsyncMock()).get_routing_analysis(period=period, api_version="v1.0")


@pytest.mark.asyncio
async def test_contact_ivr_classification_rejects_cdr_v1(period: Period) -> None:
    with pytest.raises(ValueError, match="IVR classification requires CDR v2"):
        await YeastarService(AsyncMock()).get_contact_call_stats(period=period, api_version="v1.0")


@pytest.mark.asyncio
async def test_ivr_analysis_maps_keypresses_to_destinations_without_raw_callers(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("ivr_report.json"),
        fixture_json("ivr_detail.json"),
    ]
    result = await YeastarService(client).get_ivr_analysis(period=period, ivr_ids=[12])
    assert result[0].ivr.name == "Main IVR"
    assert result[0].unique_calls == 3
    assert result[0].press_counts == {"1": 2, "2": 1, "timeout": 1}
    assert result[0].destinations[0].key == "1"
    assert result[0].destinations[0].destination.name == "Sales"
    assert result[0].destinations[0].calls == 2
    assert result[0].calls == []
    assert client.get.await_args_list[-1].args[0] == "call_report/detail"


@pytest.mark.asyncio
async def test_ivr_analysis_returns_bounded_masked_call_examples(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("ivr_report.json"),
        fixture_json("ivr_detail.json"),
    ]
    result = await YeastarService(client).get_ivr_analysis(
        period=period, ivr_ids=[12], include_calls=True, max_call_examples=1
    )
    assert len(result[0].calls) == 1
    assert result[0].calls[0].caller.number == "+33*******89"
    assert result[0].calls[0].caller.name is None
    assert result[0].numbers_masked is True


@pytest.mark.asyncio
async def test_ivr_analysis_filters_first_keypress_after_complete_bounded_retrieval(
    period: Period, fixture_json
) -> None:
    first = fixture_json("ivr_detail.json")
    first["total_number"] = 4
    first["ivr_report_detail"][1]["id"] = "call-1"
    first["ivr_report_detail"].append(
        {
            "id": "call-4",
            "time": "2026/09/15 10:20:00",
            "call_from": "Client D<+33111111111>",
            "press": "2",
            "call_to": "External Person<+33222222222>",
            "destination_type": "External Number",
        }
    )
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("ivr_report.json"),
        first,
    ]
    result = await YeastarService(client).get_ivr_analysis(
        period=period,
        ivr_ids=[12],
        keys=["2"],
        first_keypress_only=True,
        include_calls=True,
    )
    assert result[0].press_counts == {"2": 2}
    assert result[0].unique_calls == 2
    assert result[0].calls[-1].destination.name is None
    assert "page" not in client.get.await_args_list[-1].kwargs["params"]


@pytest.mark.asyncio
async def test_ivr_detail_retrieval_has_a_fixed_safety_limit(period: Period, fixture_json) -> None:
    detail = fixture_json("ivr_detail.json")
    detail["total_number"] = 10_001
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("ivr_report.json"),
        detail,
    ]
    with pytest.raises(ValueError, match="IVR detail exceeds the 10000-row safety limit"):
        await YeastarService(client).get_ivr_analysis(period=period, ivr_ids=[12])


@pytest.mark.asyncio
async def test_contact_call_stats_crosses_directory_with_cdr_without_returning_raw_rows(
    period: Period, fixture_json
) -> None:
    cdr = fixture_json("cdr_v2.json")
    cdr["data"][0]["call_from_number"] = "+33123456789"
    cdr["data"][1]["call_from_number"] = "+33987654321"
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("company_contacts.json"),
        fixture_json("pbx_info.json"),
        cdr,
    ]
    result = await YeastarService(client).get_contact_call_stats(period=period)
    assert result.matched_calls == 2
    assert result.contacts[0].contact.name == "Contact 3"
    assert result.contacts[0].contact.company is None
    assert result.contacts[0].contact.numbers == ["+33*******89", "+33*******22"]
    assert result.contacts[0].inbound_calls == 1
    assert result.contacts[0].via_ivr_calls == 1
    assert result.contacts[0].without_ivr_calls == 0
    assert result.numbers_masked is True
    assert not hasattr(result, "cdr_records")


@pytest.mark.asyncio
async def test_agent_call_summary_includes_agent_names_and_inbound_outbound_totals(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("agent_call_summary.json"),
    ]
    result = await YeastarService(client).get_agent_call_summary(period=period, queue_id=3)
    assert result[0].agent.name == "Agent One"
    assert result[0].agent.number == "1004"
    assert result[0].queue_answered_calls == 40
    assert result[0].outbound_calls == 12
    assert result[0].total_talk_seconds == 4500
    assert client.get.await_args_list[-1].kwargs["params"]["type"] == "queueagentinoutcalls"


@pytest.mark.asyncio
async def test_v1_agent_summary_supports_documented_outbound_duration(
    period: Period, fixture_json
) -> None:
    report = fixture_json("agent_call_summary.json")
    row = report["queue_agent_in_out_calls_list"][0]
    row.pop("outbound_talk_duration")
    row["outbound_duration"] = 901
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), report]
    result = await YeastarService(client).get_agent_call_summary(
        period=period, queue_id=3, api_version="v1.0"
    )
    assert result[0].outbound_talk_seconds == 901


@pytest.mark.asyncio
async def test_queue_performance_exposes_official_cumulative_waiting_fields(
    period: Period, fixture_json
) -> None:
    report = fixture_json("queue_performance.json")
    row = report["queue_performance_list"][0]
    row.update({"answered_waiting_time": 29400, "total_waiting_time": 40800})
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), report]
    result = await YeastarService(client).get_queue_performance(period=period, queue_ids=[3])
    assert result[0].waiting.answered_total_seconds == 29400
    assert result[0].waiting.all_calls_total_seconds == 40800


@pytest.mark.asyncio
async def test_ring_group_stats_include_member_names(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("ring_group_stats.json")]
    result = await YeastarService(client).get_ring_group_stats(period=period, ring_group_ids=[7])
    assert result[0].ring_group.name == "Support Backup"
    assert result[0].calls.total == 100
    assert result[0].calls.answered == 80
    assert result[0].members[0].agent.name == "Agent One"
    assert result[0].members[0].answered_calls == 50


@pytest.mark.parametrize(
    ("model", "firmware", "reports", "cdr"),
    [
        ("P-Series Software Edition", "83.21.0.116", False, False),
        ("P-Series Software Edition", "83.21.0.117", True, False),
        ("P-Series Software Edition", "83.23.0.123", True, True),
        ("P-Series Appliance Edition", "37.21.0.117", True, False),
        ("P-Series Appliance Edition", "37.23.0.123", True, True),
        ("P-Series Cloud Edition", "84.21.0.117", True, False),
        ("P-Series Cloud Edition", "84.23.0.123", True, True),
    ],
)
def test_model_specific_report_and_cdr_v2_thresholds(
    model: str, firmware: str, reports: bool, cdr: bool
) -> None:
    assert YeastarService._supports_v2_call_reports(firmware, model) is reports
    assert YeastarService._supports_v2_cdr(firmware, model) is cdr


@pytest.mark.asyncio
async def test_capabilities_explain_basis_without_unprobed_extensions(fixture_json) -> None:
    client = AsyncMock()
    client.get.return_value = fixture_json("pbx_info.json")
    capabilities = await YeastarService(client).get_capabilities()
    by_name = {item.name: item for item in capabilities.capabilities}
    assert "extensions" not in by_name
    assert by_name["pbx_info"].basis == "probed"
    assert by_name["call_reports_current"].basis == "firmware"
