"""Node-wide account settings stored in ``server_settings`` (#1058)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import ServerSetting

REGISTRATION_KEY = "registration"
# Open by default: a node that never touched the setting keeps the
# behaviour it had before #1058.
DEFAULT_REGISTRATION_OPEN = True

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


async def registration_open(db: AsyncSession) -> bool:
    value = (
        await db.execute(
            select(ServerSetting.value).where(ServerSetting.key == REGISTRATION_KEY)
        )
    ).scalar_one_or_none()
    if not isinstance(value, dict):
        return DEFAULT_REGISTRATION_OPEN
    return bool(value.get("open", DEFAULT_REGISTRATION_OPEN))


async def set_registration_open(db: AsyncSession, open_: bool) -> None:
    row = await db.get(ServerSetting, REGISTRATION_KEY)
    if row is None:
        db.add(ServerSetting(key=REGISTRATION_KEY, value={"open": open_}))
    else:
        row.value = {"open": open_}
