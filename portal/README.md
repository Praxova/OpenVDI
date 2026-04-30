# OpenVDI Portal

The OpenVDI web portal — a Vite + React + TypeScript SPA that consumes
the broker's `/api/v1/*` surface and renders the user-facing console.

## Quick start

```bash
# from the OpenVDI repo root
cd portal
pnpm install
pnpm dev
```

Then open <http://localhost:5173/>.

The dev server proxies `/api/*` to the broker on `:8080`. Make sure the
broker is running (see ../broker/README.md or the project root README).

## Stack notes

- **Package manager: pnpm.** Faster, smaller `node_modules`, and refuses
  imports of phantom dependencies (catches a class of bugs npm allows).
- **Tailwind v3 with the Praxova design-token theme bridge.** Vanilla
  Tailwind palette utilities (`bg-amber-500`, `p-3.5`, `rounded-2xl`)
  do NOT compile — the bridge replaces them with design-system tokens.
  Use `bg-action-primary`, `p-4`, `rounded-md` instead. See
  `tailwind.config.js` for the full mapping.
- **Strict TypeScript** with `noUncheckedIndexedAccess` enabled.
- **noVNC websocket is browser-direct.** The Vite proxy handles HTTP
  only; the noVNC `wss://` connection in M3-06 goes from browser
  straight to the Proxmox node.

## Praxova design system

This portal reads tokens from a verbatim copy of
`/home/alton/Documents/Praxova/praxova-design-system/tokens.css`,
located at `src/styles/praxova/tokens.css`. Do NOT modify the local
copy — when the design system upgrades, refresh by re-copying the
file:

```bash
cp /home/alton/Documents/Praxova/praxova-design-system/tokens.css \
   src/styles/praxova/tokens.css
```

Brand SVGs in `public/brand/` are also verbatim copies. Same refresh
procedure.

The design system's component reference at
`/home/alton/Documents/Praxova/praxova-design-system/components/index.html`
is the visual contract. Components in this portal must render
equivalently to the gallery — open both side-by-side when reviewing
visual changes.

## Switching to npm

If you'd rather use npm:

```bash
rm pnpm-lock.yaml node_modules/
npm install
```

Add `package-lock.json` to git in place of `pnpm-lock.yaml`, then
update this README. Functionally everything else (scripts,
dependencies, dev/build pipeline) works identically.

## Scripts

- `pnpm dev` — Vite dev server with HMR, on port 5173.
- `pnpm build` — type-check then production build into `dist/`.
- `pnpm preview` — serve the production build locally.
- `pnpm lint` — ESLint over the source tree.
- `pnpm typecheck` — `tsc --noEmit` over the project.
- `pnpm test` — Vitest unit suite.
- `pnpm e2e` — Playwright smoke test (requires live broker + Proxmox).
- `pnpm e2e:install` — fetch the chromium binary used by `pnpm e2e`.

## Smoke test (M3 acceptance)

The Playwright spec at `e2e/` exercises the full M3 happy path against
a live broker + Proxmox cluster. It is not a unit test — it requires
real infrastructure and runs in a real browser.

### Preconditions

1. **Broker running** at `http://localhost:8080` (or set `OPENVDI_BROKER_URL`).
2. **One Proxmox cluster registered** in the broker, with `status='active'`.
3. **One template registered** in the broker, with QEMU guest agent
   installed and the `agent: 1` config line set.
4. **One pool created** against that template, with VMID range
   allocated and the test user entitled.
5. **Test user** entitled to **exactly one** pool. The launcher will
   render one card, which is what the connect-flow spec expects.
6. **PVE self-signed cert trusted** by the test browser. For local
   dev: visit `https://{pve-host}:8006` once and click through the
   browser's untrusted-cert prompt.

### Environment variables

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `OPENVDI_BROKER_URL` | no | `http://localhost:8080` | Broker base URL |
| `OPENVDI_PORTAL_URL` | no | `http://localhost:5173` | Vite dev server URL |
| `OPENVDI_TEST_USER` | no | `alice` | Username for the entitled test user |
| `OPENVDI_TEST_GROUPS` | no | `""` | Comma-separated AD groups for the test user |
| `OPENVDI_TEST_POOL_ID` | **yes** | — | UUID of the pool the test user is entitled to |
| `OPENVDI_TEST_ADMIN_USER` | no | — | Admin username for the pre-test provision call. If unset, the spec assumes a warm spare already exists. |
| `OPENVDI_TEST_ADMIN_GROUPS` | no | `Admins` | Admin's AD groups |

### Running

```bash
# One-time chromium install
pnpm e2e:install

# Run the suite
pnpm e2e

# Run a specific spec
pnpm e2e e2e/launcher.spec.ts

# UI mode (interactive)
pnpm e2e --ui
```

### What the suite covers

- `launcher.spec.ts` — login form → /desktops renders one pool card → logout → /login.
- `connect-flow.spec.ts` — login (via localStorage seed) → click Connect → URL transitions to `/desktops/{id}/console` → toolbar status reads "Connecting…" then "Connected to ..." → canvas exists with non-zero dimensions → click Disconnect → /desktops re-renders with TanStack invalidation → /sessions shows the disconnected session under the All filter.
- `theme-toggle.spec.ts` — toggle the theme → `data-theme` attribute on `<html>` flips → launcher remains visible in both modes → toggle back round-trips.

