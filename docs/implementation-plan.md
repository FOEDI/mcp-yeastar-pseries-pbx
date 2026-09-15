# V1 implementation plan

1. Model configuration, periods, normalized PBX/queue/agent/call/report data, and explicit Yeastar v1/v2 endpoint routing.
2. Build a single async `YeastarClient` with OAuth token caching/refresh, Yeastar error handling, pagination, required User-Agent, and GET-only business operations.
3. Add services that normalize Yeastar Call Reports and CDR payloads and aggregate server-side before returning data whenever individual records are unnecessary.
4. Expose the twelve requested local stdio MCP tools plus a `yeastar-mcp doctor` CLI; no generic API proxy and no network MCP transport.
5. Develop with mocked documentation-shaped fixtures, opt-in live integration tests, ruff/pytest gates, and GitHub Actions; document credentials and Hermes connection.

## Documentation decisions

- Authentication uses `POST /openapi/v1.0/get_token`; subsequent read calls carry `access_token` and a required `User-Agent` header.
- Configuration/system/queue discovery uses documented v1 endpoints; CDR and Call Reports expose both v1 and v2 data paths because Yeastar separates historical and new CDR/report data.
- V2 CDR list/detail requires sufficiently recent PBX firmware; capability detection will report unsupported calls rather than silently guessing.
- Tool periods use ISO 8601 at the MCP boundary and are converted to the PBX-configured date/time display format for Yeastar.
- Aggregate report endpoints are preferred over raw CDR. Detail tools have explicit pagination/limits and privacy-safe number masking by default.
