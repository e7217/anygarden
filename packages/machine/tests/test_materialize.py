"""Tests for Spawner._materialize_agent_dir (per-agent directory prune + reconcile).

Scope: the materialization step only. Actual subprocess spawn is
covered by test_spawner.py — here we verify that the on-disk tree
matches the manifest in ``SpawnManifest.agents_md`` + ``files`` and
that re-running the materializer with a different manifest deletes
the files that dropped out (the reason the spawn frame needs a prune
step at all — see
``docs/decisions/002-per-agent-directory-with-server-manifest.md``).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from doorae_machine.agent_dir import AgentFilePathError
from doorae_machine.spawner import SpawnManifest, Spawner


@pytest.fixture
def agent_dirs_root(tmp_path: Path) -> Path:
    return tmp_path / "doorae" / "agents"


@pytest.fixture
def spawner(agent_dirs_root: Path) -> Spawner:
    return Spawner(
        on_stopped=AsyncMock(),
        on_crashed=AsyncMock(),
        agent_dirs_root=agent_dirs_root,
    )


def _msg(
    *,
    agent_id: str = "agent-x",
    agents_md: str | None = "# instructions\nHello",
    files: dict[str, str] | None = None,
    engine_secrets: dict[str, str] | None = None,
    engine: str = "codex",
) -> SpawnManifest:
    return SpawnManifest(
        agent_id=agent_id,
        engine=engine,
        agent_token="tok",
        profile_yaml="",
        rooms=["r1"],
        server_url="ws://localhost",
        agents_md=agents_md,
        files=files or {},
        engine_secrets=engine_secrets or {},
    )


class TestMaterializeFresh:
    def test_creates_root_and_workspace(
        self, spawner: Spawner, agent_dirs_root: Path
    ) -> None:
        agent_root = spawner._materialize_agent_dir(_msg())

        assert agent_root == agent_dirs_root / "agent-x"
        assert agent_root.is_dir()
        assert (agent_root / "workspace").is_dir()

    def test_writes_agents_md(self, spawner: Spawner) -> None:
        agent_root = spawner._materialize_agent_dir(_msg(agents_md="# A\nbody"))
        agents_md = agent_root / "AGENTS.md"
        # Base content is preserved at the top; Memory section is
        # always appended.
        content = agents_md.read_text()
        assert content.startswith("# A\nbody")
        assert "## Memory" in content
        # chmod 600 — owner rw only
        mode = agents_md.stat().st_mode & 0o777
        assert mode == 0o600

    def test_agents_md_auto_inlines_skill_bodies(
        self, spawner: Spawner
    ) -> None:
        """Codex (and similar engines that only read AGENTS.md) must
        still see the skills somewhere. The materializer appends
        each ``skills/<name>/SKILL.md`` body into a deterministic
        "Available skills" section at the end of AGENTS.md so Codex
        can honor skill rules without us having to teach the admin
        to paste skill bodies manually into AGENTS.md.
        """
        skill_greeting = (
            "---\n"
            "name: greeting\n"
            "description: say hi\n"
            "---\n\n"
            "# greeting body"
        )
        skill_review = (
            "---\n"
            "name: review\n"
            "description: code review\n"
            "---\n\n"
            "# review body"
        )
        agent_root = spawner._materialize_agent_dir(
            _msg(
                agents_md="# base\nplain instructions",
                files={
                    "skills/greeting/SKILL.md": skill_greeting,
                    "skills/review/SKILL.md": skill_review,
                },
            )
        )
        rendered = (agent_root / "AGENTS.md").read_text()

        # The original base AGENTS.md is still there, unchanged, at
        # the top. No mutation of admin-authored instructions.
        assert rendered.startswith("# base\nplain instructions")

        # A clearly-marked section announces the auto-inlined
        # skills so the reader knows they were generated.
        assert "## Available skills" in rendered
        assert "(auto-generated" in rendered

        # Both skill bodies are present.
        assert "# greeting body" in rendered
        assert "# review body" in rendered

        # Skills are listed in sorted order for deterministic
        # caching. "greeting" < "review", so greeting first.
        g_idx = rendered.index("# greeting body")
        r_idx = rendered.index("# review body")
        assert g_idx < r_idx

        # Each skill is introduced by its path so the admin can
        # cross-reference.
        assert "skills/greeting/SKILL.md" in rendered
        assert "skills/review/SKILL.md" in rendered

        # The raw skills/*/SKILL.md files are still written to
        # disk (claude-code + gemini-cli use them via native
        # discovery). Auto-inline is purely additive.
        assert (agent_root / "skills" / "greeting" / "SKILL.md").read_text() == skill_greeting
        assert (agent_root / "skills" / "review" / "SKILL.md").read_text() == skill_review

    def test_agents_md_no_section_when_no_skills(
        self, spawner: Spawner
    ) -> None:
        """A manifest with no skills should not sprout an empty
        ``## Available skills`` section at the bottom of AGENTS.md.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(
                agents_md="# base\nbody",
                files={".codex/config.toml": "[x]\n"},
            )
        )
        rendered = (agent_root / "AGENTS.md").read_text()
        assert "Available skills" not in rendered
        assert rendered.startswith("# base\nbody")
        # Memory section is always appended.
        assert "## Memory" in rendered

    def test_writes_files_under_whitelisted_prefixes(
        self, spawner: Spawner
    ) -> None:
        files = {
            "skills/coder/SKILL.md": "---\nname: coder\n---\nbody",
            ".codex/config.toml": "[mcp_servers.x]\ncommand = \"y\"\n",
            ".gemini/settings.json": "{\"mcp\": {}}",
        }
        agent_root = spawner._materialize_agent_dir(_msg(files=files))

        for path, expected in files.items():
            f = agent_root / path
            assert f.read_text() == expected
            assert f.stat().st_mode & 0o777 == 0o600

    def test_creates_claude_md_symlink(self, spawner: Spawner) -> None:
        agent_root = spawner._materialize_agent_dir(_msg())
        link = agent_root / "CLAUDE.md"
        assert link.is_symlink()
        assert os.readlink(link) == "AGENTS.md"

    def test_creates_agents_skills_symlink(self, spawner: Spawner) -> None:
        agent_root = spawner._materialize_agent_dir(
            _msg(files={"skills/coder/SKILL.md": "body"})
        )
        link = agent_root / ".agents" / "skills"
        assert link.is_symlink()
        # relative target pointing one level up
        assert os.readlink(link) == "../skills"

    def test_creates_workspace_agents_md_symlink_for_codex(
        self, spawner: Spawner
    ) -> None:
        """Default engine (codex) gets ``workspace/AGENTS.md`` as a
        symlink to ``../AGENTS.md``. This is the isolation contract
        the Codex review signed off on: reads resolve through the
        symlink to the canonical file, but writes via the agent's
        shell tool resolve to a path OUTSIDE the workspace-write
        sandbox and get rejected at the sandbox boundary. Without
        that, an agent could overwrite its own instructions
        mid-session (in-session prompt injection) and subsequent
        turns would see the tampered content.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(agents_md="# instructions", engine="codex")
        )
        path = agent_root / "workspace" / "AGENTS.md"
        assert path.is_symlink()
        assert os.readlink(path) == "../AGENTS.md"
        # The symlink target is the canonical managed file.
        assert path.read_text().startswith("# instructions")

    def test_creates_workspace_claude_md_symlink_for_codex(
        self, spawner: Spawner
    ) -> None:
        """Same isolation contract for the CLAUDE.md bridge used by
        Claude Code (which also tolerates symlinks). Writes through
        the symlink land on ``agent_root/CLAUDE.md`` which is
        outside the workspace sandbox — rejected.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(agents_md="# instructions", engine="codex")
        )
        path = agent_root / "workspace" / "CLAUDE.md"
        assert path.is_symlink()
        assert os.readlink(path) == "../CLAUDE.md"
        assert path.read_text().startswith("# instructions")

    def test_creates_workspace_agents_md_real_copy_for_gemini(
        self, spawner: Spawner
    ) -> None:
        """Gemini CLI's file-reader tool resolves symlinks before
        the "allowed workspace directories" check and rejects any
        symlink whose target escapes the sandbox. The codex-style
        ``workspace/AGENTS.md -> ../AGENTS.md`` symlink fails for
        gemini with "Path not in workspace: resolves outside the
        allowed workspace directories".

        So for ``engine == "gemini-cli"`` the materializer writes a
        real-file copy of the composed bytes. To keep the isolation
        loss bounded, the copy is mode 0o400 (read-only for the
        owner) so a trivial ``open(..., O_WRONLY)`` write fails with
        EACCES. The agent can still ``chmod u+w`` before writing
        (chmod is not sandbox-blocked), but the detour is loud and
        the next spawn's materializer overwrites the bytes either
        way — tamper is scoped to a single session.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(agents_md="# instructions", engine="gemini-cli")
        )
        path = agent_root / "workspace" / "AGENTS.md"
        assert path.is_file()
        assert not path.is_symlink()
        assert path.read_text().startswith("# instructions")
        # 0o400 — owner read-only. 0o600 would have let the agent
        # overwrite its own instructions without even having to
        # chmod first (no speedbump at all).
        assert path.stat().st_mode & 0o777 == 0o400

    def test_creates_workspace_claude_md_real_copy_for_gemini(
        self, spawner: Spawner
    ) -> None:
        """Same pattern for the CLAUDE.md slot when the engine is
        gemini-cli — real file, 0o400. (Gemini doesn't actually read
        CLAUDE.md but the materializer writes both slots uniformly
        so a later engine switch doesn't leave one slot in the wrong
        shape.)
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(agents_md="# instructions", engine="gemini-cli")
        )
        path = agent_root / "workspace" / "CLAUDE.md"
        assert path.is_file()
        assert not path.is_symlink()
        assert path.read_text().startswith("# instructions")
        assert path.stat().st_mode & 0o777 == 0o400

    def test_workspace_agents_md_absent_when_no_agents_md(
        self, spawner: Spawner
    ) -> None:
        agent_root = spawner._materialize_agent_dir(_msg(agents_md=None))
        link = agent_root / "workspace" / "AGENTS.md"
        assert not link.exists()
        assert not link.is_symlink()

    def test_workspace_claude_md_absent_when_no_agents_md(
        self, spawner: Spawner
    ) -> None:
        agent_root = spawner._materialize_agent_dir(_msg(agents_md=None))
        link = agent_root / "workspace" / "CLAUDE.md"
        assert not link.exists()
        assert not link.is_symlink()

    def test_workspace_claude_md_removed_when_agents_md_cleared(
        self, spawner: Spawner
    ) -> None:
        """Same contract as AGENTS.md but for the CLAUDE.md bridge
        used by Claude Code. A stale CLAUDE.md copy left behind
        would expose the previous session's instructions to the
        next turn even though the canonical manifest dropped them.
        """
        spawner._materialize_agent_dir(_msg(agents_md="# first"))
        agent_root = spawner._agent_dirs_root / "agent-x"
        path = agent_root / "workspace" / "CLAUDE.md"
        assert path.is_file()

        spawner._materialize_agent_dir(_msg(agents_md=None))

        assert not path.exists()
        assert not path.is_symlink()

    def test_workspace_agents_md_removed_when_agents_md_cleared(
        self, spawner: Spawner
    ) -> None:
        """If an earlier spawn materialized ``workspace/AGENTS.md``
        and a later spawn clears ``agents_md``, the old copy must
        be removed. prune wipes ``agent_root/AGENTS.md`` from the
        managed tree but preserves ``workspace/`` wholesale, so
        without an explicit reconcile the previous session's
        instructions would leak into the next spawn.
        """
        # First spawn sets agents_md → copy created.
        spawner._materialize_agent_dir(_msg(agents_md="# first"))
        agent_root = spawner._agent_dirs_root / "agent-x"
        path = agent_root / "workspace" / "AGENTS.md"
        assert path.is_file()
        assert path.read_text().startswith("# first")

        # Second spawn clears agents_md → the slot must go empty.
        spawner._materialize_agent_dir(_msg(agents_md=None))

        assert not path.exists()
        assert not path.is_symlink()
        # The managed AGENTS.md one level up is gone too (prune).
        assert not (agent_root / "AGENTS.md").exists()

    def test_engine_secrets_not_persisted_to_disk(self, spawner: Spawner) -> None:
        """#184: secrets flow into the subprocess environment, never the
        disk. The materializer must NOT drop a ``.env`` file under any
        engine-specific config directory, because that file is readable
        from the agent sandbox (``workspace/`` can reach ``../.claude/``
        etc.) and the LLM's ``Read`` tool would happily exfiltrate the
        plaintext key.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(
                engine="gemini-cli",
                engine_secrets={"GEMINI_API_KEY": "sk-abc"},
                files={".gemini/settings.json": "{}"},
            )
        )

        assert not (agent_root / ".gemini" / ".env").exists()
        assert not (agent_root / ".codex" / ".env").exists()
        assert not (agent_root / ".claude" / ".env").exists()

    def test_codex_engine_does_not_create_empty_codex_dir(
        self, spawner: Spawner
    ) -> None:
        """codex 엔진이라도 ``.codex/*`` 오버레이가 없으면 빈
        디렉토리를 남기지 않는다. ``CODEX_HOME`` 리다이렉트는
        오버레이 유무로 스코핑되기 때문에(``spawn()`` 참조), 빈
        디렉토리를 강제 생성하면 host ``~/.codex/auth.json`` 기반
        스타트업 경로를 일관적으로 깨뜨리게 된다.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(engine="codex", files={})
        )
        assert not (agent_root / ".codex").exists()


class TestMaterializePrune:
    """Re-running materialize with a different manifest must converge
    the disk tree to exactly the new manifest — deletions included.
    """

    def test_prune_removes_file_not_in_new_manifest(
        self, spawner: Spawner
    ) -> None:
        # First spawn: two skills
        spawner._materialize_agent_dir(
            _msg(
                files={
                    "skills/coder/SKILL.md": "c1",
                    "skills/reviewer/SKILL.md": "r1",
                }
            )
        )

        # Second spawn: reviewer dropped from manifest
        agent_root = spawner._materialize_agent_dir(
            _msg(files={"skills/coder/SKILL.md": "c2"})
        )

        assert (agent_root / "skills" / "coder" / "SKILL.md").read_text() == "c2"
        assert not (agent_root / "skills" / "reviewer" / "SKILL.md").exists()
        assert not (agent_root / "skills" / "reviewer").exists()

    def test_prune_preserves_workspace_contents(
        self, spawner: Spawner
    ) -> None:
        spawner._materialize_agent_dir(_msg())

        # Agent dropped a file in workspace/ during the last spawn
        agent_root = spawner._agent_dirs_root / "agent-x"
        (agent_root / "workspace").mkdir(parents=True, exist_ok=True)
        runtime_file = agent_root / "workspace" / "scratch.txt"
        runtime_file.write_text("runtime state")

        # New spawn with a completely different manifest
        spawner._materialize_agent_dir(
            _msg(
                agents_md="# new version",
                files={".codex/config.toml": "[x]\n"},
            )
        )

        assert runtime_file.read_text() == "runtime state"
        assert runtime_file.exists()

    def test_workspace_agents_md_refreshed_even_if_tampered_gemini(
        self, spawner: Spawner
    ) -> None:
        """The gemini-cli real-file copy is materializer-owned. If
        a previous session bypassed the 0o400 speedbump (``chmod
        u+w`` then overwrite), the next spawn must restore the
        canonical bytes. This is the "tamper is scoped to one
        session" guarantee — without it a single successful
        tamper would persist across spawns.
        """
        spawner._materialize_agent_dir(
            _msg(agents_md="# real", engine="gemini-cli")
        )
        agent_root = spawner._agent_dirs_root / "agent-x"
        path = agent_root / "workspace" / "AGENTS.md"
        assert path.read_text().startswith("# real")

        # Simulate the agent chmod'ing and rewriting the copy with
        # malicious text.
        os.chmod(path, 0o600)
        path.write_text("# tampered instructions")

        spawner._materialize_agent_dir(
            _msg(agents_md="# real", engine="gemini-cli")
        )

        assert path.is_file()
        assert not path.is_symlink()
        assert path.read_text().startswith("# real")
        # The mode is restored to the speedbump too.
        assert path.stat().st_mode & 0o777 == 0o400

    def test_workspace_agents_md_symlink_restored_after_tamper_codex(
        self, spawner: Spawner
    ) -> None:
        """For codex/claude-code the symlink IS the isolation
        contract: reads resolve, writes resolve to a sandbox-external
        path and fail. In a unit test there's no sandbox, so we
        instead verify that the materializer restores a fresh
        symlink on every spawn regardless of what the previous
        session left behind — if a previous session somehow
        replaced the symlink with a regular file, the next spawn
        must put the symlink back so the isolation contract
        re-engages.
        """
        spawner._materialize_agent_dir(
            _msg(agents_md="# real", engine="codex")
        )
        agent_root = spawner._agent_dirs_root / "agent-x"
        path = agent_root / "workspace" / "AGENTS.md"
        assert path.is_symlink()

        # Simulate the agent replacing the symlink with a regular
        # file (unlink + create) — this is one of the tamper paths
        # that costs a little more than the real-file copy case
        # because the symlink has to be removed first.
        path.unlink()
        path.write_text("# tampered instructions")
        assert path.is_file()
        assert not path.is_symlink()

        spawner._materialize_agent_dir(
            _msg(agents_md="# real", engine="codex")
        )

        # The symlink is back — the isolation contract is restored.
        assert path.is_symlink()
        assert os.readlink(path) == "../AGENTS.md"
        assert path.read_text().startswith("# real")

    def test_prune_wipes_engine_config_when_removed(
        self, spawner: Spawner
    ) -> None:
        spawner._materialize_agent_dir(
            _msg(files={".codex/config.toml": "old"})
        )

        agent_root = spawner._materialize_agent_dir(_msg(files={}))

        assert not (agent_root / ".codex" / "config.toml").exists()
        # The empty .codex/ dir is also gone — no engine-specific
        # re-seed keeps it around, which is important so codex agents
        # without an overlay can fall back to host ``~/.codex/``.
        assert not (agent_root / ".codex").exists()

    def test_prune_removes_stale_symlinks(self, spawner: Spawner) -> None:
        # First spawn creates CLAUDE.md symlink
        spawner._materialize_agent_dir(_msg())
        agent_root = spawner._agent_dirs_root / "agent-x"
        link = agent_root / "CLAUDE.md"
        assert link.is_symlink()

        # Re-materialize with no agents_md → symlink target would be
        # dead, so the materializer should remove the stale link and
        # not recreate it until there's something to point at.
        spawner._materialize_agent_dir(_msg(agents_md=None))
        # AGENTS.md should be gone; CLAUDE.md symlink should be gone.
        assert not (agent_root / "AGENTS.md").exists()
        assert not link.exists()
        assert not link.is_symlink()


class TestMaterializeValidation:
    def test_rejects_invalid_path(self, spawner: Spawner) -> None:
        with pytest.raises(AgentFilePathError):
            spawner._materialize_agent_dir(
                _msg(files={"workspace/evil.md": "x"})
            )

    def test_rejects_path_traversal(self, spawner: Spawner) -> None:
        with pytest.raises(AgentFilePathError):
            spawner._materialize_agent_dir(
                _msg(files={"skills/../escape.md": "x"})
            )

    def test_rejects_unwhitelisted_extension(
        self, spawner: Spawner
    ) -> None:
        # Issue #112 — ``.sh``/``.py``/etc. are now whitelisted so the
        # materializer accepts skill-local scripts. Use ``.bash`` (an
        # intentionally-omitted shell variant) for the rejection case.
        with pytest.raises(AgentFilePathError):
            spawner._materialize_agent_dir(
                _msg(files={"skills/coder/run.bash": "#!/bin/bash"})
            )

    @pytest.mark.parametrize(
        "bad_id",
        [
            "../escape",
            "../../etc",
            "/etc",
            "/absolute",
            "a/b",
            "..",
            ".",
            "with space",
            "with.dot",
            "\x00null",
        ],
    )
    def test_rejects_malicious_agent_id(
        self, spawner: Spawner, tmp_path: Path, bad_id: str
    ) -> None:
        """A spawn frame with ``agent_id`` that would escape the root
        must fail before any filesystem op — otherwise the prune walk
        could delete files outside the managed root.
        """
        # Seed a sibling directory that a successful escape would clobber.
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        canary = sibling / "keep-me.txt"
        canary.write_text("must not be touched")

        with pytest.raises(AgentFilePathError):
            spawner._materialize_agent_dir(_msg(agent_id=bad_id))

        # The sibling directory must be intact — no collateral damage.
        assert canary.read_text() == "must not be touched"


class TestClaudeCodeDefaultSettings:
    """claude-code 엔진은 cwd 단위 ``.claude/settings.json``만 권한
    소스로 인정한다 (어댑터가 ``setting_sources=["project"]`` 고정).
    admin manifest가 settings.json을 보내지 않으면 SDK의 기본 ask
    모드 + headless 환경 조합으로 모든 도구 호출이 거부돼 에이전트
    가 무용지물이 된다. 이슈 #111 — 빈 슬롯을 디폴트 화이트리스트
    로 채워서 안전한 동작을 보장하되, admin이 manifest로 같은 경로
    파일을 보내면 admin 버전이 우선한다 (per-agent override).
    """

    def test_default_written_for_claude_code(self, spawner: Spawner) -> None:
        agent_root = spawner._materialize_agent_dir(
            _msg(engine="claude-code")
        )
        path = agent_root / ".claude" / "settings.json"
        assert path.is_file()

    def test_default_includes_websearch_and_webfetch(
        self, spawner: Spawner
    ) -> None:
        """리그레션 가드: 디폴트가 비어있는 ``permissions.allow``로
        퇴화하면 이슈 #111이 다시 재현된다 (WebSearch가 거부돼
        '권한이 승인되지 않아…' 응답).
        """
        import json
        agent_root = spawner._materialize_agent_dir(
            _msg(engine="claude-code")
        )
        path = agent_root / ".claude" / "settings.json"
        body = json.loads(path.read_text())
        allow = body.get("permissions", {}).get("allow", [])
        assert "WebSearch" in allow
        assert "WebFetch" in allow

    def test_default_chmod_600(self, spawner: Spawner) -> None:
        """다른 manifest 파일과 동일한 권한 정책. 0o600 — owner rw."""
        agent_root = spawner._materialize_agent_dir(
            _msg(engine="claude-code")
        )
        path = agent_root / ".claude" / "settings.json"
        assert path.stat().st_mode & 0o777 == 0o600

    @pytest.mark.parametrize("engine", ["codex", "gemini-cli", "openhands"])
    def test_no_default_for_other_engines(
        self, spawner: Spawner, engine: str
    ) -> None:
        """``.claude/settings.json``은 claude-code 전용이다. 다른
        엔진은 자기 엔진 디렉토리(.codex/, .gemini/, .openhands/)
        만 본다 — 빈 .claude/ 디렉토리가 남으면 prune 일관성도
        깨지고 디스크 노이즈가 된다.
        """
        agent_root = spawner._materialize_agent_dir(_msg(engine=engine))
        assert not (agent_root / ".claude" / "settings.json").exists()

    def test_admin_manifest_overrides_default(self, spawner: Spawner) -> None:
        """admin이 자기 정책을 manifest로 보내면 그 파일이 단일
        진실 원천이 된다. 디폴트와 머지하지 않는다 — 머지 의미론
        을 admin이 학습할 필요가 없게 단순 대체로 일관.
        """
        custom = '{"permissions": {"allow": ["Read", "Glob"]}}'
        agent_root = spawner._materialize_agent_dir(
            _msg(
                engine="claude-code",
                files={".claude/settings.json": custom},
            )
        )
        path = agent_root / ".claude" / "settings.json"
        assert path.read_text() == custom

    def test_default_restored_after_respawn(self, spawner: Spawner) -> None:
        """prune은 매 spawn에서 .claude/를 통째로 지운다. 디폴트
        는 결정론적으로 매번 다시 작성돼야 한다 — "한 번 만들고
        끝"이면 manifest가 비는 spawn 사이에서 권한이 사라짐.
        """
        spawner._materialize_agent_dir(_msg(engine="claude-code"))
        agent_root = spawner._materialize_agent_dir(_msg(engine="claude-code"))
        path = agent_root / ".claude" / "settings.json"
        assert path.is_file()
        assert path.stat().st_mode & 0o777 == 0o600

    def test_workspace_claude_symlink_for_claude_code(
        self, spawner: Spawner
    ) -> None:
        """claude CLI는 ``cwd + '/.claude/settings.json'`` 만 보고
        walk-up 하지 않는다 (확인된 동작 — debug log:
        ``Broken symlink or missing file encountered for
        settings.json at path: <workspace>/.claude/settings.json``).
        어댑터는 cwd를 ``workspace/`` 로 고정하므로 spawner 가
        ``workspace/.claude → ../.claude`` 심볼릭 링크를 만들어
        둬야 settings.json 이 발견된다. AGENTS.md/CLAUDE.md 의
        sandbox-into-workspace 브리지 패턴과 동일.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(engine="claude-code")
        )
        link = agent_root / "workspace" / ".claude"
        assert link.is_symlink()
        assert os.readlink(link) == "../.claude"
        # 링크를 따라가면 실제 settings.json 이 보여야 한다.
        assert (link / "settings.json").is_file()

    @pytest.mark.parametrize("engine", ["codex", "gemini-cli", "openhands"])
    def test_no_workspace_claude_symlink_for_other_engines(
        self, spawner: Spawner, engine: str
    ) -> None:
        """심볼릭 링크는 claude-code 전용이다. 다른 엔진의
        workspace에 ``.claude`` 가 생기면 prune 일관성과 디스크
        노이즈 측면에서 손해.
        """
        agent_root = spawner._materialize_agent_dir(_msg(engine=engine))
        link = agent_root / "workspace" / ".claude"
        assert not link.exists()
        assert not link.is_symlink()


# ── Codex host-auth bridge (post-#213 follow-up) ────────────────────


class TestCodexHostAuthSymlink:
    """#213 made ``Spawner.spawn`` redirect ``CODEX_HOME`` at the
    per-agent ``.codex/`` when the manifest carries a codex overlay.
    Deployments that authenticate codex via ``codex auth login``
    (host ``~/.codex/auth.json``, no ``OPENAI_API_KEY`` in
    ``engine_secrets``) then broke because codex could no longer find
    the host credentials — the first turn started and then completed
    with an empty assistant message, appearing stuck.

    The materializer restores pre-#213 auth discovery by symlinking
    the host ``~/.codex/auth.json`` into the per-agent codex home
    when the redirect is about to fire.
    """

    @pytest.fixture
    def fake_host_codex(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Point ``Path.home()`` at a tmp dir with a populated
        ``.codex/auth.json`` so tests don't touch the real host."""
        home = tmp_path / "home-user"
        codex_dir = home / ".codex"
        codex_dir.mkdir(parents=True)
        auth = codex_dir / "auth.json"
        auth.write_text('{"auth_mode":"chatgpt","tokens":{"fake":"fake"}}')
        os.chmod(auth, 0o600)
        monkeypatch.setenv("HOME", str(home))
        return auth

    def test_symlink_created_when_codex_overlay_present(
        self,
        spawner: Spawner,
        fake_host_codex: Path,
    ) -> None:
        """오버레이 있는 codex 에이전트는 host auth.json 을 가리키는
        symlink 를 ``.codex/auth.json`` 에 받아야 한다. 없으면 codex
        app-server가 auth 없이 구동되어 LLM 응답이 빈 값으로 완료됨.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(
                engine="codex",
                files={".codex/config.toml": "[mcp_servers.x]\n"},
            )
        )

        link = agent_root / ".codex" / "auth.json"
        assert link.is_symlink()
        # 링크 타겟은 host ``~/.codex/auth.json`` — Path.home() 이
        # 가리키는 곳. 실제 경로 비교로 검증.
        assert Path(os.readlink(link)) == fake_host_codex
        # 링크를 따라가면 auth 컨텐츠가 읽혀야 한다
        assert "chatgpt" in link.read_text()

    def test_no_symlink_when_host_auth_missing(
        self,
        spawner: Spawner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """host 에 ``~/.codex/auth.json`` 이 아예 없는 설치(fresh host,
        codex 로그인 안 됨)에서는 symlink 생성을 건너뛰어야 한다.
        dead link 를 남기면 codex 가 "read permission denied" 같은
        혼란스러운 에러를 내게 된다.
        """
        home = tmp_path / "home-nohost-auth"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        agent_root = spawner._materialize_agent_dir(
            _msg(
                engine="codex",
                files={".codex/config.toml": "[mcp_servers.x]\n"},
            )
        )

        link = agent_root / ".codex" / "auth.json"
        assert not link.is_symlink()
        assert not link.exists()

    def test_no_symlink_when_no_codex_overlay(
        self,
        spawner: Spawner,
        fake_host_codex: Path,
    ) -> None:
        """``.codex/*`` 오버레이가 없는 codex 에이전트는 ``CODEX_HOME``
        이 리다이렉트되지 않으므로 auth symlink 도 불필요하다. 빈
        ``.codex/`` 디렉토리가 남지도 말아야 한다.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(engine="codex", files={})
        )

        assert not (agent_root / ".codex").exists()

    def test_admin_authored_auth_preserved(
        self,
        spawner: Spawner,
        fake_host_codex: Path,
    ) -> None:
        """admin 이 manifest 로 ``.codex/auth.json`` 을 명시하면 그
        파일이 호스트 symlink 를 이긴다. 서비스 계정 토큰을 per-
        agent 로 주입하는 운영 시나리오. 파일-쓰기 루프가 먼저
        실행되어 파일이 존재하므로 symlink 블록이 건드리지 않는다.
        """
        admin_auth = '{"auth_mode":"api","OPENAI_API_KEY":"sk-admin"}'
        agent_root = spawner._materialize_agent_dir(
            _msg(
                engine="codex",
                files={
                    ".codex/config.toml": "[mcp_servers.x]\n",
                    ".codex/auth.json": admin_auth,
                },
            )
        )

        path = agent_root / ".codex" / "auth.json"
        assert path.is_file()
        assert not path.is_symlink()
        assert path.read_text() == admin_auth

    @pytest.mark.parametrize(
        "engine", ["claude-code", "gemini-cli", "openhands"]
    )
    def test_non_codex_engines_do_not_get_auth_symlink(
        self,
        spawner: Spawner,
        fake_host_codex: Path,
        engine: str,
    ) -> None:
        """host auth 다리 로직은 codex 엔진에서만 발동. 다른 엔진에
        ``.codex/*`` 파일이 어쩌다 실려도 symlink 를 만들어선 안 된다.
        """
        agent_root = spawner._materialize_agent_dir(
            _msg(
                engine=engine,
                files={".codex/config.toml": "[x]\n"},
            )
        )

        link = agent_root / ".codex" / "auth.json"
        assert not link.is_symlink()
        assert not link.exists()

    def test_stale_auth_symlink_refreshed_across_spawns(
        self,
        spawner: Spawner,
        fake_host_codex: Path,
    ) -> None:
        """prune 이 ``.codex/`` 를 통째로 날린 뒤 두 번째 spawn 에서도
        symlink 가 정확히 재생성돼야 한다. 호스트 ``~/.codex/auth.json``
        이 rotate 되어도 다음 spawn 에서 fresh 타겟을 가리키게 된다.
        """
        spawner._materialize_agent_dir(
            _msg(
                engine="codex",
                files={".codex/config.toml": "[mcp_servers.x]\n"},
            )
        )
        # 두 번째 spawn — prune 후 재생성
        agent_root = spawner._materialize_agent_dir(
            _msg(
                engine="codex",
                files={".codex/config.toml": "[mcp_servers.y]\n"},
            )
        )

        link = agent_root / ".codex" / "auth.json"
        assert link.is_symlink()
        assert Path(os.readlink(link)) == fake_host_codex


# ── Symlink follow-refusal (Issue #186) ─────────────────────────────


class TestMaterializeRefusesSymlinkFollow:
    """Previously ``Path.write_text`` followed symlinks at the final
    component. A malicious previous-session agent could leave a
    symlink at ``workspace/MEMORY.md`` or a similar slot pointing to
    an arbitrary path, and the next materialize would write through
    it. All materialize-time writes now go through ``safe_write_text``
    which uses ``O_NOFOLLOW``.
    """

    def test_memory_md_symlink_is_refused(
        self,
        spawner: Spawner,
        agent_dirs_root: Path,
        tmp_path: Path,
    ) -> None:
        """MEMORY.md의 seed 로직은 ``if not memory_md.exists() and not
        memory_md.is_symlink()`` 가드로 기존 symlink를 건드리지 않고
        통과해야 한다 — write 경로로 들어가도 ``safe_write_text`` 가
        ELOOP로 거절한다.
        """
        # Pre-seed a symlink that would otherwise redirect writes.
        victim = tmp_path / "outside.txt"
        victim.write_text("sacred")
        agent_root = agent_dirs_root / "agent-x"
        (agent_root / "workspace").mkdir(parents=True, exist_ok=True)
        memory_md = agent_root / "workspace" / "MEMORY.md"
        memory_md.symlink_to(victim)

        # Materialize — must NOT write through the symlink.
        spawner._materialize_agent_dir(_msg(agent_id="agent-x"))

        # Victim file untouched.
        assert victim.read_text() == "sacred"

    def test_gemini_workspace_bridge_refuses_symlink(
        self,
        spawner: Spawner,
        agent_dirs_root: Path,
        tmp_path: Path,
    ) -> None:
        """Gemini 분기의 ``workspace/AGENTS.md`` real-copy write 경로가
        이전 세션에서 심어둔 symlink를 unlink로 제거하고 새 파일을 만든다.
        O_NOFOLLOW는 unlink와 open 사이 race가 발생해도 ELOOP로 방어한다.
        """
        victim = tmp_path / "outside.txt"
        victim.write_text("sacred")
        agent_root = agent_dirs_root / "agent-x"
        (agent_root / "workspace").mkdir(parents=True, exist_ok=True)

        # Run once to establish the agent_root in a sane state.
        spawner._materialize_agent_dir(
            _msg(agent_id="agent-x", engine="gemini-cli")
        )

        # Replace the real copy with a symlink pointing outside.
        bridge = agent_root / "workspace" / "AGENTS.md"
        if bridge.exists() or bridge.is_symlink():
            bridge.unlink()
        bridge.symlink_to(victim)

        # Re-materialize. The unlink-then-write path handles the
        # symlink cleanly and writes a real file inside workspace.
        spawner._materialize_agent_dir(
            _msg(agent_id="agent-x", engine="gemini-cli")
        )

        assert victim.read_text() == "sacred"
        assert not bridge.is_symlink()
        assert bridge.is_file()


class TestMemoryMaterialize:
    """Issue #237 — ``memory/notes.md`` is seeded from the DB snapshot on
    every materialize so the cluster's last-known memory content is the
    starting point for every new session. The file is the runtime truth
    thereafter; sync-back from the machine flushes file → DB on change.
    """

    def test_writes_memory_notes_with_content(
        self, spawner: Spawner, agent_dirs_root: Path
    ) -> None:
        msg = _msg()
        msg.memory_md = "## User\nPrefers Korean responses."
        agent_root = spawner._materialize_agent_dir(msg)
        notes = agent_root / "memory" / "notes.md"
        assert notes.is_file()
        assert notes.read_text() == "## User\nPrefers Korean responses."
        assert notes.stat().st_mode & 0o777 == 0o600
        assert (agent_root / "memory").stat().st_mode & 0o777 == 0o700

    def test_writes_empty_memory_file_when_db_has_none(
        self, spawner: Spawner, agent_dirs_root: Path
    ) -> None:
        """Fresh agent with ``memory_md=None`` still gets an empty file so
        the agent knows the path exists and can start writing."""
        msg = _msg()
        msg.memory_md = None
        agent_root = spawner._materialize_agent_dir(msg)
        notes = agent_root / "memory" / "notes.md"
        assert notes.is_file()
        assert notes.read_text() == ""

    def test_rematerialize_seeds_from_db_snapshot(
        self, spawner: Spawner, agent_dirs_root: Path
    ) -> None:
        """On re-spawn the prune step wipes ``memory/``; the DB snapshot
        (which should already reflect the last sync-back) is then
        written. Simulates restart / machine move."""
        msg = _msg()
        msg.memory_md = "first"
        spawner._materialize_agent_dir(msg)

        msg.memory_md = "second (after sync-back)"
        agent_root = spawner._materialize_agent_dir(msg)
        assert (agent_root / "memory" / "notes.md").read_text() == (
            "second (after sync-back)"
        )

    def test_agents_md_mentions_memory_notes_path(
        self, spawner: Spawner
    ) -> None:
        """AGENTS.md convention points at ``memory/notes.md`` so agents
        of every engine learn where to write long-term memory."""
        agent_root = spawner._materialize_agent_dir(_msg())
        body = (agent_root / "AGENTS.md").read_text()
        assert "memory/notes.md" in body
        # Ephemeral convention is documented too.
        assert "ephemeral" in body.lower()
