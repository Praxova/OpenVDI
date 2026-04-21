# OpenVDI Implementation Prompts — Milestone 1

Eight sequential prompts covering Milestone 1 ("it clones a VM through the provider interface") plus database scaffolding for M2+. Run them in order in Claude Code. Each prompt assumes Claude Code has read `CLAUDE.md` (automatic at repo root) and will read specific doc sections as needed.

| # | Subsystem | Files touched |
|---|-----------|---------------|
| 01a | Abstraction layer (hypervisor-agnostic) | `broker/pyproject.toml`, `broker/app/config.py`, `broker/app/providers/{__init__,base,exceptions}.py` |
| 01b | Proxmox-local plumbing | `broker/app/providers/proxmox/{__init__,exceptions,params,types}.py` |
| 02 | `_ProxmoxClient` core + `ProxmoxProvider` scaffold (cluster + task methods) | `broker/app/providers/proxmox/{client,provider,__init__}.py` |
| 03 | `ProxmoxProvider` VM lifecycle methods | `broker/app/providers/proxmox/provider.py` (extend) |
| 04 | `ProxmoxProvider` guest agent methods | `broker/app/providers/proxmox/provider.py` (extend) |
| 05 | `ProxmoxProvider` console ticket method | `broker/app/providers/proxmox/provider.py` (extend) |
| 06 | M1 acceptance test script (interface-only) | `broker/scripts/test_proxmox_provider.py` |
| 07 | DB schema + seed + drop + docker-compose | `db/*.sql`, `docker-compose.yml`, `.env.example`, `.gitignore` |

Prompt 07 is independent of 01a–06 and can be run in parallel.

## How to use

1. Open a fresh Claude Code session at the repo root (so `CLAUDE.md` is picked up automatically).
2. Paste the prompt file contents.
3. Review the plan Claude Code produces *before* letting it write files.
4. After the code lands, run the acceptance criteria listed in the prompt.

## Key rule

**Consumers of the abstraction — the test script, future services, future workers — import from `providers.base` and `providers.exceptions` only.** They construct providers through the registry (`get_provider_class("proxmox")`) and drive everything through `HypervisorProvider`. They do not import `ProxmoxProvider` or `_ProxmoxClient` directly. This is what we're protecting.

## When a prompt goes sideways

If Claude Code produces bad output (wrong API usage, invents parameters, ignores spec quirks, reaches into `providers.proxmox` from a service), the fix is usually one of:

- **It didn't actually consult `pve-spec-query`** — re-prompt with "use `pve_get_endpoint_detail` to verify the parameters for X before proceeding."
- **It didn't read the relevant doc section** — point it at the specific file + section by name.
- **It violated the dependency rule** — point it at `CLAUDE.md` → *Dependency Rules*.
- **The prompt is ambiguous** — fix the prompt, not the code. Come back to Claude Desktop to update it.
