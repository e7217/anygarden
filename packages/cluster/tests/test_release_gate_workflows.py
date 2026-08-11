from pathlib import Path

ROOT = Path(__file__).parents[3]
RELEASE = (ROOT / ".github/workflows/release.yml").read_text()
RELEASE_OPS = (ROOT / "docs/runbook/release-operations.md").read_text()
SMOKE = (ROOT / ".github/workflows/engine-smoke.yml").read_text()
SMOKE_IMAGE = (ROOT / ".github/engine-smoke/Dockerfile").read_text()
SMOKE_PUBLISH = (
    ROOT / ".github/workflows/publish-engine-smoke-image.yml"
).read_text()


def test_tag_release_cannot_bypass_browser_or_engine_gate() -> None:
    assert "release-gate:" in RELEASE
    assert "needs: release-gate" in RELEASE
    assert "npm test" in RELEASE
    assert "npm run test:e2e" in RELEASE
    assert "npm run build" in RELEASE
    assert "head_sha=${EXACT_SHA}" in RELEASE
    assert "event=workflow_dispatch&status=success" in RELEASE
    assert "Release blocked" in RELEASE


def test_release_builds_once_and_publishes_downloaded_artifacts() -> None:
    build, publish = RELEASE.split("  publish-package:", maxsplit=1)

    assert "  build-package:" in build
    assert "needs: release-gate" in build
    assert "contents: read" in build
    assert "uses: actions/upload-artifact@v4" in build
    assert "name: release-artifacts-${{ steps.parse.outputs.package }}-${{ github.sha }}" in build
    assert "needs: build-package" in publish
    assert "actions: read" in publish
    assert "uses: actions/download-artifact@v4" in publish
    assert (
        "name: release-artifacts-${{ needs.build-package.outputs.package }}-${{ github.sha }}"
        in publish
    )
    assert "\n          uv build --package" not in publish


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
    assert "actions: read" in preflight
    assert 'actions/runs/${GITHUB_RUN_ID}/approvals' in preflight
    assert '.state == "approved"' in preflight
    assert '.name == "release-smoke"' in preflight
    assert "environments/release-smoke" not in preflight
    assert "runs-on: [self-hosted, linux, anygarden-release-smoke]" in SMOKE
    assert "--read-only --cap-drop ALL" in SMOKE
    assert "--security-opt no-new-privileges" in SMOKE
    assert "--network anygarden-smoke-egress" in SMOKE
    assert "container-readonly-empty-workspace" in SMOKE
    assert "if-no-files-found: error" in SMOKE
    assert "ANYGARDEN_SMOKE_PROXY_URL: ${{ vars.ANYGARDEN_SMOKE_PROXY_URL }}" in (
        preflight
    )
    assert "ANYGARDEN_SMOKE_PROXY_URL: ${{ vars.ANYGARDEN_SMOKE_PROXY_URL }}" in live
    assert "-e ANYGARDEN_SMOKE_PROXY_URL" in live
    assert '-e HTTP_PROXY="${ANYGARDEN_SMOKE_PROXY_URL}"' in live
    assert '-e HTTPS_PROXY="${ANYGARDEN_SMOKE_PROXY_URL}"' in live
    assert "OPENAI_API_KEY" not in preflight
    assert "secrets.ANYGARDEN_SMOKE_OPENAI_API_KEY" in live


def test_smoke_image_publisher_is_protected_and_emits_immutable_digest() -> None:
    assert "workflow_dispatch:\n" in SMOKE_PUBLISH
    assert "environment: release-smoke" in SMOKE_PUBLISH
    assert 'test "${GITHUB_REF}" = "refs/heads/${ANYGARDEN_DEFAULT_BRANCH}"' in (
        SMOKE_PUBLISH
    )
    assert 'test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"' in SMOKE_PUBLISH
    assert "deployment-branch-policies" in SMOKE_PUBLISH
    assert '== ["main"]' in SMOKE_PUBLISH
    assert "file: .github/engine-smoke/Dockerfile" in SMOKE_PUBLISH
    assert "push: true" in SMOKE_PUBLISH
    assert "tags: type=sha,format=long" in SMOKE_PUBLISH
    assert "${{ steps.build.outputs.digest }}" in SMOKE_PUBLISH


