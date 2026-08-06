"""Fail-closed release smoke for one protected Codex canary invocation.

The default outcome is ``BLOCKED_CONFIGURATION``.  A live call is possible
only from the protected workflow, on the exact default-branch SHA, inside the
dedicated isolated container.  Raw prompts, responses, credentials, sessions,
and stderr are never written to evidence or logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from ipaddress import AddressValueError, IPv4Address, IPv4Network
from pathlib import Path
from urllib.parse import urlsplit

CANARY_PROMPT = (
    "Reply with exactly ANYGARDEN_SMOKE_OK and nothing else. "
    "Do not use tools, read files, or create, modify, or delete anything."
)
CANARY_RESPONSE = "ANYGARDEN_SMOKE_OK"
MAX_RESPONSE_BYTES = 256
HARD_TIMEOUT_SECONDS = 60
TERM_GRACE_SECONDS = 2
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
ALLOWED_ITEM_TYPES = {"agent_message", "reasoning"}
RUNTIME_STATE_ROOT = Path("/tmp")
RFC1918_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
SMOKE_FAILURE_RESULT_CODES = {
    "approval_requested": "FAIL_APPROVAL_REQUESTED",
    "canary_mismatch": "FAIL_CANARY_MISMATCH",
    "engine_nonzero": "FAIL_ENGINE_NONZERO",
    "protocol_output_limit": "FAIL_PROTOCOL_OUTPUT_LIMIT",
    "protocol_shape": "FAIL_PROTOCOL_SHAPE",
    "response_limit": "FAIL_RESPONSE_LIMIT",
    "tool_requested": "FAIL_TOOL_REQUESTED",
}


class BlockedConfiguration(Exception):
    """The protected smoke prerequisites are absent or stale."""


class SmokeFailure(Exception):
    """The engine ran but did not satisfy the fixed canary contract."""


@dataclass(frozen=True)
class SmokeEvidence:
    exact_sha: str
    workflow_run: str
    engine: str
    engine_version: str
    model_version: str
    result_code: str
    duration_ms: int
    input_sha256: str
    output_length: int
    output_sha256: str


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "")
    if not value:
        raise BlockedConfiguration(key)
    return value


def validate_configuration(env: Mapping[str, str]) -> tuple[str, str]:
    """Validate every human/configuration gate without revealing values."""
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise BlockedConfiguration("event")
    default_branch = _required(env, "ANYGARDEN_DEFAULT_BRANCH")
    if env.get("GITHUB_REF") != f"refs/heads/{default_branch}":
        raise BlockedConfiguration("default_branch")
    exact_sha = _required(env, "GITHUB_SHA")
    checkout_sha = _required(env, "ANYGARDEN_CHECKOUT_SHA")
    if not SHA_PATTERN.fullmatch(exact_sha) or checkout_sha != exact_sha:
        raise BlockedConfiguration("exact_sha")
    if env.get("ANYGARDEN_SMOKE_ENVIRONMENT_PROTECTED") != "true":
        raise BlockedConfiguration("protected_environment")
    if env.get("ANYGARDEN_SMOKE_APPROVED") != "true":
        raise BlockedConfiguration("human_approval")
    if env.get("ANYGARDEN_SMOKE_BUDGET_POLICY") != "vendor-daily-cap-1":
        raise BlockedConfiguration("budget")
    if env.get("ANYGARDEN_SMOKE_EGRESS_POLICY") != "vendor-only":
        raise BlockedConfiguration("egress")
    validate_proxy_url(env)
    if env.get("ANYGARDEN_SMOKE_CREDENTIAL_SCOPE") != "low-privilege-test-only":
        raise BlockedConfiguration("credential_scope")
    image = _required(env, "ANYGARDEN_SMOKE_CONTAINER_IMAGE")
    if not IMAGE_PATTERN.fullmatch(image):
        raise BlockedConfiguration("pinned_image")
    model = _required(env, "ANYGARDEN_SMOKE_MODEL")
    if not MODEL_PATTERN.fullmatch(model):
        raise BlockedConfiguration("model")
    return exact_sha, model


def validate_proxy_url(env: Mapping[str, str]) -> str:
    """Accept only a credential-free private IPv4 HTTP proxy endpoint."""
    value = _required(env, "ANYGARDEN_SMOKE_PROXY_URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        address = IPv4Address(parsed.hostname or "")
    except (AddressValueError, ValueError):
        raise BlockedConfiguration("proxy") from None
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or port <= 0
        or not any(address in network for network in RFC1918_NETWORKS)
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or value != f"http://{address}:{port}"
    ):
        raise BlockedConfiguration("proxy")
    return value


def validate_proxy_transport(env: Mapping[str, str], proxy_url: str) -> None:
    if env.get("HTTP_PROXY") != proxy_url or env.get("HTTPS_PROXY") != proxy_url:
        raise BlockedConfiguration("proxy")


def build_command(model: str) -> list[str]:
    """Return the fixed one-invocation command; no caller prompt/flags exist."""
    return [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-c",
        "approval_policy=untrusted",
        "-c",
        "model_reasoning_effort=minimal",
        "-m",
        model,
        "-",
    ]


def prepare_runtime_state(env: Mapping[str, str]) -> dict[str, str]:
    """Create the fixed private state directories on the container tmpfs."""
    state_dirs = {
        "HOME": RUNTIME_STATE_ROOT / "home",
        "CODEX_HOME": RUNTIME_STATE_ROOT / "codex",
    }
    for key, path in state_dirs.items():
        if env.get(key) != str(path):
            raise BlockedConfiguration("runtime_state")
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        except OSError:
            raise BlockedConfiguration("runtime_state") from None
    return {key: str(path) for key, path in state_dirs.items()}


def build_child_env(
    env: Mapping[str, str],
    credential: str,
    runtime_state: Mapping[str, str],
    proxy_url: str,
) -> dict[str, str]:
    return {
        "PATH": env.get("PATH", ""),
        "HOME": runtime_state["HOME"],
        "CODEX_HOME": runtime_state["CODEX_HOME"],
        "OPENAI_API_KEY": credential,
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "LANG": "C.UTF-8",
    }


def parse_response(raw: bytes) -> bytes:
    """Extract the canary while rejecting tool/approval activity."""
    if len(raw) > 64 * 1024:
        raise SmokeFailure("protocol_output_limit")
    texts: list[str] = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise SmokeFailure("protocol_shape")
        event_type = str(event.get("type", ""))
        if "approval" in event_type:
            raise SmokeFailure("approval_requested")
        if event_type in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            if item_type not in ALLOWED_ITEM_TYPES:
                raise SmokeFailure("tool_requested")
            if event_type == "item.completed" and item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
    response = "\n".join(texts).strip().encode()
    if len(response) > MAX_RESPONSE_BYTES:
        raise SmokeFailure("response_limit")
    if response != CANARY_RESPONSE.encode():
        raise SmokeFailure("canary_mismatch")
    return response


def _terminate_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=TERM_GRACE_SECONDS)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def execute(model: str, env: Mapping[str, str]) -> tuple[bytes, str]:
    if (
        env.get("ANYGARDEN_SMOKE_RUNTIME_ISOLATED")
        != "container-readonly-empty-workspace"
    ):
        raise BlockedConfiguration("runtime_isolation")
    if any(Path.cwd().iterdir()):
        raise BlockedConfiguration("workspace_not_empty")
    proxy_url = validate_proxy_url(env)
    validate_proxy_transport(env, proxy_url)
    credential = _required(env, "OPENAI_API_KEY")
    runtime_state = prepare_runtime_state(env)
    codex = shutil.which("codex")
    if not codex:
        raise BlockedConfiguration("engine_missing")
    version = subprocess.run(
        [codex, "--version"], capture_output=True, timeout=5, check=False
    )
    if version.returncode != 0:
        raise BlockedConfiguration("engine_version")
    engine_version = version.stdout.decode(errors="replace").strip()[:80]
    child_env = build_child_env(env, credential, runtime_state, proxy_url)
    proc = subprocess.Popen(
        build_command(model),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        start_new_session=True,
    )
    try:
        stdout, _stderr = proc.communicate(
            input=CANARY_PROMPT.encode(), timeout=HARD_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        _terminate_group(proc)
        raise TimeoutError from exc
    if proc.returncode != 0:
        raise SmokeFailure("engine_nonzero")
    return parse_response(stdout), engine_version


def make_evidence(
    env: Mapping[str, str],
    result: str,
    started: float,
    *,
    output: bytes = b"",
    engine_version: str = "",
) -> SmokeEvidence:
    return SmokeEvidence(
        exact_sha=env.get("GITHUB_SHA", ""),
        workflow_run=env.get("GITHUB_RUN_ID", ""),
        engine="codex-cli",
        engine_version=engine_version,
        model_version=env.get("ANYGARDEN_SMOKE_MODEL", ""),
        result_code=result,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        input_sha256=_sha256(CANARY_PROMPT.encode()),
        output_length=len(output),
        output_sha256=_sha256(output) if output else "",
    )


def write_evidence(path: Path, evidence: SmokeEvidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(evidence), sort_keys=True) + "\n", encoding="utf-8"
    )


def run(mode: str, evidence_path: Path, env: Mapping[str, str]) -> int:
    started = time.monotonic()
    try:
        _exact_sha, model = validate_configuration(env)
        if mode == "preflight":
            write_evidence(evidence_path, make_evidence(env, "PREFLIGHT_PASS", started))
            return 0
        output, engine_version = execute(model, env)
        write_evidence(
            evidence_path,
            make_evidence(
                env, "PASS", started, output=output, engine_version=engine_version
            ),
        )
        return 0
    except BlockedConfiguration:
        write_evidence(
            evidence_path, make_evidence(env, "BLOCKED_CONFIGURATION", started)
        )
        return 2
    except TimeoutError:
        write_evidence(evidence_path, make_evidence(env, "TIMEOUT", started))
        return 124
    except SmokeFailure as exc:
        reason = (
            exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], str) else ""
        )
        result = SMOKE_FAILURE_RESULT_CODES.get(reason, "FAIL")
        write_evidence(evidence_path, make_evidence(env, result, started))
        return 1
    except Exception:  # noqa: BLE001 - evidence must redact every unknown failure
        write_evidence(evidence_path, make_evidence(env, "FAIL", started))
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "run"))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.mode, args.evidence, os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
