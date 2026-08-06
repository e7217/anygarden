from pathlib import Path

ROOT = Path(__file__).parents[3]
RELEASE = (ROOT / ".github/workflows/release.yml").read_text()
SMOKE = (ROOT / ".github/workflows/engine-smoke.yml").read_text()


def test_tag_release_cannot_bypass_browser_or_engine_gate() -> None:
    assert "release-gate:" in RELEASE
    assert "needs: release-gate" in RELEASE
    assert "npm test" in RELEASE
    assert "npm run test:e2e" in RELEASE
    assert "npm run build" in RELEASE
    assert "head_sha=${EXACT_SHA}" in RELEASE
    assert "event=workflow_dispatch&status=success" in RELEASE
    assert "Release blocked" in RELEASE


def test_live_smoke_has_no_pr_ref_prompt_or_retry_inputs() -> None:
    assert "workflow_dispatch:\n" in SMOKE
    assert "pull_request" not in SMOKE
    assert "pull_request_target" not in SMOKE
    assert "inputs:" not in SMOKE
    assert "refs/heads" not in SMOKE  # checked by the fixed runner contract
    assert "ANYGARDEN_DEFAULT_BRANCH" in SMOKE
    assert "ANYGARDEN_CHECKOUT_SHA" in SMOKE


def test_live_smoke_requires_protected_isolated_fail_closed_runner() -> None:
    preflight, live = SMOKE.split("  live-canary:", maxsplit=1)

    assert "environment: release-smoke" in SMOKE
    assert "protection_rules" in SMOKE
    assert "deployment-branch-policies" in SMOKE
    assert '== [\"main\"]' in SMOKE
    assert "runs-on: [self-hosted, linux, anygarden-release-smoke]" in SMOKE
    assert "--read-only --cap-drop ALL" in SMOKE
    assert "--security-opt no-new-privileges" in SMOKE
    assert "--network anygarden-smoke-egress" in SMOKE
    assert "container-readonly-empty-workspace" in SMOKE
    assert "if-no-files-found: error" in SMOKE
    assert "OPENAI_API_KEY" not in preflight
    assert "secrets.ANYGARDEN_SMOKE_OPENAI_API_KEY" in live
