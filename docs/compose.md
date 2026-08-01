# Local production Compose environment

Docker Compose runs the existing backend and frontend images as one local
production environment. It adds no database, cache, event bus, GPU worker, or
distributed scheduling service.

## Configuration

The repository-root `.env` is the single configuration input for Compose,
backend runtime settings, and local Vite development.

```sh
cp .env.example .env
```

`backend.settings.Settings` validates `OMNITRACK_*` values at process startup.
The API intentionally rejects `OMNITRACK_API_WORKERS` values other than `1`
until stream ownership and WebSocket/EventBuffer state become shared.

`OMNITRACK_ENVIRONMENT=container` is set only inside Compose. When
`OMNITRACK_SQLITE_PATH` is omitted, the validated default is
`/app/data/inference_data.db`; the `backend-data` named volume persists it.
For direct local Python execution, the default remains `inference_data.db`.

`VITE_*` values are build-time browser configuration. Do not put production
secrets in `VITE_API_KEY`; Vite embeds it in the static bundle.

The PostgreSQL, Redis, and Kafka settings are validated configuration
placeholders only. They are not consumed or provisioned by this subsystem.

## Start and verify

```sh
docker compose --env-file .env up --build --wait
docker compose ps
curl http://127.0.0.1:${OMNITRACK_API_PORT:-8000}/health
curl http://127.0.0.1:${OMNITRACK_FRONTEND_PORT:-8080}/healthz
```

The frontend routes its existing `/api` and `/ws` contracts through Nginx to
the backend service on the private `omnitrack` bridge network. The backend is
not exposed to that route through a hardcoded URL.

## Lifecycle and persistence

- The frontend starts only after backend health succeeds.
- Both services use `restart: unless-stopped` and a configurable stop grace
  period. `docker compose down` sends `SIGTERM`; the existing FastAPI lifespan
  closes sockets and drains streams.
- `backend-data` is the only persistent volume. Verify it survives recreation:

```sh
docker compose down
docker compose up -d --wait
docker volume inspect omnitrack-backend-data
```

Use `docker compose down -v` only when intentionally destroying local SQLite
state.

## Rollback

No API or database-schema migration is included. Stop the Compose project and
continue using the existing standalone images or local backend/frontend
commands. Remove `backend-data` only if the stored SQLite state is no longer
needed.
