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

    def test_engine_secrets_rendered_for_gemini(self, spawner: Spawner) -> None:
        agent_root = spawner._materialize_agent_dir(
            _msg(
                engine="gemini-cli",
                engine_secrets={"GEMINI_API_KEY": "sk-abc"},
                files={".gemini/settings.json": "{}"},
            )
        )
        env_path = agent_root / ".gemini" / ".env"
        assert env_path.read_text() == "GEMINI_API_KEY=sk-abc\n"
        assert env_path.stat().st_mode & 0o777 == 0o600


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
        # The empty .codex/ dir is also gone
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
