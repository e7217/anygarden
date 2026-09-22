"""Resolve the database URL that Alembic migrations should target.

Split out of ``db/migrations/env.py`` so the precedence rules can be unit
tested without standing up an Alembic context — ``env.py`` runs as an
Alembic script, not as an importable module.

Why there is deliberately no default (#647)
-------------------------------------------
``alembic.ini`` used to hardcode ``sqlite+aiosqlite:///%(here)s/anygarden.db``
while the application reads ``ANYGARDEN_DB_URL`` (defaulting to
``~/.anygarden/anygarden.db``). Every ``alembic`` invocation therefore
created and migrated a *phantom* database that the app never opened, and
reported success while doing it. The divergence survived undetected for
months because the phantom file is gitignored.

Any default at all reintroduces that failure mode: whatever we pick will
eventually disagree with the app's configuration, and the disagreement
hides behind a success log. So when no target is specified we raise
instead, naming every way to specify one.

Dropping the default has a second benefit: ``alembic downgrade -1`` can no
longer silently downgrade a live database, because it has no implicit
target to fall back to.
"""

from __future__ import annotations

from collections.abc import Mapping

DB_URL_ENV_VAR = "ANYGARDEN_DB_URL"

X_ARG_NAME = "db_url"

NO_URL_MESSAGE = (
    "No database URL configured for Alembic.\n"
    "\n"
    "There is no default on purpose: a default that drifts from the "
    "application's configuration migrates the wrong database and still "
    "reports success (#647). Name the target explicitly with one of:\n"
    "\n"
    "  1. alembic -x db_url=sqlite+aiosqlite:////tmp/scratch.db upgrade head\n"
    f"  2. {DB_URL_ENV_VAR}=sqlite+aiosqlite:///... alembic upgrade head\n"
    "  3. set sqlalchemy.url in alembic.ini (fixed deployments only)\n"
    "\n"
    "Day-to-day development needs none of these: the server migrates its "
    "own database at startup."
)


def resolve_db_url(
    x_args: Mapping[str, str],
    env: Mapping[str, str],
    config_url: str | None,
    *,
    config_file: str | None,
) -> str:
    """Return the database URL Alembic should operate on.

    Precedence, highest first:

    1. ``-x db_url=...`` — an explicit, one-shot CLI override.
    2. ``config_url`` when ``config_file`` is ``None`` — the caller built
       an :class:`alembic.config.Config` in code and set the URL on it
       (``app.py`` startup migrations, ``tests/test_migrations.py``).
       That is an explicit argument from the caller and must outrank the
       ambient environment, otherwise a stray ``ANYGARDEN_DB_URL`` in the
       developer's shell would redirect a test's throwaway database at
       their live one.
    3. ``ANYGARDEN_DB_URL`` — the same variable the application reads, so
       an ad-hoc ``alembic`` run targets whatever the app would open.
    4. ``config_url`` read from an ini file — the escape hatch for
       deployments that pin the URL in ``alembic.ini``. Empty by default.

    :param x_args: ``context.get_x_argument(as_dictionary=True)``.
    :param env: process environment, normally ``os.environ``.
    :param config_url: ``config.get_main_option("sqlalchemy.url")``.
    :param config_file: ``config.config_file_name``; ``None`` means the
        config was assembled programmatically rather than read from disk.
    :raises CommandError: when no source supplies a URL.
    """
    from alembic.util.exc import CommandError

    x_url = x_args.get(X_ARG_NAME)
    if x_url:
        return x_url

    if config_url and config_file is None:
        return config_url

    env_url = env.get(DB_URL_ENV_VAR)
    if env_url:
        return env_url

    if config_url:
        return config_url

    raise CommandError(NO_URL_MESSAGE)
