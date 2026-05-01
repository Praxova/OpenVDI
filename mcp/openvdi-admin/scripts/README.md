# MCP scripts

Helper shell scripts for the openvdi-admin MCP.

## acceptance.sh

The M5 milestone gate. Walks the full MCP tool catalog end-to-end
against a live broker. Run before tagging `m5-complete`.

### Prerequisites

- Broker reachable at `OPENVDI_BROKER_URL`.
- Service-account credentials work (`OPENVDI_SERVICE_USER`,
  `OPENVDI_SERVICE_PASSWORD`).
- At least one cluster + template registered. Pass their UUIDs as
  `CLUSTER_ID` and `TEMPLATE_ID`.
- VMID range 9000-9019 unclaimed by other pools.

### Run

```bash
export OPENVDI_BROKER_URL=https://broker.example.com
export OPENVDI_SERVICE_USER=openvdi-mcp-svc
export OPENVDI_SERVICE_PASSWORD=...
export OPENVDI_VERIFY_SSL=true
export CLUSTER_ID=<uuid>
export TEMPLATE_ID=<uuid>
export TEST_USER=alice  # optional; defaults to OPENVDI_SERVICE_USER

./scripts/acceptance.sh
```

### Pass criteria

- Health check succeeds with at least one cluster active.
- Pre-reset succeeds (cleans up any stale m5acc- pools from prior runs).
- Deploy creates a pool, grants entitlement, pre-provisions 2 desktops.
- Smoke test verifies a desktop.
- Diagnose pool returns health=healthy or health=degraded with a
  reasonable issues list.
- Diagnose user returns ≥1 directly-entitled pool.
- Final reset cleans up everything.

Exit 0 on full pass; non-zero on any failure with the failed step
labeled.
