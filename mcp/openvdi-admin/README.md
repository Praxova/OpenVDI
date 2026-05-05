# openvdi-admin MCP

Operational MCP server for OpenVDI. Enables AI agents to drive an
OpenVDI broker through 43 typed tools (37 thin wrappers + 6 intent
tools).

For operator-facing documentation, see [`docs/mcp.md`](../../docs/mcp.md).
For deployment, see [`docs/deploy.md`](../../docs/deploy.md) →
*MCP Server Deployment*.

This README is for developers extending or maintaining the MCP.

## Layout

```
mcp/openvdi-admin/
├── pyproject.toml
├── src/openvdi_admin/
│   ├── server.py            # FastMCP entry point + main()
│   ├── auth.py              # BrokerAuthClient (lazy login, refresh)
│   ├── client.py            # BrokerClient (verb helpers, envelope unwrap)
│   ├── config.py            # pydantic-settings env loader
│   ├── errors.py            # BrokerError + unwrap_envelope
│   ├── logging.py           # text/json formatter
│   ├── _request_context.py  # ContextVar for request_id
│   ├── _tool_wrapper.py     # @register_tool decorator + instrumentation
│   ├── tools/               # 37 thin-wrapper tools
│   │   ├── _common.py       # get_broker_client, require_writable, dry_run_envelope
│   │   ├── _polling.py      # wait_for_pool_terminal_state, etc.
│   │   ├── clusters.py
│   │   ├── templates.py
│   │   ├── entitlements.py
│   │   ├── pools.py
│   │   ├── dashboard.py
│   │   ├── audit.py
│   │   ├── desktops.py
│   │   ├── sessions.py
│   │   └── user_diagnostics.py
│   └── intent/              # 6 intent tools
│       ├── _result.py       # IntentResult + StepTracker
│       ├── smoke_test.py
│       ├── deploy_pool.py
│       ├── reset_environment.py
│       ├── diagnose_user.py
│       ├── diagnose_pool.py
│       └── health_check.py
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_client.py
│   ├── test_errors.py
│   ├── test_request_context.py
│   ├── test_tool_wrapper.py
│   ├── test_logging.py
│   ├── tools/
│   │   └── test_<resource>.py per tools/ file
│   └── intent/
│       └── test_<intent>.py per intent/ file
├── examples/
│   ├── claude-desktop-config.json
│   ├── claude-code-config.toml
│   └── README.md
└── scripts/
    ├── acceptance.sh        # M5 milestone gate
    └── README.md
```

## Setup

