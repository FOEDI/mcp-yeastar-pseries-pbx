# yeastar-mcp

A local, strictly read-only MCP server for analytics from one Yeastar P-Series PBX. It is intended to run over **stdio** on the same on-prem Linux VM as Hermes Agent. No MCP HTTP endpoint is created.

> Status: V1 is complete against documentation-shaped mocks. Live PBX validation remains opt-in until Yeastar Client ID / Client Secret credentials are available.

## Design goals

- Python 3.12+, `uv`, official stable MCP Python SDK 2.x, async `httpx`, `pydantic-settings`.
- One central `YeastarClient` for HTTP, OAuth token acquisition/refresh, error handling, and API-version routing.
- Business services return normalized Pydantic models rather than raw Yeastar field names.
- MCP tools expose only bounded read operations; there is no generic API proxy.
- Aggregate reports are preferred. Call-level tools mask telephone numbers by default.
- Unit and MCP protocol tests need no PBX. Live tests are marked `integration` and skip without credentials.

## Tool surface

All tools carry MCP annotations `readOnlyHint=true`, `destructiveHint=false`, and `idempotentHint=true`.

- `get_pbx_info` — model, firmware, PBX clock, and uptime.
- `get_capabilities` — v1/v2 analytics support with an explicit probed, firmware, or documented basis.
- `list_queues`, `list_agents` — queues and named queue agents.
- `list_ivrs`, `list_extensions`, `list_ring_groups` — minimal read-only directories needed to discover report IDs; sensitive configuration fields are omitted.
- `get_call_stats` — bounded aggregate call counts/durations. Official extension talking time and CDR `handling_duration` are exposed under distinct field names; source CDRs are never returned to the LLM.
- `get_call_activity` — local hourly, daily, weekly, or monthly volume/status/direction aggregation, with configurable duration bands.
- `get_extension_performance` — one named statistics/talk-time row per extension, optionally filtered by inbound, outbound, or internal traffic.
- `get_queue_performance`, `get_queue_wait_times` — queue totals, rates, SLA, wait/talk metrics, and documented time buckets.
- `get_agent_performance`, `get_agent_call_summary` — named per-agent queue performance and inbound/outbound/talk totals.
- `get_ivr_analysis` — IVR press counts and destinations, with key filtering and first-keypress mode; complete detail retrieval and optional call examples are bounded.
- `get_ring_group_stats` — ring-group totals and named member metrics.
- `get_routing_analysis` — v2-only local counts for long routing, multi-leg calls, route changes, and loop candidates that require a repeated destination.
- `get_contact_call_stats` — locally joins the Yeastar company directory to CDRs and returns contact-level aggregates rather than raw CDR rows.
- `get_missed_calls`, `get_abandoned_calls`, `get_calls` — bounded call lists, masked by default; v2 call search also supports caller, called number, routing-duration, segment-count, and disconnect-party filters.
- `get_call_details` — v2 CDR basic data, timeline legs, and documented top-level event metadata; opaque event payloads are never parsed or returned.

Period arguments are inclusive ISO 8601 `start` and `end` values and must be naive PBX-local datetimes without a timezone offset. Timezone-aware values are rejected because the PBX API accepts display-formatted local times but exposes no timezone conversion contract.

## Architecture

```text
src/yeastar_mcp/
├── cli.py          # `yeastar-mcp serve|doctor`
├── client.py       # async HTTP + centralized OAuth token lifecycle
├── doctor.py       # local/live read-only diagnostics
├── endpoints.py    # documented endpoint/version registry
├── models.py       # normalized MCP-facing Pydantic models
├── server.py       # 22 explicit MCP tools, stdio only
├── services.py     # reports, CDR aggregation, normalization, privacy
└── settings.py     # YEASTAR_* environment configuration

tests/
├── fixtures/       # realistic official-documentation-shaped payloads
├── integration/    # opt-in live PBX probes
└── test_*.py       # unit, normalization, auth, doctor, MCP protocol tests
```

