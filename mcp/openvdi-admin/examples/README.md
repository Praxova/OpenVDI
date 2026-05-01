# Example MCP client configurations

Minimal snippets to drop into your AI agent's config. Replace
placeholder values (`broker.example.com`, `replace-me-secret`)
with real values for your deployment.

## Files

- `claude-desktop-config.json` — Claude Desktop. Merge the
  `mcpServers` block into your existing
  `~/Library/Application Support/Claude/claude_desktop_config.json`
  (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).
  If `mcpServers` already exists, add the `openvdi-admin` key
  alongside the others.

- `claude-code-config.toml` — Claude Code. Append to your
  `.claude/config.toml` (project-local) or `~/.claude/config.toml`
  (global).

## After configuring

1. Restart the agent (Claude Desktop / Code reads config at startup).
2. The agent should now have access to ~43 `openvdi_*` tools.
3. Try a quick smoke: ask the agent to "run the OpenVDI health check."
   It should call `openvdi_health_check` and return broker + cluster
   status.

## Don't commit your real config

These examples use placeholders. Don't replace them with real
credentials and commit the file. Use your secrets manager (1Password,
LastPass, AWS Secrets Manager, sops, etc.) for the real values.

## See also

- `docs/mcp.md` — comprehensive MCP documentation.
- `docs/deploy.md` → *MCP Server Deployment* — full env-var reference.
