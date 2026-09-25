# Code Conventions & Standards

## Python Backend & Inference

### Style & Formatting
- **Linter & Formatter**: Ruff / Flake8 style compatibility, PEP 8 compliance.
- **Type Annotations**: Python typing throughout (`typing.Optional`, `typing.List`, `typing.Dict`, `typing.Tuple`, and modern Python 3.10+ union types where appropriate).
- **Data Validation**: Strict Pydantic v2 models for all incoming request bodies, response envelopes, and configuration schemas.
- **Docstrings**: Google / Sphinx style docstrings for modules, classes, and service methods.

### Concurrency & Threading Rules
- **Thread Isolation**: Video frame grabbers execute in dedicated worker threads to prevent blocking the asyncio event loop.
- **Asyncio Best Practices**: Endpoints and WebSocket handlers use non-blocking `await` calls. CPU-bound or blocking I/O tasks are offloaded via `asyncio.to_thread` or thread pools.
- **Graceful Shutdown**: All long-running workers register cancellation tokens / threading events checked on application shutdown (`Lifespan` context manager in FastAPI).

### Error Handling & Logging
- **Structured Logging**: Standardized JSON logger configured via `backend/observability/logging.py`.
- **Correlation Tracking**: Every request / stream cycle carries a correlation ID via `backend/observability/correlation.py`.
- **Standardized Error Responses**: Handled via custom exception handlers returning `{ "error": { "code": ..., "message": ..., "details": ... } }`.

## TypeScript & React Frontend

### Language & Strictness
- **Strict TypeScript**: `noImplicitAny: true`, strict null checks, no unused locals/parameters.
- **Functional Components**: React 18 functional components with explicit hook dependencies.
- **No Direct Mutation**: Zustand stores updated via immutable state updates.

### Component & Styling Guidelines
- **Tailwind CSS**: Utility classes organized logically (layout, sizing, typography, colors, states).
- **Utility merging**: Using `clsx` and `tailwind-merge` via `cn()` helper for dynamic class composition.
- **Canvas Rendering Rules**:
  - Never draw frames or high-rate bounding boxes inside standard React re-render cycles.
  - Bind canvas rendering to `requestAnimationFrame`.
  - Store volatile high-frequency metrics in mutable refs or atomic Zustand stores without triggering layout recalculations.
