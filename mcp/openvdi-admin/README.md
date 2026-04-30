# openvdi-admin MCP server

MCP server that exposes the OpenVDI broker to AI agents. Thin-wrapper
tools cover the full admin surface; intent tools layer domain
knowledge on top of the wrappers. v0 ships with the Praxova IT Agent
and Claude Desktop / Claude Code as primary consumers.

This README is the developer / operator quickstart. The full doc —
architecture, tool catalog, troubleshooting — lives in
`docs/mcp.md` (lands in M5-09).

## Status

M5-02 ships the package scaffold and broker auth client. Zero tools
registered yet; the server starts and idles. Subsequent prompts add:

- M5-03: clusters / templates / entitlements thin wrappers
- M5-04: pools / dashboard / audit thin wrappers
- M5-05: desktops / sessions / user-diagnostics / console thin wrappers
- M5-06: testing intent tools (smoke_test, deploy_pool, reset_test_environment)
- M5-07: diagnosis intent tools (diagnose_user, diagnose_pool, health_check)
- M5-08: logging + request-id propagation
- M5-09: docs + acceptance gate

## Install

From the monorepo root:

```bash
cd mcp/openvdi-admin
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

The MCP loads its config from environment variables (or a `.env` file
in the working directory). Copy the template and fill in:

```bash
cp .env.example .env
# Edit .env — at minimum:
#   OPENVDI_BROKER_URL=https://openvdi.example.com
#   OPENVDI_SERVICE_USER=openvdi-mcp-svc
#   OPENVDI_SERVICE_PASSWORD=<password>
```

The service account must be a regular AD user that's a member of
`OPENVDI_LDAP_ADMIN_GROUP` on the broker side. The MCP authenticates
the same way an admin authenticates from the portal — `POST /auth/login`
with username + password.

## Run

```bash
openvdi-admin
# or
python -m openvdi_admin.server
```

The server speaks MCP over stdio (FastMCP default). Launched
standalone, it hangs on stdin waiting for the parent agent process.
Real usage is via Claude Desktop / Claude Code config that spawns the
binary as a subprocess; example configs land in M5-09.

## Test

```bash
pytest tests/
ruff check src/ tests/
```

## Architecture

- `auth.py` — `BrokerAuthClient`. Lazy login on first call; tracks
  access token + refresh cookie; concurrent-refresh dedup via
  in-flight promise.
- `client.py` — `BrokerClient`. Verb-shaped helpers (`get`, `post`,
  `put`, `delete`) that unwrap the broker's `{data, error}` envelope.
  401 → refresh + replay once.
- `errors.py` — `BrokerError` + envelope unwrapping.
- `config.py` — pydantic-settings env-var loader.
- `logging.py` — text/json formatter swap.
- `server.py` — FastMCP entry point.
- `tools/` (M5-03+) — one file per resource; `@mcp.tool()` decorators
  self-register at import.

## Security notes

- The MCP holds long-lived service-account credentials. Treat the
  `.env` like a deployment secret.
- Every action the MCP takes is attributed in the broker's audit log
  to the service-account username. Anything an agent does is
  attributable to the service account, not to whoever's interacting
  with the agent.
- `OPENVDI_MCP_READ_ONLY=true` disables every destructive tool.
  Default false.

## Source

This package is part of the [OpenVDI monorepo](https://github.com/praxova/openvdi). MIT-licensed broker; this MCP is GPL-3.0 to keep the agent
ecosystem in the open.
