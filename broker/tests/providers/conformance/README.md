# OpenVDI Provider Conformance Suite

Pytest-driven test suite that asserts a `HypervisorProvider`
implementation conforms to the contract defined in
`broker/app/providers/base.py`. Run against a live cluster — NOT
unit tests; NOT mocked.

## Why

OpenVDI v0 ships one provider (Proxmox); future providers (vSphere,
Hyper-V, etc.) must pass this same suite to be merged. The suite
acts as the cross-provider contract enforcer.

It also serves as a regression suite during M4 worker development:
running `pytest tests/providers/conformance/ --provider=proxmox`
after a worker prompt catches Proxmox provider regressions before
they ship.

## Prerequisites

1. **A Proxmox cluster** dedicated to testing (or a non-prod cluster
   you're willing to leave artifacts on). Production clusters are
   NOT acceptable — the suite creates and destroys VMs.

2. **A test user / API token** with permissions to:
   - Clone VMs from the test template
   - Start / stop / shutdown / reboot VMs
   - Configure VMs (cpu, memory)
   - Create / rollback / delete snapshots
   - Destroy VMs (purge)
   - Read VM and node status
   - Query the guest agent

3. **A test template VM** (registered in Proxmox as a template via
   `qm template <vmid>`) with:
   - Working OS (Linux or Windows)
   - QEMU guest agent installed and enabled (`agent: 1` in VM config)
   - Auto-login DISABLED (the `agent_get_users` clean-boot test
     expects an empty user list)

4. **A reserved VMID range** for the suite — typically 100 IDs
   (e.g. 9100-9199). The suite allocates sequentially within the
   range. If the range is exhausted, the next test fails clearly.

## Setup

```bash
# Copy the example config and fill in cluster credentials.
cd broker
cp tests/conformance.yaml.example tests/conformance.yaml
# Edit tests/conformance.yaml — fill in api_url, token_id,
# token_secret, default_node, test_template_vmid,
# test_pool_vmid_range.
```

## Running

```bash
# Run the full suite against Proxmox.
pytest tests/providers/conformance/ --provider=proxmox -v

# Run one test file.
pytest tests/providers/conformance/test_lifecycle.py --provider=proxmox -v

# Run with extra logging.
pytest tests/providers/conformance/ --provider=proxmox -v -s --log-cli-level=DEBUG
```

The default `pytest tests/` does NOT run the conformance suite —
it's opt-in via `--provider`. This is by design: the suite needs a
live cluster and is too slow + heavy for CI.

## Cleanup of stragglers

The suite uses best-effort cleanup. A test that fails mid-VM
lifecycle may leave a VM behind. To clean up:

```bash
# List leaked VMs in the test range.
ssh root@pve1 "qm list | awk '\$1 >= 9100 && \$1 <= 9199'"

# Destroy them (after verifying!).
ssh root@pve1 "for vmid in \$(qm list | awk '\$1 >= 9100 && \$1 <= 9199 {print \$1}'); do
  qm stop \$vmid 2>/dev/null
  qm destroy \$vmid --purge 1 2>/dev/null
done"
```

If the suite repeatedly leaks VMs in the same place, that's a real
test bug — file an issue, don't just clean up and re-run.

## Skipping

Tests skip gracefully when the provider declares the capability is
absent. Snapshot tests skip on providers without
`capabilities().snapshots`; guest-agent tests skip without
`capabilities().guest_agent`.

If `tests/conformance.yaml` is missing or doesn't have a block for
`--provider=...`, the entire suite skips with a clear message.

## CI

Not run in CI for v0 — live cluster requirement. M5+ may add a
hosted test cluster + run the suite on PRs that touch
`broker/app/providers/`.

## Adding a new provider

When `vsphere`, `hyperv`, etc. arrive:

1. Add a `<provider>` block to `tests/conformance.yaml.example`
   with the provider's required fields.
2. Extend `_make_vm_ref` in `conftest.py` with a branch for the new
   `provider_type`, returning a `VMRef.data` shape that matches the
   new provider's convention (Proxmox uses `{"node", "vmid"}`;
   vSphere will likely use a MoRef string, etc.).
3. If the new provider's `__init__` takes different kwargs than
   `(api_url, token_id, token_secret, verify_ssl)`, extend the
   `provider` fixture to dispatch on `provider_type`.
4. Run the suite. The provider must pass every test that's not
   skipped by capabilities.

The test bodies stay unchanged — they import only the abstract
Protocol + shared types from `app.providers.base`, never the
concrete provider.