The dependency direction is `MCP tools -> services -> YeastarClient -> PBX`. Tools never construct URLs and the client has no MCP-exposed generic request operation.

## Install

```bash
git clone https://github.com/FOEDI/mcp-yeastar-pseries-pbx.git
cd mcp-yeastar-pseries-pbx
uv sync --locked --dev
```

`uv` will install a compatible Python 3.12 interpreter when necessary.

## Configuration

```bash
cp .env.example .env
chmod 600 .env
```

Edit the untracked `.env`:

```dotenv
YEASTAR_BASE_URL=https://192.168.1.50:8088
YEASTAR_CLIENT_ID=replace-with-openapi-client-id
YEASTAR_CLIENT_SECRET=replace-with-openapi-client-secret
YEASTAR_VERIFY_SSL=true
YEASTAR_ALLOW_RAW_NUMBERS=false
```

`YEASTAR_BASE_URL` is the PBX web origin, without `/openapi`. Keep TLS verification enabled. For a private CA, install its CA certificate in the VM trust store. Set `YEASTAR_VERIFY_SSL=false` only as a temporary diagnostic on a trusted network.

The `.env` file, tokens, and credentials are ignored by Git. Tokens live only in process memory. Because Yeastar carries `access_token` in the query string, do not enable full request-URL logging in production.

### Obtain Yeastar credentials later

