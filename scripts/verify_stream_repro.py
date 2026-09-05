"""One-shot verification: stream start via TestClient, analytics on and off.

Run: python scripts/verify_stream_repro.py
Exits non-zero on failure.
"""
import os
import sys
import time

os.environ.setdefault("OMNITRACK_SQLITE_PATH", "dev_verify.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.settings import get_settings  # noqa: E402


def run_case(label: str, analytics_enabled: bool) -> bool:
    os.environ["OMNITRACK_ANALYTICS_ENABLED"] = "true" if analytics_enabled else "false"
    get_settings.cache_clear()
    ok = True
    with TestClient(app) as client:
        stream_id = f"repro-{label}"
        r = client.post(
            "/stream/start",
            json={
                "stream_id": stream_id,
                "source": 0,
                "width": 640,
                "height": 480,
                "fps": 30,
                "tracking_enabled": True,
            },
        )
        print(f"[{label}] /stream/start -> {r.status_code}")
        if r.status_code != 200:
            print(r.text)
            return False
        # Let the inference loop process several frames.
        deadline = time.time() + 12
        while time.time() < deadline:
            m = client.get(f"/stream/{stream_id}/metrics")
            if m.status_code != 200:
                print(f"[{label}] /metrics -> {m.status_code}: {m.text}")
                return False
            body = m.json()
            if body.get("error_message"):
                print(f"[{label}] stream error: {body['error_message']}")
                return False
            if body.get("total_frames", 0) >= 10 and body.get("is_active"):
                print(
                    f"[{label}] alive, frames={body['total_frames']} "
                    f"fps={body['fps']:.1f}"
                )
                break
            time.sleep(0.5)
        else:
            print(f"[{label}] stream never reached 10 frames: {m.json()}")
            ok = False
        # Metrics must stay 200 with the stream alive.
        m = client.get(f"/stream/{stream_id}/metrics")
        ok = ok and m.status_code == 200 and m.json().get("is_active")
        print(f"[{label}] final /metrics -> {m.status_code} active={m.json().get('is_active')}")
        s = client.post("/stream/stop", params={"stream_id": stream_id})
        print(f"[{label}] /stream/stop -> {s.status_code}")
    return ok


if __name__ == "__main__":
    disabled_ok = run_case("analytics-off", analytics_enabled=False)
    enabled_ok = run_case("analytics-on", analytics_enabled=True)
    print(f"analytics-disabled: {'PASS' if disabled_ok else 'FAIL'}")
    print(f"analytics-enabled:  {'PASS' if enabled_ok else 'FAIL'}")
    sys.exit(0 if disabled_ok and enabled_ok else 1)
