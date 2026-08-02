"""Provider benchmarking and fastest-provider selection for auto mode.

Benchmarks every available provider once with a small warmup inference, records
load time / latency / throughput / memory, persists results to a JSON cache next
to the model so re-benchmarking is skipped on later startups, and returns the
fastest provider with graceful fallback if it fails to load.
"""

from __future__ import annotations

import json
import logging
import os
import time

import numpy as np

logger = logging.getLogger(__name__)

try:
    import psutil
except Exception:  # noqa: BLE001 - memory tracking is best-effort
    psutil = None  # type: ignore[assignment]

LAST_RESULTS: dict = {}
LAST_DURATION_SECONDS: float = 0.0
LAST_SELECTED: str = ""

_CACHE_VERSION = 1


def _cache_path(model_name: str) -> str:
    root, _ = os.path.splitext(model_name)
    return root + "_benchmark.json"


def _signature(model_name: str) -> str:
    try:
        return str(int(os.path.getmtime(model_name)))
    except OSError:
        return "missing"


def _read_cache(config) -> dict | None:
    path = _cache_path(config.model_name)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if (
        data.get("version") == _CACHE_VERSION
        and data.get("model") == config.model_name
        and data.get("signature") == _signature(config.model_name)
    ):
        return data
    return None


def _write_cache(config, results: dict) -> None:
    path = _cache_path(config.model_name)
    payload = {
        "version": _CACHE_VERSION,
        "model": config.model_name,
        "signature": _signature(config.model_name),
        "providers": sorted(results),
        "results": results,
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError as exc:  # noqa: BLE001 - a missing cache only re-benchmarks
        logger.warning(f"Could not write benchmark cache {path}: {exc}")


def cached_results(model_name: str) -> dict:
    """Return cached per-provider benchmark results for ``model_name``, or {}."""
    path = _cache_path(model_name)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data.get("results", {})


def _rss_bytes() -> int:
    if psutil is None:
        return 0
    try:
        return int(psutil.Process().memory_info().rss)
    except Exception:  # noqa: BLE001 - best-effort
        return 0


def _benchmark_one(builder, config, runs: int) -> tuple[dict, object]:
    provider = builder(config)
    before = _rss_bytes()
    start = time.perf_counter()
    provider.load()
    load_ms = (time.perf_counter() - start) * 1000.0
    try:
        provider.warmup()
    except Exception as exc:  # noqa: BLE001 - warmup is best-effort
        logger.warning(f"Benchmark warmup failed for {builder.__name__}: {exc}")
    size = config.inference_width or 640
    dummy = np.zeros((size, size, 3), dtype=np.uint8)
    latencies = []
    for _ in range(max(int(runs), 1)):
        start = time.perf_counter()
        provider.predict(dummy)
        latencies.append((time.perf_counter() - start) * 1000.0)
    avg = sum(latencies) / len(latencies)
    fps = 1000.0 / avg if avg > 0 else 0.0
    data = {
        "load_time_ms": load_ms,
        "avg_latency_ms": avg,
        "fps": fps,
        "memory_bytes": max(_rss_bytes() - before, 0),
        "score": fps,
    }
    return data, provider


def _benchmark_all(config, builders) -> tuple[dict, dict]:
    results: dict = {}
    instances: dict = {}
    for builder in builders:
        try:
            data, provider = _benchmark_one(builder, config, config.benchmark_runs)
            results[builder.name] = data
            instances[builder.name] = provider
        except Exception as exc:  # noqa: BLE001 - unusable providers are excluded
            logger.warning(f"Benchmark failed for {builder.__name__}: {exc}")
    return results, instances


def _record_metrics(results: dict) -> None:
    try:
        from backend.observability import metrics as _m
    except Exception:  # noqa: BLE001 - metrics are optional
        return
    _m.INFERENCE_BENCHMARK_DURATION.set(LAST_DURATION_SECONDS)
    for name, data in results.items():
        _m.INFERENCE_PROVIDER_SCORE.labels(provider=name).set(
            float(data.get("score", 0.0))
        )


def select_provider(config, builders):
    """Benchmark (or reuse cached results for) ``builders`` and return the best.

    Results are cached per model + provider set, so re-benchmarking only happens
    when the model changed or the set of available providers changed. The
    fastest provider is loaded and returned; if it fails to load, the
    next-fastest is tried.
    """
    global LAST_RESULTS, LAST_DURATION_SECONDS, LAST_SELECTED
    available = sorted(b.name for b in builders)
    results: dict | None = None
    instances: dict = {}
    cached = _read_cache(config)
    if cached is not None and sorted(cached.get("providers", [])) == available:
        results = cached.get("results", {})
        LAST_DURATION_SECONDS = 0.0
    if results is None:
        start = time.perf_counter()
        results, instances = _benchmark_all(config, builders)
        LAST_DURATION_SECONDS = time.perf_counter() - start
        _write_cache(config, results)
    LAST_RESULTS = results
    _record_metrics(results)

    ranked = [
        name
        for name, data in sorted(
            results.items(), key=lambda kv: kv[1].get("score", 0.0), reverse=True
        )
        if data.get("score", 0.0) > 0
    ]
    if instances and ranked:
        best = instances[ranked[0]]
        best._auto_loaded = True
        LAST_SELECTED = best.name
        return best

    by_name = {b.name: b for b in builders}
    last_error: Exception | None = None
    for name in ranked:
        builder = by_name.get(name)
        if builder is None:
            continue
        provider = builder(config)
        try:
            provider.load()
            provider._auto_loaded = True
            LAST_SELECTED = provider.name
            return provider
        except Exception as exc:  # noqa: BLE001 - try the next-fastest
            last_error = exc
            logger.warning(
                f"Benchmark-selected provider {name} failed to load ({exc}); "
                "trying next-fastest"
            )
    raise RuntimeError(
        f"No inference provider could be loaded (last error: {last_error})"
    )