In the PBX administrator portal, enable OpenAPI and create an API application with the smallest read permissions required for System, Queue, Call Reports, and CDR. Copy its **Client ID** and **Client Secret** into `.env`; the API maps these to the token request's `username` and `password`. See Yeastar's official [Enable API](https://help.yeastar.com/en/p-series-software-edition/developer-guide/enable-yeastar-p-series-pbx-api.html) and [Get Access Token](https://help.yeastar.com/en/p-series-software-edition/developer-guide/get-access-token.html) pages.

## Doctor

Without credentials, the command succeeds and marks every live PBX probe `SKIP`:

```bash
uv run yeastar-mcp doctor
uv run yeastar-mcp doctor --json
```

With credentials it checks, using read-only requests:

1. PBX connection and OAuth authentication;
2. firmware/version information;
3. Call Reports;
4. CDR using the detected v1/v2 generation;
5. queue listing;
6. normalized capabilities.

A failed live probe makes `doctor` exit non-zero.

## Run locally

```bash
uv run yeastar-mcp
# equivalent:
uv run yeastar-mcp serve
```

Both commands start stdio transport. Do not type into the process manually: stdout is reserved for MCP JSON-RPC.

## Connect to Hermes Agent

Use the target Hermes profile's CLI rather than hand-editing YAML. Replace the repository path with an absolute path and keep `--args` last:

```bash
hermes mcp add yeastar-mcp \
  --command "$(command -v uv)" \
  --connect-timeout 60 \
  --args run --directory /absolute/path/to/mcp-yeastar-pseries-pbx yeastar-mcp

hermes mcp test yeastar-mcp
```

Then start/enter Hermes and reload MCP:

```text
/reload-mcp
```

Hermes filters stdio subprocess environments, so the recommended setup lets `pydantic-settings` read the project-local, untracked `.env` after `uv --directory` selects the repository. No Yeastar secret is copied into Hermes configuration.

For a named profile, run the corresponding profile launcher/CLI for `mcp add` and `mcp test`; profiles have separate configuration and secret scopes. Current commands and filtering behavior are documented in [Use MCP with Hermes](https://hermes-agent.nousresearch.com/docs/guides/use-mcp-with-hermes) and the [MCP config reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference).

## Development and verification

```bash
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not integration"
```

Live read-only integration probe:

```bash
set -a
. ./.env
set +a
uv run pytest -m integration -v
```

The integration test is skipped unless the required connection credentials are available. It calls only normalized read services.

## Yeastar API versions

Yeastar separates historical and current CDR/Call Report datasets after the CDR data-structure upgrade. This project therefore routes per operation rather than applying one global API version:

- Authentication, PBX information, queues: `openapi/v1.0`.
- Call Reports: selectable `v1.0` or `v2.0` (default `v2.0`).
- CDR search: selectable `v1.0` or `v2.0` (default `v2.0`).
- CDR detail/timeline: documented v2 endpoint only.

Call Reports v2 begin at Software `83.21.0.117`, Appliance `37.21.0.117`, and Cloud `84.21.0.117`. Dedicated CDR v2 endpoints have later minimums: Software `83.23.0.123`, Appliance `37.23.0.123`, and Cloud `84.23.0.123`. `get_capabilities` identifies firmware-inferred results explicitly; unknown version formats are treated conservatively and should be confirmed by `doctor`.

See [docs/yeastar-openapi.md](docs/yeastar-openapi.md) for exact paths, parameters used, report types, and source links. The complete mapping from the supplied Excel sheet to MCP tools, business-reference requirements, and explicit limits is in [docs/workbook-coverage.md](docs/workbook-coverage.md).

## Privacy model

- Queue, agent, IVR, contact, routing, activity, and report tools aggregate locally whenever record-level data is unnecessary.
- Telephone numbers are masked and external caller/callee names are suppressed by default.
- `include_numbers=true` is rejected at the MCP boundary unless the runtime also has `YEASTAR_ALLOW_RAW_NUMBERS=true`; both controls must be explicit.
- Agent and internal extension names remain available for operational performance reports.
- Company-contact results use `Contact <id>` pseudonyms unless raw identity disclosure is enabled.
- Call/IVR examples, detail retrieval, directories, and local CDR scans have fixed limits and fail rather than returning silently incomplete statistics.
- Opaque Yeastar `event_content`, recordings, audio, transcripts, call notes, PINs, account codes, IP addresses, and arbitrary raw payloads are never exposed.
- No MCP network listener exists in V1.

Only enable raw numbers in an approved reporting environment. A cloud LLM necessarily receives any raw values returned to it; use a local model if telephone identities must not leave the VM.

## Known limitations

- Real payloads have not yet been validated against this specific PBX because credentials are unavailable.
- Cross-upgrade date ranges may require one v1 and one v2 request and deduplication; V1 exposes the API-version choice but does not automatically merge both datasets.
- Queue AVG Wait & Talk Time only supports complete day/month/year buckets because that is the documented API contract.
- Queue AVG response rows are normalized to distinct naive PBX-local bucket starts (hour, day, or month according to the requested period).
- Agent Performance does not expose static/dynamic membership, so those rows report `membership_type="unknown"`; `list_agents` still derives membership from Queue List.
- CDR v1 does not support queue filtering; requests that specify `queue_ids` with `api_version="v1.0"` are rejected.
- V2 CDR documentation is inconsistent about whether `queues` is an object or array; normalization accepts both and currently selects the first queue for the compact call model.
- `get_call_details` is v2-only. Legacy v1 CDR has no equivalent documented detail/timeline endpoint.
- PBX-local time is used; response timestamps are parsed with the configured PBX date format, while V1 does not infer a timezone name from display settings.

## Official references

- [Yeastar P-Series Software Edition API summary](https://help.yeastar.com/en/p-series-software-edition/developer-guide/api-interfaces-and-events-summary.html)
- [Yeastar authorization](https://help.yeastar.com/en/p-series-software-edition/developer-guide/authorization-rule.html)
- [Yeastar Call Report Statistics](https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-call-report-list.html)
- [Yeastar CDR v2 list](https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-cdr-list-v2.html)
- [Yeastar CDR v2 search](https://help.yeastar.com/en/p-series-software-edition/developer-guide/search-specific-cdr-v2.html)
- [Yeastar CDR v2 detail](https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-cdr-detail-v2.html)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v2.2.0)
- [MCP Python SDK testing guide](https://py.sdk.modelcontextprotocol.io/get-started/testing/)

## License

MIT — see [LICENSE](LICENSE).
