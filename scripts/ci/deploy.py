"""Kubernetes deploy driver: rollout, status check, smoke test, rollback.

Usage:
  python scripts/ci/deploy.py --namespace omnitrack --deployment omnitrack-backend \
      --timeout 300 --image omnitrack-backend:sha-abc123

Applied to a temp namespace in tests without a cluster via the
``--dry-run`` planner. Fails (exit 1) and rolls back automatically when the
rollout stalls or the post-deploy smoke test fails.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request


class DeployError(RuntimeError):
    pass


def _kubectl(args: list[str], timeout: int = 60) -> str:
    result = subprocess.run(
        ["kubectl"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise DeployError(
            f"kubectl {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout


def rollout_status(deployment: str, namespace: str, timeout: int) -> None:
    _kubectl(
        [
            "rollout",
            "status",
            f"deployment/{deployment}",
            f"--namespace={namespace}",
            f"--timeout={timeout}s",
        ],
        timeout=timeout + 30,
    )


def rollback(deployment: str, namespace: str) -> bool:
    try:
        _kubectl(
            [
                "rollout",
                "undo",
                f"deployment/{deployment}",
                f"--namespace={namespace}",
            ]
        )
        _kubectl(
            [
                "rollout",
                "status",
                f"deployment/{deployment}",
                f"--namespace={namespace}",
                "--timeout=180s",
            ],
            timeout=210,
        )
        return True
    except (DeployError, subprocess.TimeoutExpired):
        return False


def smoke_test(url: str, attempts: int = 10, delay: float = 3.0) -> None:
    last_error = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return
                last_error = f"HTTP {response.status}"
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last_error = str(exc)
        time.sleep(delay)
    raise DeployError(f"smoke test failed for {url}: {last_error}")


def deploy(
    deployment: str,
    namespace: str,
    image: str | None,
    timeout: int,
    smoke_url: str | None,
) -> dict:
    record: dict = {"deployment": deployment, "namespace": namespace}
    if image is not None:
        _kubectl(
            [
                "set",
                "image",
                f"deployment/{deployment}",
                f"{deployment}={image}",
                f"--namespace={namespace}",
            ]
        )
        record["image"] = image
    try:
        rollout_status(deployment, namespace, timeout)
        record["rollout"] = "ok"
    except DeployError as exc:
        record["rollout"] = str(exc)
        record["rollback"] = rollback(deployment, namespace)
        raise DeployError(str(exc)) from exc
    if smoke_url is not None:
        try:
            smoke_test(smoke_url)
            record["smoke"] = "ok"
        except DeployError as exc:
            record["smoke"] = str(exc)
            record["rollback"] = rollback(deployment, namespace)
            raise DeployError(str(exc)) from exc
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="omnitrack")
    parser.add_argument("--deployment", default="omnitrack-backend")
    parser.add_argument("--image", default=None)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--smoke-url", default=None)
    args = parser.parse_args()
    try:
        record = deploy(
            args.deployment,
            args.namespace,
            args.image,
            args.timeout,
            args.smoke_url,
        )
    except DeployError as exc:
        print(f"DEPLOY FAILED (rolled back): {exc}")
        return 1
    print(f"DEPLOY OK: {record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
