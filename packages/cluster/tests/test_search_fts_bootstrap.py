"""Regression tests for #473 — FTS index missing on fresh-DB bootstrap.

The ``messages_fts`` virtual table and its sync triggers are created
only by migration 008. The fresh-DB bootstrap path
(``_ensure_schema_ready`` Case 2) does ``create_all + stamp`` and never
runs migrations, so on a brand-new install the FTS table was absent and
every authenticated search hit ``OperationalError: no such table:
messages_fts`` → an unhandled 500.

These tests pin two guarantees:

1. A fresh DB bootstrapped via ``_ensure_schema_ready`` has a working
   ``messages_fts`` table whose triggers keep it in sync with
   ``messages`` (insert → searchable).
2. When the FTS index is genuinely absent, ``GET /api/v1/search``
   degrades to 503 instead of leaking a 500.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, User


class TestFreshBootstrapCreatesFts:
    @pytest.mark.asyncio
    async def test_messages_fts_exists_and_is_searchable_after_bootstrap(self) -> None:
        """A fresh DB bootstrapped via ``_ensure_schema_ready`` must have a
        ``messages_fts`` table; inserting a message must make it findable
        via the FTS MATCH query (proving the sync triggers were created).
        """
        from anygarden.app import _ensure_schema_ready

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.begin() as conn:
                # The FTS virtual table must exist.
                tables = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                }
                assert "messages_fts" in tables, (
                    "fresh bootstrap must create the messages_fts virtual table"
                )

                # Seed FK targets, then insert a message; the AFTER INSERT
                # trigger should mirror it into messages_fts.
                conn.execute(
                    text(
                        "INSERT INTO projects (id, name, created_at) "
                        "VALUES ('p-1', 'P', '2026-06-22T00:00:00+00:00')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO rooms (id, project_id, name, created_at, "
                        "is_dm, context_window_enabled, speaker_strategy, "
                        "current_speaker_index, ephemeral, allow_human_assignment) "
                        "VALUES ('r-1', 'p-1', 'R', '2026-06-22T00:00:00+00:00', "
                        "0, 0, 'mentioned_only', 0, 0, 0)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO messages "
                        "(id, room_id, participant_id, content, seq, created_at) "
                        "VALUES ('m-1', 'r-1', NULL, 'hello searchable world', 1, "
                        "'2026-06-22T00:00:00+00:00')"
                    )
                )

                rows = conn.execute(
                    text(
                        "SELECT message_id FROM messages_fts "
                        "WHERE messages_fts MATCH 'searchable'"
                    )
                ).all()
                assert [r[0] for r in rows] == ["m-1"], (
                    "FTS triggers must mirror inserted messages so search "
                    "returns the row"
                )
            sync_engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestSearchDegradesWhenIndexMissing:
    @pytest_asyncio.fixture()
    async def env_without_fts(self) -> AsyncIterator[dict]:
        """App + DB created via ``create_all`` ONLY (no FTS table), with an
        authenticated user. This reproduces the original broken
        fresh-bootstrap state where the search index is absent.
        """
        config = AnygardenSettings(
            db_url="sqlite+aiosqlite://",
            jwt_secret=secrets.token_urlsafe(32),
            log_level="DEBUG",
        )
        engine = build_engine(config.db_url)
        factory = build_session_factory(engine)
        # Deliberately do NOT create messages_fts.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with factory() as db:
            user = User(email="searcher@test.com", password_hash="x")
            db.add(user)
            await db.commit()
            user_id = user.id
            user_email = user.email

        token = create_user_token(
            user_id, user_email, False, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {"client": client, "token": token}

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_search_returns_503_not_500_when_fts_absent(
        self, env_without_fts: dict
    ) -> None:
        client = env_without_fts["client"]
        token = env_without_fts["token"]
        resp = await client.get(
            "/api/v1/search",
            params={"q": "anything"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503, (
            f"missing FTS index must degrade to 503, got {resp.status_code}: "
            f"{resp.text}"
        )
