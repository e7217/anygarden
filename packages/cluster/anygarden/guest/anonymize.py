"""Periodic anonymisation of expired / revoked guest sessions.

§11.10 of the design doc. We cannot hard-delete guest ``User`` rows
because messages reference them via ``participant_id`` with
``ON DELETE SET NULL`` and downstream analytics still want the
row to exist. Instead we:

1. Find guest users whose backing ``RoomInviteLink`` has been either
   revoked or expired *more than ``ANON_AFTER`` ago*, AND whose
   display_name has not yet been scrubbed.
2. Overwrite ``display_name`` with a fixed sentinel so no personal
   info (nickname) lingers past the grace window.

The grace window is 30 days by default — long enough for audit
investigations, short enough to satisfy casual GDPR-style requests.
Operators can run the job more aggressively via the
``--after-hours`` CLI flag in :func:`main`.

Idempotent: re-running the job on already-anonymised rows skips
them via the ``display_name != SENTINEL`` predicate, so it is safe
to schedule every day.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Participant, RoomInviteLink, User

logger = structlog.get_logger(__name__)

# The sentinel we overwrite ``display_name`` with. Intentionally
# human-readable in the UI; the frontend already shows this without
# extra formatting.
ANON_DISPLAY_NAME = "(former guest)"
# Grace window before a revoked / expired guest is scrubbed.
DEFAULT_GRACE = timedelta(days=30)


async def anonymize_expired_guests(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    grace: timedelta = DEFAULT_GRACE,
) -> int:
    """Overwrite ``display_name`` on guest User rows past the grace
    window.

    Returns the number of rows updated. Safe to call repeatedly —
    the subsequent passes find nothing to do.
    """
    now = now or datetime.now(timezone.utc)
    threshold = now - grace

    # Find candidates: anonymous User rows that have a Participant
    # in some room whose invite link is either revoked_at <= threshold
    # OR expires_at <= threshold. A guest may have accepted the same
    # invite id only once, so a simple join suffices.
    stmt = (
        select(User.id)
        .join(Participant, Participant.user_id == User.id)
        .join(
            RoomInviteLink,
            RoomInviteLink.room_id == Participant.room_id,
        )
        .where(
            User.is_anonymous.is_(True),
            User.display_name != ANON_DISPLAY_NAME,
            or_(
                RoomInviteLink.revoked_at <= threshold,
                RoomInviteLink.expires_at <= threshold,
            ),
        )
        .distinct()
    )

    candidates = [row[0] for row in (await db.execute(stmt)).all()]
    if not candidates:
        return 0

    # Bulk update rather than per-row to keep the log concise. We
    # still go through the ORM so ``updated_at``-style hooks run if
    # anything is added later.
    await db.execute(
        update(User)
        .where(User.id.in_(candidates))
        .values(display_name=ANON_DISPLAY_NAME)
    )
    await db.commit()
    logger.info(
        "guest.anonymized",
        count=len(candidates),
        threshold=threshold.isoformat(),
    )
    return len(candidates)


async def _run_once(
    *,
    db_url: str,
    grace_hours: int,
    session_factory_override: Callable[[], AsyncSession] | None = None,
) -> int:
    """Entry helper — open an engine, run the pass, close the engine.

    ``session_factory_override`` is a hook for tests that already hold
    an engine open and don't want a second one.
    """
    if session_factory_override is not None:
        async with session_factory_override() as db:
            return await anonymize_expired_guests(
                db, grace=timedelta(hours=grace_hours)
            )

    engine = build_engine(db_url)
    try:
        factory = build_session_factory(engine)
        async with factory() as db:
            return await anonymize_expired_guests(
                db, grace=timedelta(hours=grace_hours)
            )
    finally:
        await engine.dispose()


def main() -> None:
    """CLI entry — ``python -m anygarden.guest.anonymize``.

    Intended to run from cron / systemd timer. Exits 0 with the
    number of rows updated logged. A non-zero exit code is reserved
    for configuration errors.
    """
    parser = argparse.ArgumentParser(description="Anonymise expired guest rows.")
    parser.add_argument(
        "--db-url",
        required=True,
        help="SQLAlchemy async DB URL, e.g. sqlite+aiosqlite:///anygarden.db",
    )
    parser.add_argument(
        "--after-hours",
        type=int,
        default=int(DEFAULT_GRACE.total_seconds() // 3600),
        help=(
            "Grace window in hours before a revoked/expired guest is "
            "anonymised. Defaults to the RFC value (30 days)."
        ),
    )
    args = parser.parse_args()

    count = asyncio.run(
        _run_once(db_url=args.db_url, grace_hours=args.after_hours)
    )
    # A structlog event already fires from ``anonymize_expired_guests``
    # when rows are updated. Emit a single summary event here so cron
    # wrappers have a reliable completion marker even on no-op runs,
    # and avoid ``print`` so the output goes through the same pipeline
    # as the rest of the application logs.
    logger.info("guest.anonymize.completed", count=count)


if __name__ == "__main__":  # pragma: no cover
    main()
