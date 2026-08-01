# Docker images

This subsystem provides independently deployable CPU backend and frontend
images. Docker Compose configuration is documented separately in
[`compose.md`](compose.md). PostgreSQL, Redis, Kafka, and worker scheduling
remain future subsystems. Existing local commands remain supported.

## Build

Build the backend from the repository root:

```sh
docker build --file Dockerfile.backend --tag omnitrack-backend:local .
```

The backend image requires `yolov8n.pt` in the build context. The current
detector resolves that exact file relative to its working directory. Failing
the build when it is missing avoids an implicit model download during the
first production stream start.

Build the frontend with the default same-origin API contract:

```sh
docker build --file Dockerfile.frontend --tag omnitrack-frontend:local .
```

The default build retains `VITE_API_BASE_URL=/api` and
`VITE_WS_BASE_URL=/ws`. Use explicit public origins only when the frontend is
served separately from its API gateway:

```sh
docker build --file Dockerfile.frontend \
  --build-arg VITE_API_BASE_URL=https://api.example.example \
  --build-arg VITE_WS_BASE_URL=wss://api.example.example \
  --tag omnitrack-frontend:local .
```

Do not pass `VITE_API_KEY` for production authentication: Vite embeds it in
the browser bundle. The existing API-key mechanism is not a replacement for
end-user authentication.

## Run

The backend runs exactly one Uvicorn worker. This is required until the
distributed stream-manager subsystem moves stream ownership, EventBuffers,
and WebSocket coordination out of process.

```sh
docker run --rm --name omnitrack-backend -p 8000:8000 omnitrack-backend:local
```

Verify liveness:

```sh
curl http://127.0.0.1:8000/health
```

The application currently resolves SQLite as `inference_data.db` from its
working directory. This image keeps that behavior unchanged. Durable database
configuration and PostgreSQL are separate subsystems; do not scale this image
or attach multiple containers to the same SQLite file.

Run the frontend static server:

```sh
docker run --rm --name omnitrack-frontend -p 8080:8080 omnitrack-frontend:local
```

The frontend image serves static assets, SPA fallback, and configurable
same-origin `/api` and `/ws` forwarding. In Compose, its Nginx template routes
those paths to the private backend service. For an external gateway, set
`OMNITRACK_BACKEND_HOST` and `OMNITRACK_API_PORT` to the gateway upstream, or
use explicit public Vite build arguments.

## Runtime considerations

- Send `SIGTERM` or `docker stop`; the backend's existing FastAPI lifespan
  closes WebSockets, stops inference threads, and disposes SQLAlchemy.
- The CPU image is the baseline. GPU runtime selection and worker images are
  deferred to the GPU-worker abstraction subsystem.
- Mount camera devices only for edge deployments. RTSP inputs are the portable
  production path; host device ownership is not managed by this image.
- Treat model images as immutable deployment artifacts. Object storage and
  model distribution are intentionally deferred.

## Rollback

No database or API migration is part of Dockerization. Stop/remove the
containers and use the existing local backend and frontend commands. Removing
the images has no effect on application data outside the container.
