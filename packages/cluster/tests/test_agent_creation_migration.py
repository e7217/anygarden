"""Adding request keys must preserve agents and their dependent records."""
import pytest
from alembic import command
from anygarden.db.models import Agent, AgentFile, Participant, Room
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import IntegrityError

from .test_migrations import _alembic_config


def test_creation_request_migration_preserves_existing_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("ANYGARDEN_DB_URL", raising=False)
    path = tmp_path / "creation.db"
    config = _alembic_config(str(path))
    command.upgrade(config, "076_pi_native_auth")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as db:
        db.execute(insert(Agent.__table__).values(id="existing", name="Reviewer", engine="echo"))
        db.execute(insert(Room.__table__).values(id="dm", name="DM", is_dm=True, representative_agent_id="existing"))
        db.execute(insert(Participant.__table__).values(id="member", room_id="dm", agent_id="existing", role="member"))
        db.execute(insert(AgentFile.__table__).values(agent_id="existing", path="AGENTS.md", content="Keep me"))
    command.upgrade(config, "head")
    with engine.begin() as db:
        row = db.execute(select(Agent.__table__).where(Agent.id == "existing")).mappings().one()
        assert row["creation_request_key"] is None
        assert row["creation_request_fingerprint"] is None
        assert db.execute(select(Room.representative_agent_id)).scalar_one() == "existing"
        assert db.execute(select(Participant.agent_id)).scalar_one() == "existing"
        assert db.execute(select(AgentFile.content)).scalar_one() == "Keep me"
        db.execute(insert(Agent.__table__).values(id="created", name="New", engine="echo", creation_request_key="key", creation_request_fingerprint="fingerprint"))
    with engine.begin() as db, pytest.raises(IntegrityError):
        db.execute(insert(Agent.__table__).values(id="duplicate", name="Duplicate", engine="echo", creation_request_key="key"))
    with engine.connect() as db:
        assert not db.execute(text("PRAGMA foreign_key_check")).all()
    engine.dispose()