Python 3.10+ required (matches the broker's dev environment).

```bash
cd mcp/openvdi-admin
pip install -e ".[dev]"
```

If `mcp[cli]` install stalls (transitive-dep heaviness on constrained
hosts), see `docs/deploy.md` → *MCP Server Deployment* for the
fallback pattern.

## Running

```bash
# Set required env vars (see docs/deploy.md for the full list)
export OPENVDI_BROKER_URL=http://localhost:8080
export OPENVDI_SERVICE_USER=admin
export OPENVDI_SERVICE_PASSWORD=admin
export OPENVDI_VERIFY_SSL=false

# Run via the entry point
openvdi-admin

# Or via Python
python -m openvdi_admin.server
```

The MCP speaks MCP-protocol-over-stdio. It hangs waiting for input
on stdin; the agent client (Claude Desktop, Code, etc.) drives it.

For interactive testing without a real client, see
`scripts/acceptance.sh`.

## Tests

```bash
pytest tests/ -v
```

Test count after M5-09b: ~284 unit tests across the package. All
mocked; no live broker required.

For tests against a real broker, run the acceptance script:

```bash
./scripts/acceptance.sh
```

## Adding a new tool

### Thin wrapper

1. Add the function to the appropriate `tools/<resource>.py` file.
   Naming: `openvdi_<verb>_<resource>`.
2. Decorate with `@register_tool()`.
3. If destructive, call `require_writable("openvdi_<...>")` at the
   top of the body and implement the `confirm: bool = False` /
   dry-run / execute split.
4. Use `dry_run_envelope(...)` from `tools/_common.py` for preview
   shape.
5. Return raw broker data (lists, dicts) — the MCP framework
   serializes to JSON.
6. Add unit tests to `tests/tools/test_<resource>.py` using the
   `monkeypatch.setattr` pattern to inject a mock client.

### Intent tool

1. Create `intent/<name>.py`.
2. Decorate with `@register_tool()`.
3. Compose thin wrappers — call them as regular Python coroutines.
   Pass `confirm=True` explicitly to thin wrappers' destructive
   calls (otherwise you'd just collect dry-run previews).
4. Use `StepTracker` from `intent/_result.py` for per-step timing
   and structured failure surfaces.
5. Wrap the orchestration in `try/except BrokerError` and return
   `tracker.failure_result(...)` on error.
6. Raise BrokerErrors INSIDE step contexts (not after) so
   `last_failed_step()` attributes correctly.
7. Add tests with mocked thin wrappers.

### Server registration

Add an import line at the bottom of `server.py`:

```python
import openvdi_admin.tools.<new_module>     # noqa: E402, F401
# or
import openvdi_admin.intent.<new_module>    # noqa: E402, F401
```

The `@register_tool()` decorator side-effects the registration onto
the FastMCP `mcp` singleton.

## Conventions

### Decorators

The `@register_tool()` decorator from `_tool_wrapper.py` combines
`@mcp.tool()` and `@instrument_tool` into one. Use it for every new
tool — gets logging + request_id propagation for free.

```python
from openvdi_admin._tool_wrapper import register_tool

@register_tool()
async def openvdi_my_new_thing(...) -> dict:
    ...
```

### Error handling

Thin wrappers raise `BrokerError` (typed exception with `code`,
`http_status`, `message`, optional `details`). The instrumentation
decorator catches and logs them; FastMCP surfaces them in the tool
result.

Intent tools wrap their orchestration in `try/except BrokerError` and
return a structured `failure_result` envelope. The agent never sees
a raised exception from intent tools — only the envelope.

### Read-only mode

Every destructive tool calls `require_writable("openvdi_<name>")`
at the top of its body. Read tools (list, get, dashboard, audit,
diagnose) do NOT call it.

The decision is per-tool, declared in code. There's no automatic
"infer destructive from name" — explicit over implicit.

### Dry-run / confirm

Destructive tools take `confirm: bool = False` as their LAST
positional parameter. With `confirm=False`, return a dry-run preview
via `dry_run_envelope(...)`. With `confirm=True`, execute.

### Polling for long ops

Use `wait_for_pool_terminal_state` / `wait_for_desktop_terminal_state`
from `tools/_polling.py`. Predicates (e.g. `pool_provision_terminal`)
are factored functions; new operations get their own predicates.

### Tests

Mock at the boundary — patch `tools._common.get_broker_client` to
return an `AsyncMock`. Verify both the broker calls and the tool's
output shape. See `tests/tools/test_clusters.py` for the canonical
pattern.

For intent tools, mock the thin wrappers themselves:
```python
monkeypatch.setattr(
    "openvdi_admin.intent.<name>.openvdi_<wrapper>",
    AsyncMock(return_value=...),
)
```

## Implementation feedback to honor

These came from M5-04/05/06/07/08 implementation:

- **Use `mcp[cli]`, not standalone `fastmcp`.** Import:
  `from mcp.server.fastmcp import FastMCP`.
- **`PUT /pools/{id}` rejects `status` field.** Update tools must NOT
  include `status` in PUT bodies.
- **Drain is one-way.** No automatic flip from `draining` to
  `disabled`. Predicates use sessions-only signal.
- **`POST /pools/{id}/provision` requires `count: int` (1-50).** No
  "fill to min_spare" path at the API layer.
- **Pool capacity shape:** `pool["capacity"]["total_desktops"]` (from
  `get_pool`) vs `summary["capacity"]["total"]` (from
  `get_pool_summary`). Different sources, different field names.
- **`POST /desktops/{id}/assign` accepts `username` only.** No
  `assignment_type` body field — broker derives from
  `pool.pool_type`.
- **`POST /desktops/{id}/rebuild` rejects with CONFLICT when active
  session exists.** Workflows that rebuild must force-disconnect
  first.
- **`DELETE /desktops/{id}` is 202-Accepted async**, returns a
  `TaskAccepted` body. Not 204.
- **Force-disconnect IS synchronous.** No settle-sleep needed before
  follow-up actions.
- **Intent-tool failures must raise INSIDE the step context** so
  `last_failed_step()` attributes correctly.
- **Don't log tool args, ever.** Sensitive data risk; redaction is
  brittle. Broker audit_log has redacted args.

These are the gotchas that bit the implementation. Don't relearn
them.

## License

Apache-2.0 (per `pyproject.toml`, matching the project-root
`LICENSE`). Praxova captures revenue at the agent layer, not the
MCP layer; permissive licensing on the broker + MCP keeps the
foundation friction-free for customer extension and aligns with the
license used by Praxova's IT Agent.

## See also

- `docs/mcp.md` — operator-facing MCP doc.
- `docs/deploy.md` → *MCP Server Deployment* — installation and env
  vars.
- `docs/architecture.md` → *MCP Surface* — system layering.
- `docs/prompts/m5-*.md` — implementation prompts for every M5
  milestone. Read these before writing M6+ prompts that touch the
  MCP.
- `docs/prompts/m5-planning-seed.md` — the design seed and
  implementation feedback log.
