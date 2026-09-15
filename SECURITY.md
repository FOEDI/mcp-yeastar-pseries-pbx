# Security policy

## Scope

This server is intentionally read-only:

- Business code calls only HTTP `GET` endpoints.
- OAuth token acquisition and refresh are the only internal `POST` requests.
- Twenty-two named tools are registered; no arbitrary URL/path/request tool exists.
- MCP V1 uses local stdio and opens no listening socket.
- MCP read-only/destructive annotations are advisory metadata; the structural endpoint/tool restrictions are the primary control.

## Secrets

Keep Yeastar credentials only in the untracked `.env` on the on-prem VM and set mode `0600`. Never commit `.env`, tokens, captured production payloads, call recordings, or PBX certificates containing private keys.

Yeastar documents query-string access tokens. Configure reverse proxies, HTTP debugging, and application observability so full query strings are not retained.

## Personal data

Telephone numbers are masked and external caller/callee names are suppressed by default. `include_numbers=true` is accepted by MCP tools only when the local runtime also sets `YEASTAR_ALLOW_RAW_NUMBERS=true`; the default is fail-closed. Agent and internal extension names remain available for operational statistics. Company contacts are pseudonymized as `Contact <id>` unless raw identity disclosure is enabled.

Aggregated tools should be used whenever record-level data is unnecessary. Local scans have explicit safety limits, detailed examples are bounded, and incomplete aggregates are rejected rather than silently truncated. The server excludes recordings, call notes, opaque event payloads, AI transcripts, PIN/account codes, and endpoint IP addresses.

Enabling raw values does not keep them on-prem if the connected model is remote. Use a local model and restrict outbound tools when data must not leave the VM.

## Reporting

Report vulnerabilities privately to the repository owner rather than opening an issue containing credentials or PBX data.