### What the suite does NOT cover

These are caught by the manual acceptance checklist below.

- Pixel-level rendering of the canvas (the noVNC handshake is asserted; the painted desktop is not).
- Real LDAP/JWT authentication — M3 is dev-auth only.
- Multi-pool launcher rendering — the test fixture assumes one entitled pool.
- Concurrent-user behavior — single test worker.
- Long-running session stability — connect/disconnect is instantaneous in the spec.
- Mobile / tablet viewport rendering (cosmetic for M3).
- Browser back/forward navigation through the connected console.

## Manual acceptance checklist

After Playwright passes, walk through the following manually before
marking M3 complete. Each is a real-world condition the smoke spec
can't reasonably exercise.

- [ ] Open the portal in light mode. Visual quality is acceptable — cards have soft shadows on white-ish surface, status badges colored correctly per tone, brand mark legible.
- [ ] Toggle to dark mode. Visual quality is acceptable — surfaces lift instead of shadow, text remains legible against dark bg, brand mark uses the dark-mode SVG, status badges still legible.
- [ ] Set browser DevTools → Rendering → Emulate CSS prefers-reduced-motion: reduce. Reload. Spinner animations stop, button transitions become instant. Page still functions.
- [ ] Browser back/forward navigation through /desktops, /sessions, /desktops/{id}/console doesn't crash. The auth state persists across reloads.
- [ ] Connect to a desktop. Real keyboard input (typing) reaches the VM. Mouse clicks reach the VM. The canvas scales when the browser window is resized.
- [ ] Click Send Ctrl+Alt+Del. The VM responds (Windows: secure attention sequence dialog; Linux: depends on DE).
- [ ] Close the browser tab while connected. Wait 30 seconds. Reopen the launcher. The previously-assigned desktop reflects the keepalive DELETE (no dangling session in the broker).
- [ ] Disconnect via the toolbar Disconnect button while a real working session is in progress. Verify the desktop is recycled (non-persistent pools) or retains state (persistent pools).
- [ ] On a reduced-bandwidth connection (DevTools → Network → Slow 3G), the toolbar status remains responsive. The connection establishes, just more slowly.
- [ ] On a real PVE cluster with multiple nodes, the connection works regardless of which node owns the desktop. (M3-05's wss:// URL embeds the node and routes correctly.)
- [ ] AppShell header remains sticky during launcher scroll (when the user is entitled to many pools, the header doesn't scroll out of view).
- [ ] Logout while connected to a console (in another tab). The auth token is cleared; the connected tab continues to render the canvas (already-authenticated WebSocket) but the launcher tab bounces to /login.

If any of the above fails, treat it as an M3 bug and fix before
declaring milestone complete.

## M4 admin smoke test

Validates the admin happy path end-to-end: admin LDAP login, pool
registration, warm-spare provisioning, entitlement grant, user
connect, force-disconnect, audit verification. Requires the broker
running in `OPENVDI_AUTH_MODE=jwt` + a populated LDAP test realm
with both an admin user (member of `OPENVDI_LDAP_ADMIN_GROUP`) and a
regular user.

```bash
# Required env vars (in addition to M3 connect-flow setup):
export OPENVDI_TEST_ADMIN_USER=<ldap-admin-user>
export OPENVDI_TEST_ADMIN_PASSWORD=<password>
export OPENVDI_TEST_USER=<ldap-regular-user>
export OPENVDI_TEST_USER_PASSWORD=<password>

# Optional overrides (defaults shown):
export OPENVDI_TEST_CLUSTER_NAME=e2e-cluster
export OPENVDI_TEST_TEMPLATE_NAME=e2e-template
export OPENVDI_TEST_VMID_RANGE_START=9000

# Run only the admin spec (M3 connect-flow is independent):
pnpm exec playwright test admin-flow

# Or run the full suite:
pnpm e2e
```

**Pre-staged state on the test cluster:**
- A registered cluster named `e2e-cluster` (or `OPENVDI_TEST_CLUSTER_NAME`).
- A registered template named `e2e-template` (or `OPENVDI_TEST_TEMPLATE_NAME`).
- The base template VM exists in PVE with the `openvdi-base` snapshot.
- A free VMID range starting at `OPENVDI_TEST_VMID_RANGE_START` (10 IDs reserved).

**The test:**
- Creates an ephemeral pool named `e2e-pool-${timestamp}`.
- Cleans up via `DELETE /api/v1/pools/{id}` in `afterEach`.
- If cleanup fails (broker down, session stuck), prints a warning —
  manual cleanup may be needed. Same posture as the M3 connect-flow's
  best-effort cleanup.

**Auto-skips** if the env vars above aren't set, so contributors
without the LDAP test realm aren't blocked.

## M3 prompt history

- M3-01: scaffold + design-system pickup.
- M3-02: typed API client + envelope handling.
- M3-03: dev-auth login + AppShell + theme toggle.
- M3-04: desktop launcher view.
- M3-05: NoVNCViewer component.
- M3-06: console route — wire viewer to connect flow.
- M3-07: sessions view + placeholder cleanup.
- M3-08: Playwright smoke test — milestone gate.
