"""Precedence rules for the Alembic migration target URL (#647).

``alembic.ini`` used to hardcode a database the application never opens,
so every ``alembic`` invocation migrated a phantom file and reported
success. These tests pin the replacement behaviour: an explicit target
always wins, and "no target at all" fails loudly instead of guessing.
"""

from __future__ import annotations

import pytest
from alembic.util.exc import CommandError

from anygarden.db.migration_url import DB_URL_ENV_VAR, resolve_db_url

X = "sqlite+aiosqlite:///from-x.db"
ENV = "sqlite+aiosqlite:///from-env.db"
INI = "sqlite+aiosqlite:///from-ini.db"
CODE = "sqlite+aiosqlite:///from-code.db"


class TestResolveDbUrl:
    def test_x_argument_beats_env_and_ini(self) -> None:
        """``-x db_url=`` is the one-shot override and outranks everything."""
        assert (
            resolve_db_url(
                {"db_url": X},
                {DB_URL_ENV_VAR: ENV},
                INI,
                config_file="alembic.ini",
            )
            == X
        )

    def test_env_beats_ini(self) -> None:
        """The app's own env var outranks a checked-in ini default.

        The ini value is the one that drifted out of sync in #647; the env
        var is what the running application reads, so it is the better
        guess at "the database that actually matters".
        """
        assert (
            resolve_db_url({}, {DB_URL_ENV_VAR: ENV}, INI, config_file="alembic.ini")
            == ENV
        )

    def test_ini_used_when_nothing_else_set(self) -> None:
        """Deployments that pin ``sqlalchemy.url`` keep working."""
        assert resolve_db_url({}, {}, INI, config_file="alembic.ini") == INI

    def test_programmatic_url_beats_env(self) -> None:
        """A URL set on a code-built Config is an explicit argument.

        ``app.py`` startup migrations and ``tests/test_migrations.py``
        construct ``Config()`` with no ini file and set the URL directly.
        A stray ``ANYGARDEN_DB_URL`` in the developer's shell must not
        redirect those at a different database.
        """
        assert (
            resolve_db_url({}, {DB_URL_ENV_VAR: ENV}, CODE, config_file=None) == CODE
        )

    def test_x_argument_still_beats_programmatic_url(self) -> None:
        assert (
            resolve_db_url({"db_url": X}, {}, CODE, config_file=None) == X
        )

    def test_missing_url_raises_with_every_option_named(self) -> None:
        """No default: fail loudly and say how to supply a target."""
        with pytest.raises(CommandError) as excinfo:
            resolve_db_url({}, {}, None, config_file="alembic.ini")

        message = str(excinfo.value)
        assert "-x db_url" in message
        assert DB_URL_ENV_VAR in message
        assert "alembic.ini" in message

    @pytest.mark.parametrize("empty", ["", None])
    def test_empty_values_are_treated_as_unset(self, empty: str | None) -> None:
        """``FOO=`` means "not configured", matching ``_apply_runtime_env``."""
        assert (
            resolve_db_url(
                {"db_url": empty} if empty is not None else {},
                {DB_URL_ENV_VAR: empty} if empty is not None else {},
                INI,
                config_file="alembic.ini",
            )
            == INI
        )

    def test_empty_programmatic_url_falls_through_to_env(self) -> None:
        assert (
            resolve_db_url({}, {DB_URL_ENV_VAR: ENV}, "", config_file=None) == ENV
        )
