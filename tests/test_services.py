from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from yeastar_mcp.models import Period
from yeastar_mcp.services import YeastarService


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
async def test_calls_mask_numbers_by_default(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("cdr_v2.json")]
    service = YeastarService(client)
    result = await service.get_calls(period=period)
    assert result.calls[0].caller.number == "+33*******89"
    assert result.calls[0].callee.number == "****"
    assert result.total_available == 2


@pytest.mark.asyncio
async def test_get_calls_rejects_page_before_first(period: Period) -> None:
    with pytest.raises(ValueError, match="page must be >= 1"):
        await YeastarService(AsyncMock()).get_calls(period=period, page=0)


@pytest.mark.asyncio
async def test_v1_calls_omit_v2_parameters_and_reject_queue_filters(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), {"data": []}]
    service = YeastarService(client)
    await service.get_calls(period=period, api_version="v1.0")
    params = client.get.await_args_list[-1].kwargs["params"]
    assert "order_by" not in params and "queue_list" not in params
    with pytest.raises(ValueError, match="queue filters are not supported by CDR v1"):
        await service.get_calls(period=period, api_version="v1.0", queue_ids=[3])


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
async def test_v1_abandoned_calls_use_documented_missed_call_report(
    period: Period, fixture_json
) -> None:
    client = AsyncMock()
    client.get.side_effect = [fixture_json("pbx_info.json"), fixture_json("missed_calls.json")]
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
    assert detail.timeline[0].caller.name == "Client"
    assert detail.timeline[0].callee.number == "****"
    assert detail.timeline[0].duration_seconds == 300
    assert detail.timeline[0].talking_seconds == 292
    assert detail.timeline[0].started_at == datetime(2026, 9, 15, 10, 3, 44)


@pytest.mark.asyncio
async def test_call_stats_pages_through_all_cdr_without_returning_records(
    period: Period, fixture_json
) -> None:
    first = fixture_json("cdr_v2.json")
    first["total_number"] = 1001
    first["data"] = first["data"][:1] * 1000
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
async def test_extension_call_stats_use_aggregate_call_report(period: Period, fixture_json) -> None:
    client = AsyncMock()
    client.get.side_effect = [
        fixture_json("pbx_info.json"),
        fixture_json("extension_call_stats.json"),
    ]
    service = YeastarService(client)
    stats = await service.get_call_stats(period=period, extension_ids=[73])
    assert stats.calls.total == 62
    assert stats.calls.answered == 40
    assert stats.calls.missed == 10
    assert stats.calls.abandoned == 2
    assert stats.source == "Yeastar Extension Call Statistics report (v2.0)"
    request = client.get.await_args_list[-1]
    assert request.kwargs["params"]["type"] == "extcallstatistics"
    assert request.kwargs["params"]["ext_id_list"] == "73"


@pytest.mark.asyncio
async def test_call_stats_rejects_queue_and_extension_filters_together(period: Period) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        await YeastarService(AsyncMock()).get_call_stats(
            period=period, queue_ids=[3], extension_ids=[73]
        )


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
