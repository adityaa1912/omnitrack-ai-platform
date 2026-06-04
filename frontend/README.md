# OmniTrack Frontend

Real-time AI operations dashboard for the OmniTrack AI Platform.

## Stack

- React 18 + Vite + TypeScript
- TailwindCSS (dark-mode-first design system)
- TanStack Query (server state) — Zustand reserved for real-time state
- React Router

## Architecture

```
src/
  app/         Router + Query client composition
  config/      Typed environment access
  types/       API contract (mirrors backend Pydantic schemas)
  lib/         Framework-agnostic infra (API client; WS service later)
  hooks/       React bridge to lib/ (added in later commits)
  store/       Client-owned real-time state (added in later commits)
  components/  ui / layout / feature domains
  pages/       Routed views
```

**State boundary:** TanStack Query owns server state (list/metrics);
Zustand owns ephemeral real-time state (live frames, socket status,
rolling telemetry). They are intentionally not mixed.

## Development

```bash
cp .env.example .env
npm install
npm run dev   # http://localhost:5173, proxies /api and /ws to :8000
```

The Vite dev server proxies `/api` -> `http://localhost:8000` and
`/ws` -> `ws://localhost:8000`, so run the backend (`python -m backend.run`)
alongside it.