def test_live_canary_keeps_workspace_empty_and_state_on_tmpfs() -> None:
    _preflight, live = SMOKE.split("  live-canary:", maxsplit=1)

    assert SMOKE_IMAGE.rstrip().endswith("WORKDIR /work")
    assert "COPY " not in SMOKE_IMAGE
    assert "ADD " not in SMOKE_IMAGE
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=16m" in live
    assert "--tmpfs /work:rw,noexec,nosuid,size=4m" in live
    assert "--workdir /work" in live
    assert 'install -d -m 0755 "${EVIDENCE_DIR}"' in live
    assert 'install -m 0666 /dev/null "${EVIDENCE_FILE}"' in live
    assert "--user 65532:65532" in live
    assert "size=16m,mode=1777" in live
    assert "size=4m,mode=1777" in live
    assert "-e HOME=/tmp/home -e CODEX_HOME=/tmp/codex" in live
    assert '-v "${EVIDENCE_DIR}:/evidence:ro"' in live
    assert '-v "${EVIDENCE_FILE}:/evidence/evidence.json:rw"' in live
    assert "engine-smoke-evidence:/evidence:rw" not in live
    assert "/work/home" not in live
    assert "/work/codex" not in live
    assert "mkdir -p /work" not in live


def test_smoke_records_secret_scope_and_always_cleans_temporary_resources() -> None:
    preflight, live = SMOKE.split("  live-canary:", maxsplit=1)

    assert "name: Write smoke preflight secret access audit\n        if: always()" in preflight
    assert '"run_id": "${GITHUB_RUN_ID}"' in preflight
    assert "<<JSON" in preflight
    assert "<<'JSON'" not in preflight
    assert "name: engine-smoke-secret-audit-${{ github.sha }}" in preflight
    assert "rm -rf smoke-runner smoke-evidence" in preflight
    assert 'rm -rf "${RUNNER_TEMP}/engine-smoke-evidence"' in live
    assert '"${RUNNER_TEMP}/engine-smoke-runner"' in live


def test_cluster_publish_blocks_until_the_machine_floor_is_published() -> None:
    """A cluster release must not be able to precede the daemon upgrade.

    #581 fences agent generations across the cluster/daemon wire; #582 encoded
    the required pairing as an ``anygarden-machine`` floor and a rollout order.
    Nothing in CI can observe remote daemons, but publishing the cluster while
    the required machine wheel is absent from the index makes the safe order
    *impossible* — operators would have nothing to upgrade to. This gate
    removes that case, and must fail closed rather than warn.
    """
    gate = "Require the machine floor to be publishable first"
    assert gate in RELEASE
    # Scoped to the cluster distribution — machine/agent releases are unaffected.
    assert "needs.build-package.outputs.dist_name == 'anygarden'" in RELEASE
    # The floor is read from the source of truth, not restated in the workflow.
    assert '"anygarden-machine>=[^"]+"' in RELEASE
    assert "pypi.org/pypi/anygarden-machine/" in RELEASE

    # The gate has to run before the upload, otherwise it documents rather
    # than enforces.
    assert RELEASE.index(gate) < RELEASE.index("Publish to PyPI")

    # Fail closed: a missing wheel, an ambiguous floor, and a curl failure all
    # exit non-zero rather than continuing.
    gate_body = RELEASE[RELEASE.index(gate) : RELEASE.index("Publish to PyPI")]
    assert "set -euo pipefail" in gate_body
    assert gate_body.count("exit 1") >= 2
    assert "Release blocked" in gate_body


def test_release_runbook_states_the_daemon_first_acceptance_condition() -> None:
    """The operator-facing condition, including what automation cannot cover.

    The workflow gate only proves the daemon upgrade is *possible*. Whether it
    happened is unobservable from CI, so the runbook has to carry it as an
    acceptance condition and say so explicitly — otherwise a green pipeline
    reads as rollout confirmation.
    """
    assert "daemon 우선 업그레이드" in RELEASE_OPS
    assert "legacy generation-advance" in RELEASE_OPS
    # The limitation that makes the ordering unrecoverable, not merely untidy.
    assert "부작용까지 멈추지는 못한다" in RELEASE_OPS
    # Automation scope is bounded in writing.
    assert "자동화가 덮지 못하는 것" in RELEASE_OPS


def test_secret_audit_artifact_is_described_as_workflow_scope_evidence() -> None:
    """The artifact records declared scope; it cannot observe a child process.

    Describing it as proof of runtime absence would overstate it and hide that
    runtime enforcement lives in the gate script's allowlist instead.
    """
    assert "workflow-scope evidence" in RELEASE_OPS
    assert "not** proof that a secret was" in RELEASE_OPS
    assert "engine_smoke_gate.py" in RELEASE_OPS
