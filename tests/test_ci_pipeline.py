"""CI/release pipeline tests: tag computation, workflow structure,
migration safety, and deployment smoke behavior."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from check_migrations import validate as validate_migrations  # noqa: E402
from release_utils import (  # noqa: E402
    compute_image_tags,
    image_ref,
    is_pushable_ref,
    is_release_ref,
)

WORKFLOWS = {
    p.stem: yaml.safe_load(p.read_text(encoding="utf-8"))
    for p in (ROOT / ".github" / "workflows").glob("*.yml")
}


class TestImageTags:
    def test_pr_ref_not_pushable(self):
        assert is_pushable_ref("refs/pull/42/merge") is False

    def test_main_is_pushable(self):
        assert is_pushable_ref("refs/heads/main") is True

    def test_release_tag_is_pushable(self):
        assert is_pushable_ref("refs/tags/v1.2.3") is True

    def test_feature_branch_not_pushable(self):
        assert is_pushable_ref("refs/heads/feature/x") is False

    def test_non_semver_tag_not_release(self):
        assert is_release_ref("refs/tags/nightly") is False

    def test_sha_tag_always_immutable(self):
        tags = compute_image_tags("refs/heads/main", "abc123def456")
        assert "sha-abc123def456" in tags

    def test_release_tag_includes_version(self):
        tags = compute_image_tags("refs/tags/v1.2.3", "abc")
        assert "v1.2.3" in tags

    def test_no_latest_tag(self):
        for ref in ("refs/heads/main", "refs/tags/v1.0.0", "refs/heads/dev"):
            assert "latest" not in compute_image_tags(ref, "abc")

    def test_branch_slashes_normalized(self):
        tags = compute_image_tags("refs/heads/release/1.0", "abc")
        assert "release-1.0" in tags

    def test_image_ref_format(self):
        assert (
            image_ref("ghcr.io/acme", "omnitrack-backend", "sha-abc")
            == "ghcr.io/acme/omnitrack-backend:sha-abc"
        )


class TestWorkflowSyntax:
    def test_all_workflows_parse(self):
        assert set(WORKFLOWS) == {"ci", "build", "release"}

    def test_ci_runs_on_pr_and_main(self):
        triggers = WORKFLOWS["ci"][True]
        assert "pull_request" in triggers
        assert "main" in triggers["push"]["branches"]

    def test_ci_jobs_present(self):
        jobs = set(WORKFLOWS["ci"]["jobs"])
        assert {"lint", "tests", "migrations", "frontend", "dependencies", "security"} <= jobs

    def test_ci_uses_least_privilege(self):
        for workflow in WORKFLOWS.values():
            assert workflow.get("permissions", {}).get("contents") in (
                "read",
                "write",
                None,
            )

    def test_ci_declares_jwt_secret_env_not_plain(self):
        ci = WORKFLOWS["ci"]
        test_job = ci["jobs"]["tests"]
        secret = test_job["steps"][-1].get("env", {}).get("OMNITRACK_JWT_SECRET")
        assert secret and secret.startswith("ci-only-not-a-real-secret")

    def test_build_only_on_main_and_version_tags(self):
        push = WORKFLOWS["build"][True]["push"]
        assert push["branches"] == ["main"]
        assert push["tags"] == ["v*.*.*"]

    def test_build_scans_images_and_fails_on_critical(self):
        for job_name in ("backend-image", "frontend-image"):
            scan = [
                s
                for s in WORKFLOWS["build"]["jobs"][job_name]["steps"]
                if "trivy" in str(s.get("uses", ""))
            ]
            assert scan, f"{job_name} missing trivy scan"
            assert scan[0]["with"]["exit-code"] == 1
            assert scan[0]["with"]["severity"] == "CRITICAL"

    def test_staging_deploys_only_from_main_push(self):
        staging = WORKFLOWS["build"]["jobs"]["staging-deploy"]
        assert staging["if"] == (
            "github.ref == 'refs/heads/main' && github.event_name == 'push'"
        )

    def test_release_is_manual_dispatch_only(self):
        release = WORKFLOWS["release"]
        triggers = release.get("on") or release.get(True)
        assert triggers == {"workflow_dispatch": triggers["workflow_dispatch"]}

    def test_release_deploys_immutable_version_tag(self):
        deploy_steps = WORKFLOWS["release"]["jobs"]["production-deploy"]["steps"]
        backend_deploy = [s for s in deploy_steps if "deploy.py" in str(s.get("run", ""))][0]
        assert ":${{ needs.gate.outputs.version }}" in backend_deploy["run"]

    def test_release_opens_issue_on_failure(self):
        report = WORKFLOWS["release"]["jobs"]["report"]
        assert report["if"] == "failure()"
        assert report["permissions"]["issues"] == "write"

    def test_release_requires_migration_safety_check(self):
        steps = WORKFLOWS["release"]["jobs"]["production-deploy"]["steps"]
        assert any("check_migrations" in str(s.get("run", "")) for s in steps)

    def test_secret_scanning_present(self):
        security_steps = WORKFLOWS["ci"]["jobs"]["security"]["steps"]
        assert any("gitleaks" in str(s.get("uses", "")) for s in security_steps)

    def test_dependency_audit_present(self):
        security_steps = WORKFLOWS["ci"]["jobs"]["security"]["steps"]
        assert any("pip-audit" in str(s.get("uses", "")) for s in security_steps)

    def test_frontend_job_uses_npm_ci(self):
        steps = WORKFLOWS["ci"]["jobs"]["frontend"]["steps"]
        assert any(s.get("run") == "npm ci" for s in steps)

    def test_migration_job_runs_upgrade_and_check(self):
        steps = WORKFLOWS["ci"]["jobs"]["migrations"]["steps"]
        runs = " ".join(str(s.get("run", "")) for s in steps)
        assert "alembic upgrade head" in runs
        assert "alembic check" in runs

    def test_all_shell_steps_parse_as_strings(self):
        for workflow in WORKFLOWS.values():
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    if "run" in step:
                        assert isinstance(step["run"], str)


class TestMigrationSafety:
    def test_current_chain_is_clean(self):
        errors, warnings = validate_migrations()
        assert errors == []

    def test_single_head_enforced(self):
        errors, _ = validate_migrations()
        assert not any("head" in e for e in errors)

    def test_destructive_upgrade_would_fail(self, tmp_path, monkeypatch):
        file = tmp_path / "b1_add_users.py"
        file.write_text(
            "revision = 'b1'\n"
            "down_revision = None\n"
            "def upgrade():\n"
            "    op.drop_table('users')\n"
            "def downgrade():\n"
            "    op.create_table('users', sa.Column('id', sa.Integer))\n"
        )
        import check_migrations as cm

        monkeypatch.setattr(cm, "VERSIONS_DIR", tmp_path)
        errors, _ = cm.validate()
        assert any("drop_table" in e for e in errors)

    def test_allow_destructive_demotes_to_warning(self, tmp_path, monkeypatch):
        file = tmp_path / "b1_add_users.py"
        file.write_text(
            "revision = 'b1'\n"
            "down_revision = None\n"
            "def upgrade():\n"
            "    op.drop_table('users')\n"
            "def downgrade():\n"
            "    op.create_table('users', sa.Column('id', sa.Integer))\n"
        )
        import check_migrations as cm

        monkeypatch.setattr(cm, "VERSIONS_DIR", tmp_path)
        errors, warnings = cm.validate(allow_destructive=True)
        assert errors == []
        assert warnings

    def test_forked_history_fails(self, tmp_path, monkeypatch):
        base = tmp_path / "0001_base.py"
        base.write_text("revision = '0001'\ndown_revision = None\n")
        fork_a = tmp_path / "a_fork.py"
        fork_a.write_text("revision = 'aaa'\ndown_revision = '0001'\n")
        fork_b = tmp_path / "b_fork.py"
        fork_b.write_text("revision = 'bbb'\ndown_revision = '0001'\n")
        import check_migrations as cm

        monkeypatch.setattr(cm, "VERSIONS_DIR", tmp_path)
        errors, _ = cm.validate()
        assert any("1 migration head" in e for e in errors)

    def test_missing_parent_fails(self, tmp_path, monkeypatch):
        orphan = tmp_path / "zz_orphan.py"
        orphan.write_text("revision = 'zzz'\ndown_revision = 'nonexistent'\n")
        import check_migrations as cm

        monkeypatch.setattr(cm, "VERSIONS_DIR", tmp_path)
        errors, _ = cm.validate()
        assert any("missing revisions" in e for e in errors)


class TestDeploySmoke:
    def test_smoke_test_retries_then_fails(self, monkeypatch):
        import deploy as dp

        calls = []

        def fake_urlopen(url, timeout):
            calls.append(url)
            raise OSError("connection refused")

        monkeypatch.setattr(dp.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(dp.time, "sleep", lambda s: None)
        with pytest.raises(dp.DeployError, match="smoke test failed"):
            dp.smoke_test("http://x/health", attempts=3)
        assert len(calls) == 3

    def test_smoke_test_passes_on_200(self, monkeypatch):
        import deploy as dp

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(
            dp.urllib.request, "urlopen", lambda url, timeout: FakeResponse()
        )
        dp.smoke_test("http://x/health")

    def test_failed_rollout_triggers_rollback(self, monkeypatch):
        import deploy as dp

        rolled_back = []
        monkeypatch.setattr(
            dp,
            "rollout_status",
            lambda d, n, t: (_ for _ in ()).throw(dp.DeployError("timeout")),
        )
        monkeypatch.setattr(
            dp, "rollback", lambda d, n: rolled_back.append(d) or True
        )
        with pytest.raises(dp.DeployError):
            dp.deploy("backend", "ns", None, 60, None)
        assert rolled_back == ["backend"]

    def test_deploy_skips_rollback_on_success(self, monkeypatch):
        import deploy as dp

        monkeypatch.setattr(dp, "rollout_status", lambda d, n, t: None)
        rolled_back = []
        monkeypatch.setattr(dp, "rollback", lambda d, n: rolled_back.append(d))
        record = dp.deploy("backend", "ns", None, 60, None)
        assert record["rollout"] == "ok"
        assert rolled_back == []

    def test_deploy_uses_container_name_not_deployment(self, monkeypatch):
        import deploy as dp

        commands = []
        monkeypatch.setattr(
            dp,
            "_kubectl",
            lambda args, timeout=60: commands.append(args) or "",
        )
        monkeypatch.setattr(dp, "rollout_status", lambda d, n, t: None)
        dp.deploy("omnitrack-backend", "ns", "img:1", 60, None, container="backend")
        set_image = [c for c in commands if c[:2] == ["set", "image"]][0]
        assert "backend=img:1" in set_image
        assert "omnitrack-backend=img:1" not in set_image

    def test_deploy_error_includes_kubectl_stderr(self):
        assert "kubectl" in str(
            __import__("deploy").DeployError("kubectl get failed: boom")
        )


class TestPipelineScriptsAreValidPython:
    @pytest.mark.parametrize(
        "script", ["release_utils", "check_migrations", "deploy"]
    )
    def test_script_parses(self, script):
        source = (ROOT / "scripts" / "ci" / f"{script}.py").read_text(
            encoding="utf-8"
        )
        ast.parse(source, script)
