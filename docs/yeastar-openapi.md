# Yeastar OpenAPI mapping

This document records the official contracts used by V1. All business calls are `GET`; the only `POST` calls are the documented OAuth token operations inside `YeastarClient`.

## Authentication

- `POST /openapi/v1.0/get_token`
  - JSON: `username` = Client ID, `password` = Client Secret.
  - Returns `access_token` (normally 1,800 seconds) and `refresh_token` (normally 86,400 seconds).
- `POST /openapi/v1.0/refresh_token`
  - JSON: latest `refresh_token`.
- Every request includes a non-empty `User-Agent` as required by Yeastar.
- Authorized calls send `access_token` as a query parameter because that is the documented contract.
- HTTP and network errors surface only status/path information and never include the token-bearing query string. A rejected refresh token is cleared and client credentials are retried once.

Sources: [Get Access Token](https://help.yeastar.com/en/p-series-software-edition/developer-guide/get-access-token.html), [Refresh Access Token](https://help.yeastar.com/en/p-series-software-edition/developer-guide/refresh-access-token.html), [Request Structure](https://help.yeastar.com/en/p-series-software-edition/developer-guide/request-structure.html).

## Read endpoints used

### PBX and configuration (v1.0)

- `GET /openapi/v1.0/system/information`
  - Normalized: device/model, firmware, PBX time, uptime, and date/time display format. The documented serial number is intentionally omitted as a persistent device identifier.
- `GET /openapi/v1.0/queue/list`
  - Parameters: `page`, `page_size`, `sort_by=number`, `order_by=asc`.
  - Normalized: queues and static/dynamic queue agents.

Sources: [PBX Information](https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-pbx-information.html), [Queue List](https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-queue-list.html).

### Call Reports (v1.0 or v2.0)

`GET /openapi/{version}/call_report/list`

Report types used:

- `extcallstatistics`
  - `start_time`, `end_time`, required `ext_id_list`.
  - Fields normalized: official `total_call_count`, answered, no-answer/missed, `abandoned_calls`, busy, failed, voicemail, and talking time.
- `queueperformance`
  - `start_time`, `end_time`, required `queue_id_list`; optional `abandon_time`.
  - Fields normalized: total/answered/missed/abandoned, average/max waits, average/total talk, hold, answer/miss/abandon rates, SLA, v1 `average_handle_time` or v2 `average_server_time`.
- `queueavgwaittalktime`
  - required `time` (complete day `YYYY/MM/DD`, month `YYYY/MM`, or year `YYYY`, reordered to the PBX date display format) and required `queue_id_list`.
  - Fields normalized: total/answered calls, answered/all-call average waits, average talk, and each response row's `time` as a distinct naive PBX-local hour/day/month bucket start.
- `queueagentperformance`
  - `start_time`, `end_time`, required `queue_id`; optional `agent_id_list`, `abandon_time`.
  - Fields normalized per nested agent detail. Membership is `unknown` because this response does not identify static or dynamic membership.
- `unreturnmisscall`
  - `start_time`, `end_time`; optional documented `miss_call_type` including `no_answer`, `busy`, `abandoned`.
  - The API does not document paging for this report, so pagination is applied locally after retrieval.

Source: [Query Call Report Statistics](https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-call-report-list.html).

### CDR

- `GET /openapi/v2.0/cdr/search`
  - `page`, `page_size` (documented max 10,000; MCP max 1,000), `time_begin`, `time_end`, `order_by=desc`, `sort_by=time`, optional `queue_list`, `last_status`.
  - V2 fields: `uid`, `time`, `call_type`, `last_status`, parties, queues, `call_duration`, `routing_duration`, `handling_duration`.
- `GET /openapi/v1.0/cdr/search`
  - `page`, `page_size`, `start_time`, `end_time`, optional legacy `status`. It does not send v2-only `order_by` or `queue_list`; queue filters are rejected for v1.
  - V1 fields are normalized from `duration`, `ring_duration`, `talk_duration`, `disposition`.
- `GET /openapi/v2.0/cdr/detail?uid=...`
  - Normalizes `basic` and each timeline leg. Call notes and unrelated raw fields are intentionally omitted.
- `GET /openapi/{v1.0|v2.0}/cdr/list?page=1&page_size=1`
  - Used by `doctor` only as a minimal read probe.

Sources: [CDR v1 search](https://help.yeastar.com/en/p-series-software-edition/developer-guide/search-specific-cdr.html), [CDR v2 search](https://help.yeastar.com/en/p-series-software-edition/developer-guide/search-specific-cdr-v2.html), [CDR v2 detail](https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-cdr-detail-v2.html).

## Version split

Call Reports v2 use model-specific boundaries: Software `83.21.0.117`, Appliance `37.21.0.117`, and Cloud `84.21.0.117`. Legacy report data remains under `openapi/v1.0` until removed by CDR auto-cleanup. Dedicated v2 CDR endpoints require later minimum firmware: Software `83.23.0.123`, Appliance `37.23.0.123`, Cloud `84.23.0.123`. Doctor selects report and CDR probe versions independently and sends no undocumented report paging parameters.

V1 intentionally makes `api_version` explicit on report/CDR tools. It does not guess or merge datasets. No unsupported endpoint or response shape is synthesized.

## Documentation irregularities handled

- V2 CDR `queues` is described as an object in one field table and appears as an array/null elsewhere; normalization accepts both.
- Some firmware versions/model strings may not follow the documented numeric pattern; `doctor` is the authoritative live capability check.
- Yeastar report times follow the PBX display format, not a fixed ISO format. MCP inputs must be naive PBX-local ISO 8601 values; timezone-aware periods are rejected rather than silently stripping offsets.
- Timeline `call_from` and `call_to` values use the documented combined `Name<number>` shape and are split into normalized parties; timeline `time` becomes `started_at`.
- Unreturned missed-call status preserves the documented `no_answer`, `busy`, and `abandoned` distinction as `NO ANSWER`, `BUSY`, and `ABANDONED`.
- The central client enforces a `(version, path)` GET allowlist, including the doctor-only v1/v2 `cdr/list` probes.
