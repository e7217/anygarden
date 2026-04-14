"""Alembic async environment configuration."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from doorae.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry-point for online migrations.

    Alembic's env.py is sync, but we need to drive an async engine. If
    this is called outside any event loop (standard CLI path), use
    ``asyncio.run``. If it's called from inside a running loop (e.g. from
    the FastAPI lifespan via ``asyncio.to_thread``, or from a pytest-asyncio
    test), delegate to a dedicated worker thread so ``asyncio.run`` gets a
    fresh event loop it can own.
    """
    try:
        asyncio.get_running_loop()
        inside_loop = True
    except RuntimeError:
        inside_loop = False

    if not inside_loop:
        asyncio.run(run_async_migrations())
        return

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(lambda: asyncio.run(run_async_migrations()))
        future.result()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
