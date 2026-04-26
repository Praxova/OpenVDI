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

## Roadmap (M3 prompts)

- M3-01 (this prompt): scaffold + design-system pickup. ✅
- M3-02: typed API client + envelope handling.
- M3-03: dev-auth login + AppShell + theme toggle.
- M3-04: desktop launcher view.
- M3-05: NoVNCViewer component.
- M3-06: console route — wire viewer to connect flow.
- M3-07: sessions view.
- M3-08: Playwright smoke test — milestone gate.
