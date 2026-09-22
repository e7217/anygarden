"""Configuration checks shared by API and automatic lifecycle dispatch."""

import re

PROVIDER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
PI_PROVIDER_REQUIRED = "Pi requires a valid explicit provider; configure Provider in agent settings before starting."


def pi_provider_error(engine: str, provider: str | None) -> str | None:
    if engine == "pi-cli" and (
        not isinstance(provider, str) or re.fullmatch(PROVIDER_PATTERN, provider) is None
    ):
        return PI_PROVIDER_REQUIRED
    return None
